from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from pcap_feature_extractor import burst_features, collect_pcaps, load_pcap_packets

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
    parser.add_argument("--baseline-root", type=Path, required=True, help="基线 PCAP 根目录")
    parser.add_argument(
        "--candidate-root",
        type=Path,
        action="append",
        required=True,
        help="待比较的 PCAP 根目录（可多次指定）",
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
    labels = None
    if args.labels:
        labels = [label.strip() for label in args.labels.split(",") if label.strip()]
        if len(labels) != len(args.candidate_root):
            raise RuntimeError("labels 数量需与 candidate-root 数量一致。")
    else:
        labels = [path.name for path in args.candidate_root]

    baseline_values = compute_feature_values(
        args.baseline_root, group_level=args.group_level, suffix=args.pcap_suffix
    )
    results: dict[str, list[float]] = {}
    for label, candidate_root in zip(labels, args.candidate_root):
        candidate_values = compute_feature_values(
            candidate_root, group_level=args.group_level, suffix=args.pcap_suffix
        )
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
                "baseline_root": str(args.baseline_root),
                "candidates": {
                    label: {
                        "root": str(root),
                        "jsd": results[label],
                    }
                    for label, root in zip(labels, args.candidate_root)
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
