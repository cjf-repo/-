from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import gaussian_filter1d


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制端到端时延分布 CDF 对比图")
    parser.add_argument("--latency-normal", type=Path, required=True, help="基准场景 latency_logs.jsonl")
    parser.add_argument("--latency-obfuscated", type=Path, required=True, help="防御场景 latency_logs.jsonl")
    parser.add_argument("--output", type=Path, default=Path("out/latency_cdf.png"))
    parser.add_argument("--title", type=str, default="图 3-7 不同传输模式下的端到端时延分布对比图 (CDF)")
    parser.add_argument("--label-normal", type=str, default="基准场景 (Normal)")
    parser.add_argument("--label-obfuscated", type=str, default="防御场景 (Multipath)")
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

    normal_lat = load_latencies(args.latency_normal)
    obf_lat = load_latencies(args.latency_obfuscated)

    normal_lat = apply_filter(
        normal_lat,
        max_latency_ms=args.max_latency_ms,
        clip_percentile=args.clip_percentile,
    )
    obf_lat = apply_filter(
        obf_lat,
        max_latency_ms=args.max_latency_ms,
        clip_percentile=args.clip_percentile,
    )
    if not normal_lat or not obf_lat:
        raise SystemExit("过滤后样本为空，请调整 --max-latency-ms 或 --clip-percentile。")

    normal_x, normal_cdf = to_cdf(normal_lat)
    obf_x, obf_cdf = to_cdf(obf_lat)

    if args.smooth_sigma and args.smooth_sigma > 0:
        normal_cdf = gaussian_filter1d(normal_cdf, sigma=args.smooth_sigma)
        obf_cdf = gaussian_filter1d(obf_cdf, sigma=args.smooth_sigma)

    normal_mean = float(np.mean(normal_lat))
    obf_mean = float(np.mean(obf_lat))
    normal_p90 = float(np.percentile(normal_lat, 90))
    obf_p90 = float(np.percentile(obf_lat, 90))

    fig, ax = plt.subplots(figsize=(8.5, 5))
    ax.plot(normal_x, normal_cdf, color="#1f77b4", linewidth=2, label=args.label_normal)
    ax.plot(obf_x, obf_cdf, color="#2ca02c", linewidth=2, label=args.label_obfuscated)
    ax.set_title(args.title, fontsize=args.font_size + 2, pad=10)
    ax.set_xlabel("端到端时延 (ms)", fontsize=args.font_size + 1)
    ax.set_ylabel("CDF", fontsize=args.font_size + 1)
    ax.set_yticks(np.linspace(0, 1.0, 6))
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    ax.legend(loc="lower right")

    # 关键统计值参考线
    ref_style = dict(linestyle="--", linewidth=1.2, alpha=0.9)
    ax.axvline(normal_mean, color="#aec7e8", **ref_style)
    ax.axvline(normal_p90, color="#aec7e8", **ref_style)
    ax.axvline(obf_mean, color="#98df8a", **ref_style)
    ax.axvline(obf_p90, color="#98df8a", **ref_style)

    # 标注文字：靠近各自的参考线
    def annotate_at(x: float, y_frac: float, text: str, color: str) -> None:
        y = y_frac  # CDF 的 y 轴范围固定为 0~1
        xmin, xmax = ax.get_xlim()
        offset = 0.01 * (xmax - xmin)
        if x + offset <= xmax:
            ax.annotate(
                text,
                xy=(x, y),
                xytext=(6, 0),
                textcoords="offset points",
                ha="left",
                va="center",
                color=color,
                fontsize=args.font_size,
            )
        else:
            ax.annotate(
                text,
                xy=(x, y),
                xytext=(-6, 0),
                textcoords="offset points",
                ha="right",
                va="center",
                color=color,
                fontsize=args.font_size,
            )

    annotate_at(normal_mean, 0.88, f"基准平均时延：{normal_mean:.0f} ms", "#1f77b4")
    annotate_at(normal_p90, 0.80, f"基准 90% 分位：{normal_p90:.0f} ms", "#1f77b4")
    annotate_at(obf_mean, 0.72, f"防御平均时延：{obf_mean:.0f} ms", "#2ca02c")
    annotate_at(obf_p90, 0.64, f"防御 90% 分位：{obf_p90:.0f} ms", "#2ca02c")

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved latency CDF to {args.output}")


if __name__ == "__main__":
    main()
