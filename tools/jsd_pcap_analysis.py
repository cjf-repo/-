from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from pcap_feature_extractor import (
    burst_features,
    collect_pcaps,
    load_features_npz,
    load_pcap_packets,
)

FEATURE_NAMES = [
    "Packet Size",
    "IAT",
    "Burst Volume",
    "Burst Duration",
    "Throughput",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于 PCAP 计算特征分布的 JS 散度并生成雷达图",
    )
    parser.add_argument("--baseline-root", type=Path, default=None, help="基线 PCAP 根目录")
    parser.add_argument("--baseline-npz", type=Path, default=None, help="基线 NPZ 特征文件")
    parser.add_argument(
        "--candidate-root",
        type=Path,
        action="append",
        default=[],
        help="待比较的 PCAP 根目录（可多次指定）",
    )
    parser.add_argument(
        "--candidate-npz",
        type=Path,
        action="append",
        default=[],
        help="待比较的 NPZ 特征文件（可多次指定）",
    )
    parser.add_argument(
        "--labels",
        type=str,
        default=None,
        help="候选方案标签（逗号分隔，数量需与 --candidate-root 一致）",
    )
    parser.add_argument(
        "--pcap-suffix",
        type=str,
        default="entry",
        help="过滤节点 PCAP 文件后缀（如 entry/exit/middle_0/middle_1）",
    )
    parser.add_argument(
        "--group-level",
        action="store_true",
        help="启用 group 目录结构 root/<group>/<label>/*.pcap",
    )
    parser.add_argument("--bins", type=int, default=20, help="直方图分桶数")
    parser.add_argument("--npz-max-pkts", type=int, default=500, help="NPZ 特征中的包序列长度")
    parser.add_argument("--npz-max-bursts", type=int, default=50, help="NPZ 特征中的突发序列长度")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path("out/jsd_metrics.json"),
        help="输出 JS 散度 JSON 路径",
    )
    parser.add_argument(
        "--output-plot",
        type=Path,
        default=Path("out/jsd_radar.png"),
        help="输出雷达图路径",
    )
    return parser.parse_args()


def compute_feature_values(
    root: Path, *, group_level: bool, suffix: str
) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {name: [] for name in FEATURE_NAMES}
    for pcap, _label, _group in collect_pcaps(root, group_level, suffix):
        packets = load_pcap_packets(pcap)
        if not packets:
            continue
        payload_sizes = [abs(pkt.payload_len) for pkt in packets if pkt.payload_len > 0]
        if payload_sizes:
            values["Packet Size"].extend(payload_sizes)
        if len(packets) > 1:
            iats = [
                (packets[i].ts - packets[i - 1].ts) * 1000.0
                for i in range(1, len(packets))
            ]
            values["IAT"].extend(iats)
        burst_counts, burst_sizes, burst_durations = burst_features(packets)
        if burst_sizes:
            values["Burst Volume"].extend(burst_sizes)
        if burst_durations:
            values["Burst Duration"].extend(burst_durations)
        duration = max(packets[-1].ts - packets[0].ts, 1e-6)
        total_bytes = sum(payload_sizes)
        values["Throughput"].append(total_bytes / duration)
    return values


def compute_feature_values_from_npz(
    npz_path: Path,
    *,
    max_pkts: int,
    max_bursts: int,
) -> dict[str, list[float]]:
    features, _labels, _groups, _feature_names = load_features_npz(npz_path)
    values: dict[str, list[float]] = {name: [] for name in FEATURE_NAMES}

    if features.size == 0:
        return values

    sizes_end = max_pkts
    iat_end = sizes_end + max_pkts - 1
    burst_counts_end = iat_end + max_bursts
    burst_sizes_end = burst_counts_end + max_bursts
    burst_durations_end = burst_sizes_end + max_bursts

    if features.shape[1] < burst_durations_end:
        raise RuntimeError("NPZ 特征长度不足，请确认 max_pkts/max_bursts 与生成时一致。")

    sizes = features[:, :sizes_end]
    iats = features[:, sizes_end:iat_end]
    burst_sizes = features[:, burst_counts_end:burst_sizes_end]
    burst_durations = features[:, burst_sizes_end:burst_durations_end]

    values["Packet Size"] = [abs(v) for v in sizes.flatten() if v > 0]
    values["IAT"] = [v for v in iats.flatten() if v > 0]
    values["Burst Volume"] = [v for v in burst_sizes.flatten() if v > 0]
    values["Burst Duration"] = [v for v in burst_durations.flatten() if v > 0]

    for row_sizes, row_iats in zip(sizes, iats):
        total_bytes = float(np.sum(np.abs(row_sizes)))
        duration_ms = float(np.sum(row_iats))
        if duration_ms <= 0:
            continue
        values["Throughput"].append(total_bytes / (duration_ms / 1000.0))
    return values


