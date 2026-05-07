from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def configure_fonts() -> None:
    plt.rcParams["font.sans-serif"] = [
        "SimHei",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="绘制不同路径数量下最大连续分片占比 R_s 的箱线图（基于参考样本量做拟真 mock）"
    )
    parser.add_argument(
        "--reference-json",
        type=Path,
        default=Path("out/csmax_from_pcap.json"),
        help="参考 JSON，仅用于读取各组样本量。",
    )
    parser.add_argument(
        "--profile",
        choices=("mild", "medium", "strong"),
        default="strong",
        help="强化强度。",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20260414,
        help="随机种子。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/rs_boxplot_mock.png"),
        help="输出图片路径。",
    )
    parser.add_argument(
        "--save-json",
        type=Path,
        default=Path("out/rs_mock.json"),
        help="保存生成的数据。",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="图标题。",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=11,
        help="字体大小。",
    )
    return parser.parse_args()


def load_reference_counts(path: Path) -> dict[int, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {int(k): len(v) for k, v in payload.items()}


def save_json(path: Path, data: dict[int, np.ndarray]) -> None:
    payload = {str(k): [float(v) for v in arr.tolist()] for k, arr in data.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _gamma_by_target_median(lower: float, upper: float, target_median: float) -> float:
    eps = 1e-6
    target = min(max(target_median, lower + eps), upper - eps)
    ratio = (target - lower) / max(upper - lower, eps)
    ratio = min(max(ratio, eps), 1 - eps)
    return float(np.log(ratio) / np.log(0.5))


def generate_rs_mock(counts: dict[int, int], rng: np.random.Generator, profile: str) -> dict[int, np.ndarray]:
    # R_s 比 C_s,max 更强调“连续片段”，因此下降应更明显。
    target_medians = {
        "mild": {1: 0.998, 2: 0.66, 3: 0.52, 4: 0.42},
        "medium": {1: 0.998, 2: 0.58, 3: 0.43, 4: 0.34},
        "strong": {1: 0.998, 2: 0.50, 3: 0.36, 4: 0.28},
    }[profile]
    upper_bounds = {1: 1.0, 2: 0.86, 3: 0.74, 4: 0.64}

    grouped: dict[int, np.ndarray] = {}
    for path_count, n in sorted(counts.items()):
        if path_count == 1:
            values = np.clip(rng.normal(loc=0.998, scale=0.0018, size=n), 0.985, 1.0)
            grouped[path_count] = values
            continue

        lower = max(0.03, 1.0 / (path_count * 3.2))
        upper = upper_bounds.get(path_count, 0.6)
        gamma = _gamma_by_target_median(lower, upper, target_medians[path_count])

        q = (np.arange(n) + 0.5) / n
        shaped = lower + (upper - lower) * np.power(q, gamma)
        shaped += rng.normal(loc=0.0, scale=0.02, size=n)

        # 少量高尾和低尾，避免图形过于光滑
        high_n = int(n * 0.035)
        low_n = int(n * 0.06)
        if high_n > 0:
            idx = rng.choice(n, size=high_n, replace=False)
            shaped[idx] = rng.uniform(max(upper - 0.05, lower + 0.12), min(upper + 0.03, 0.92), size=high_n)
        if low_n > 0:
            idx = rng.choice(n, size=low_n, replace=False)
            shaped[idx] = rng.uniform(lower, min(lower + 0.05, upper - 0.08), size=low_n)

        rng.shuffle(shaped)
        grouped[path_count] = np.clip(shaped, 0.0, 1.0)

    return grouped


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    if not args.reference_json.exists():
        raise SystemExit(f"参考 JSON 不存在: {args.reference_json}")

    counts = load_reference_counts(args.reference_json)
    rng = np.random.default_rng(args.seed)
    grouped = generate_rs_mock(counts, rng, args.profile)
    save_json(args.save_json, grouped)

    for pc in sorted(grouped):
        arr = grouped[pc]
        print(
            f"[info] path_count={pc} n={len(arr)} "
            f"median={np.median(arr):.4f} q1={np.percentile(arr,25):.4f} q3={np.percentile(arr,75):.4f}"
        )

    path_counts = sorted(grouped.keys())
    colors = {
        1: "#DCEAF6",
        2: "#A8C8E6",
        3: "#6F9FCB",
        4: "#2F5D8A",
    }

    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    bp = ax.boxplot(
        [grouped[pc] for pc in path_counts],
        positions=path_counts,
        widths=0.55,
        patch_artist=True,
        showfliers=True,
        medianprops={"color": "#202020", "linewidth": 2.4},
        whiskerprops={"color": "#4a4a4a", "linewidth": 1.2},
        capprops={"color": "#4a4a4a", "linewidth": 1.2},
        boxprops={"edgecolor": "#4a4a4a", "linewidth": 1.1},
        flierprops={
            "marker": "o",
            "markersize": 3.0,
            "markerfacecolor": "none",
            "markeredgecolor": "#6a6a6a",
            "alpha": 0.9,
        },
    )
    for patch, pc in zip(bp["boxes"], path_counts, strict=True):
        patch.set_facecolor(colors.get(pc, "#A8C8E6"))

    ax.set_title(args.title, fontsize=args.font_size + 1)
    ax.set_xlabel("路径数量", fontsize=args.font_size)
    ax.set_ylabel(r"最大连续分片占比", fontsize=args.font_size)
    ax.set_xticks(path_counts)
    ax.set_ylim(0.0, 1.04)
    ax.set_yticks(np.arange(0.0, 1.01, 0.1))
    ax.grid(axis="y", linestyle="--", linewidth=0.8, alpha=0.35)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220)
    print(f"Saved boxplot to {args.output}")
    print(f"Saved data to {args.save_json}")


if __name__ == "__main__":
    main()
