from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt


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
    parser = argparse.ArgumentParser(description="绘制不同路径数量下攻击模型识别准确率变化趋势图")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/path_count_accuracy_trend.png"),
        help="输出图片路径",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="图标题",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=11,
        help="基础字体大小",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    # 表3-5数据
    path_counts = [1, 2, 3, 4]
    data = {
        "SVM": [50.6, 20.7, 17.2, 13.8],
        "RF": [89.3, 58.5, 48.6, 39.5],
        "CNN": [85.3, 50.6, 44.7, 40.2],
        "DF": [90.3, 55.7, 50.3, 42.4],
        "VarCNN": [94.8, 54.7, 44.9, 38.7],
    }

    colors = {
        "SVM": "#e15759",
        "RF": "#f28e2b",
        "CNN": "#4e79a7",
        "DF": "#59a14f",
        "VarCNN": "#b07aa1",
    }
    markers = {
        "SVM": "o",
        "RF": "s",
        "CNN": "D",
        "DF": "^",
        "VarCNN": "P",
    }

    fig, ax = plt.subplots(figsize=(8.8, 5.2))

    for model_name, values in data.items():
        ax.plot(
            path_counts,
            values,
            label=model_name,
            color=colors[model_name],
            marker=markers[model_name],
            linewidth=2,
            markersize=7,
        )

    ax.set_title(args.title, fontsize=args.font_size + 1)
    ax.set_xlabel("路径数量", fontsize=args.font_size)
    ax.set_ylabel("识别准确率（%）", fontsize=args.font_size)
    ax.set_xticks(path_counts)
    ax.set_xticklabels(["1（单路径）", "2", "3", "4"])
    ax.set_ylim(0, 100)
    ax.grid(True, axis="y", linestyle="--", alpha=0.35)
    ax.legend(frameon=False, ncol=3, loc="upper right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220)
    print(f"Saved trend chart to {args.output}")


if __name__ == "__main__":
    main()
