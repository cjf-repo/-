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
    parser = argparse.ArgumentParser(description="绘制多路径/正常流量识别率对比柱状图")
    parser.add_argument(
        "--multipath",
        type=str,
        required=True,
        help="多路径识别率（逗号分隔，顺序: svm,rf,cnn,df,varcnn）",
    )
    parser.add_argument(
        "--normal",
        type=str,
        required=True,
        help="正常流量识别率（逗号分隔，顺序: svm,rf,cnn,df,varcnn）",
    )
    parser.add_argument(
        "--third",
        type=str,
        default=None,
        help="第三类识别率（逗号分隔，顺序: svm,rf,cnn,df,varcnn）",
    )
    parser.add_argument(
        "--third-label",
        type=str,
        default="第三类流量",
        help="第三类图例名称",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/accuracy_compare.png"),
        help="输出图片路径",
    )
    parser.add_argument("--title", type=str, default="不同攻击模型下多路径与正常流量的识别率对比")
    parser.add_argument(
        "--note",
        type=str,
        default="",
        help="标题下方注释",
    )
    parser.add_argument("--bar-width", type=float, default=0.35)
    parser.add_argument("--bar-gap", type=float, default=0, help="同组柱子间距")
    parser.add_argument("--font-size", type=int, default=11)
    parser.add_argument("--label-font-size", type=int, default=12)
    return parser.parse_args()


def parse_values(raw: str) -> list[float]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    if len(values) != 5:
        raise SystemExit("识别率输入必须为 5 个值（svm, rf, cnn, df, varcnn）。")
    return [float(value) for value in values]


def main() -> None:
    args = parse_args()
    multipath = parse_values(args.multipath)
    normal = parse_values(args.normal)
    third = parse_values(args.third) if args.third is not None else None
    labels = ["SVM", "RF", "CNN", "DF", "VarCNN"]

    configure_fonts()
    x = list(range(len(labels)))
    series_count = 2 if third is None else 3
    group_width = series_count * args.bar_width + (series_count - 1) * args.bar_gap
    start_offset = -group_width / 2 + args.bar_width / 2
    offsets = [start_offset + i * (args.bar_width + args.bar_gap) for i in range(series_count)]

    fig, ax = plt.subplots(figsize=(9, 5))
    bars_norm = ax.bar(
        [pos + offsets[0] for pos in x],
        normal,
        width=args.bar_width,
        color="#E45756",
        label="正常流量",
    )
    bars_multi = ax.bar(
        [pos + offsets[1] for pos in x],
        multipath,
        width=args.bar_width,
        color="#4C78A8",
        label="多路径流量",
    )
    bars_third = None
    if third is not None:
        bars_third = ax.bar(
            [pos + offsets[2] for pos in x],
            third,
            width=args.bar_width,
            color="#54A24B",
            label=args.third_label,
        )

    ax.set_title(args.title, fontsize=args.label_font_size)
    ax.text(
        0.5,
        1.02,
        args.note,
        transform=ax.transAxes,
        ha="center",
        va="bottom",
        fontsize=args.font_size - 1,
    )
    ax.set_xlabel("攻击模型", fontsize=args.label_font_size)
    ax.set_ylabel("识别率（%）", fontsize=args.label_font_size)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=args.font_size)
    ax.set_ylim(0, 100)
    ax.set_yticks(list(range(0, 101, 20)))
    ax.tick_params(axis="y", labelsize=args.font_size)
    ax.legend(fontsize=args.font_size)

    def add_labels(bars):
        for bar in bars:
            height = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                height + 1.0,
                f"{height:.1f}",
                ha="center",
                va="bottom",
                fontsize=args.font_size - 1,
            )

    add_labels(bars_norm)
    add_labels(bars_multi)
    if bars_third is not None:
        add_labels(bars_third)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved accuracy chart to {args.output}")


if __name__ == "__main__":
    main()
