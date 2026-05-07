from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter1d


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制端到端时延分布 CDF 对比图")
    parser.add_argument("--latency-normal", type=Path, default=None, help="基准场景 latency_logs.jsonl")
    parser.add_argument("--latency-obfuscated", type=Path, default=None, help="防御场景 latency_logs.jsonl")
    parser.add_argument(
        "--latency-files",
        type=Path,
        nargs="+",
        default=None,
        help="通用输入：2~4 个 latency_logs.jsonl 文件，用于多线对比。",
    )
    parser.add_argument(
        "--labels",
        type=str,
        nargs="+",
        default=None,
        help="与 --latency-files 一一对应的图例标签。",
    )
    parser.add_argument("--output", type=Path, default=Path("out/latency_cdf.png"))
    parser.add_argument("--title", type=str, default="不同传输模式下的端到端时延分布对比图 (CDF)")
    parser.add_argument("--label-normal", type=str, default="基准场景 (Normal)")
    parser.add_argument("--label-obfuscated", type=str, default="防御场景 (Obfuscation)")
    parser.add_argument("--font-size", type=int, default=10)
    parser.add_argument(
        "--max-latency-ms",
        type=float,
        default=None,
        help="过滤超过该阈值的延迟（毫秒）。",
    )
    parser.add_argument(
        "--clip-percentile",
        type=float,
        default=None,
        help="按分位数截断（例如 99 表示只保留 <= P99）。",
    )
    parser.add_argument(
        "--smooth-sigma",
        type=float,
        default=1.2,
        help="CDF 曲线高斯平滑参数。",
    )
    return parser.parse_args()


def configure_fonts() -> None:
    plt.rcParams["font.sans-serif"] = [
        "SimHei",
        "Noto Sans CJK SC",
        "Microsoft YaHei",
        "Arial Unicode MS",
        "DejaVu Sans",
    ]
    plt.rcParams["axes.unicode_minus"] = False


def load_latencies(path: Path) -> list[float]:
    if not path.exists():
        raise SystemExit(f"未找到延迟日志文件: {path}")
    latencies: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("ok"):
            latencies.append(float(record.get("latency_ms", 0.0)))
    if not latencies:
        raise SystemExit(f"{path} 未包含有效的 latency_ms 记录。")
    return latencies


def to_cdf(values: list[float]) -> tuple[np.ndarray, np.ndarray]:
    sorted_vals = np.sort(np.array(values, dtype=float))
    cdf = np.arange(1, len(sorted_vals) + 1) / len(sorted_vals)
    return sorted_vals, cdf


def apply_filter(values: list[float], *, max_latency_ms: float | None, clip_percentile: float | None) -> list[float]:
    filtered = values
    if clip_percentile is not None:
        if not (0 < clip_percentile <= 100):
            raise SystemExit("--clip-percentile 需在 (0, 100] 范围内")
        threshold = float(np.percentile(filtered, clip_percentile))
        filtered = [v for v in filtered if v <= threshold]
    if max_latency_ms is not None:
        filtered = [v for v in filtered if v <= max_latency_ms]
    return filtered


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    if args.latency_files:
        if not (2 <= len(args.latency_files) <= 4):
            raise SystemExit("--latency-files 需要提供 2~4 个文件。")
        paths = args.latency_files
        if args.labels and len(args.labels) != len(paths):
            raise SystemExit("--labels 数量必须与 --latency-files 一致。")
        labels = args.labels or [f"场景{i + 1}" for i in range(len(paths))]
    else:
        if args.latency_normal is None or args.latency_obfuscated is None:
            raise SystemExit("请提供 --latency-files（2~4个），或同时提供 --latency-normal 与 --latency-obfuscated。")
        paths = [args.latency_normal, args.latency_obfuscated]
        labels = [args.label_normal, args.label_obfuscated]

    palette = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728"]
    series: list[tuple[str, np.ndarray, np.ndarray, float, float, str]] = []
    for idx, (path, label) in enumerate(zip(paths, labels, strict=True)):
        latencies = load_latencies(path)
        latencies = apply_filter(
            latencies,
            max_latency_ms=args.max_latency_ms,
            clip_percentile=args.clip_percentile,
        )
        if not latencies:
            raise SystemExit(f"{path} 过滤后样本为空，请调整 --max-latency-ms 或 --clip-percentile。")
        x, cdf = to_cdf(latencies)
        if args.smooth_sigma and args.smooth_sigma > 0:
            cdf = gaussian_filter1d(cdf, sigma=args.smooth_sigma)
        mean_val = float(np.mean(latencies))
        p90_val = float(np.percentile(latencies, 90))
        color = palette[idx % len(palette)]
        series.append((label, x, cdf, mean_val, p90_val, color))

    fig, ax = plt.subplots(figsize=(8.5, 5))
    for label, x, cdf, mean_val, _p90, color in series:
        legend_label = f"{label} (均值 {mean_val:.1f} ms)"
        ax.plot(x, cdf, color=color, linewidth=2, label=legend_label)
    ax.set_title(args.title, fontsize=args.font_size + 2, pad=10)
    ax.set_xlabel("端到端时延 (ms)", fontsize=args.font_size + 1)
    ax.set_ylabel("CDF", fontsize=args.font_size + 1)
    ax.set_yticks(np.linspace(0, 1.0, 6))
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.legend(loc="lower right")

    # 多组时额外输出统计摘要到控制台，避免图面拥挤。
    if len(series) > 2:
        print("统计摘要（mean/p90, ms）：")
        for label, _, _, mean_val, p90_val, _ in series:
            print(f"  - {label}: mean={mean_val:.2f}, p90={p90_val:.2f}")

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved latency CDF to {args.output}")


if __name__ == "__main__":
    main()
