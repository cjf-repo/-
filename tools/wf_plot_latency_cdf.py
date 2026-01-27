from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制端到端时延分布 CDF 对比图")
    parser.add_argument("--latency-normal", type=Path, required=True, help="基准场景 latency_logs.jsonl")
    parser.add_argument("--latency-obfuscated", type=Path, required=True, help="防御场景 latency_logs.jsonl")
    parser.add_argument("--output", type=Path, default=Path("out/latency_cdf.png"))
    parser.add_argument("--title", type=str, default="图 3-7 不同传输模式下的端到端时延分布对比图 (CDF)")
    parser.add_argument("--label-normal", type=str, default="基准场景 (Normal)")
    parser.add_argument("--label-obfuscated", type=str, default="防御场景 (Multipath)")
    parser.add_argument("--font-size", type=int, default=10)
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


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    normal_lat = load_latencies(args.latency_normal)
    obf_lat = load_latencies(args.latency_obfuscated)

    normal_x, normal_cdf = to_cdf(normal_lat)
    obf_x, obf_cdf = to_cdf(obf_lat)

    fig, ax = plt.subplots(figsize=(8.5, 5))
    ax.plot(normal_x, normal_cdf, color="#1f77b4", label=args.label_normal)
    ax.plot(obf_x, obf_cdf, color="#2ca02c", label=args.label_obfuscated)
    ax.set_title(args.title, fontsize=args.font_size + 2)
    ax.set_xlabel("端到端时延 (ms)", fontsize=args.font_size + 1)
    ax.set_ylabel("CDF", fontsize=args.font_size + 1)
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend()

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved latency CDF to {args.output}")


if __name__ == "__main__":
    main()