def js_divergence(values_a: list[float], values_b: list[float], bins: int) -> float:
    if not values_a or not values_b:
        return 0.0
    combined = values_a + values_b
    min_v = min(combined)
    max_v = max(combined)
    if min_v == max_v:
        return 0.0
    hist_a, _ = np.histogram(values_a, bins=bins, range=(min_v, max_v), density=False)
    hist_b, _ = np.histogram(values_b, bins=bins, range=(min_v, max_v), density=False)
    p = hist_a / max(hist_a.sum(), 1.0)
    q = hist_b / max(hist_b.sum(), 1.0)
    m = 0.5 * (p + q)
    eps = 1e-12
    kl_pm = np.sum(p * np.log2((p + eps) / (m + eps)))
    kl_qm = np.sum(q * np.log2((q + eps) / (m + eps)))
    return float(0.5 * (kl_pm + kl_qm))


def radar_plot(
    metrics: dict[str, list[float]],
    labels: list[str],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    values = np.array([metrics[label] for label in labels], dtype=float)
    angles = np.linspace(0, 2 * np.pi, len(FEATURE_NAMES), endpoint=False)
    angles = np.concatenate([angles, angles[:1]])

    fig, ax = plt.subplots(figsize=(6.5, 6.5), subplot_kw={"polar": True})
    max_value = max(values.max(), 0.1)
    ax.set_ylim(0, min(max_value * 1.2, 1.0))
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(FEATURE_NAMES)
    ax.set_yticklabels([])

    colors = ["#d62728", "#1f77b4", "#7f7f7f", "#2ca02c"]
    for idx, label in enumerate(labels):
        data = np.concatenate([values[idx], values[idx][:1]])
        color = colors[idx % len(colors)]
        ax.plot(angles, data, color=color, linewidth=2, label=label)
        ax.fill(angles, data, color=color, alpha=0.12)

    ax.legend(loc="upper right", bbox_to_anchor=(1.2, 1.1))
    fig.tight_layout()
    fig.savefig(output_path, dpi=200)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    if args.baseline_root is None and args.baseline_npz is None:
        raise RuntimeError("请提供 --baseline-root 或 --baseline-npz。")
    if args.baseline_root is not None and args.baseline_npz is not None:
        raise RuntimeError("baseline 只能选择 root 或 npz 其中一种。")
    if not args.candidate_root and not args.candidate_npz:
        raise RuntimeError("请至少提供一个 --candidate-root 或 --candidate-npz。")
    if args.candidate_root and args.candidate_npz:
        raise RuntimeError("candidate 只能选择 root 或 npz 其中一种。")

    labels = None
    if args.labels:
        labels = [label.strip() for label in args.labels.split(",") if label.strip()]
        expected = len(args.candidate_root or args.candidate_npz)
        if len(labels) != expected:
            raise RuntimeError("labels 数量需与 candidate 数量一致。")
    else:
        sources = args.candidate_root or args.candidate_npz
        labels = [path.stem if isinstance(path, Path) else str(path) for path in sources]

    if args.baseline_root is not None:
        baseline_values = compute_feature_values(
            args.baseline_root, group_level=args.group_level, suffix=args.pcap_suffix
        )
        candidates = args.candidate_root
        candidate_values_fn = lambda root: compute_feature_values(
            root, group_level=args.group_level, suffix=args.pcap_suffix
        )
    else:
        baseline_values = compute_feature_values_from_npz(
            args.baseline_npz,
            max_pkts=args.npz_max_pkts,
            max_bursts=args.npz_max_bursts,
        )
        candidates = args.candidate_npz
        candidate_values_fn = lambda npz_path: compute_feature_values_from_npz(
            npz_path,
            max_pkts=args.npz_max_pkts,
            max_bursts=args.npz_max_bursts,
        )

    results: dict[str, list[float]] = {}
    for label, candidate in zip(labels, candidates):
        candidate_values = candidate_values_fn(candidate)
        jsd_values = [
            js_divergence(baseline_values[name], candidate_values[name], args.bins)
            for name in FEATURE_NAMES
        ]
        results[label] = jsd_values

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(
            {
                "features": FEATURE_NAMES,
                "baseline_root": str(args.baseline_root) if args.baseline_root else None,
                "baseline_npz": str(args.baseline_npz) if args.baseline_npz else None,
                "candidates": {
                    label: {
                        "root": str(root) if args.baseline_root else None,
                        "npz": str(root) if args.baseline_npz else None,
                        "jsd": results[label],
                    }
                    for label, root in zip(labels, candidates)
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    radar_plot(results, labels, args.output_plot)


if __name__ == "__main__":
    main()
