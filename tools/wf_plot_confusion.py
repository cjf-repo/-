from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.axes_grid1 import make_axes_locatable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制双混淆矩阵热力图（正常/伪装对比）")
    parser.add_argument("--metrics-normal", type=Path, required=True, help="正常流量 metrics JSON")
    parser.add_argument("--metrics-obfuscated", type=Path, required=True, help="伪装流量 metrics JSON")
    parser.add_argument("--output", type=Path, default=Path("out/confusion_matrix_compare.png"))
    parser.add_argument("--title-normal", type=str, default="(a) 基准单路径场景（DS-Normal）")
    parser.add_argument("--title-obfuscated", type=str, default="(b) 多路径传输场景（DS-Multipath）")
    parser.add_argument("--normalize", action="store_true", help="按行归一化显示百分比")
    parser.add_argument("--max-labels", type=int, default=50, help="最多展示的类别数（过多会裁剪）")
    parser.add_argument("--font-size", type=int, default=8)
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


def load_confusion(path: Path) -> tuple[np.ndarray, float | None]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", payload)
    matrix = metrics.get("confusion_matrix")
    if matrix is None:
        raise SystemExit("未在 JSON 中找到 confusion_matrix 字段。")
    accuracy = metrics.get("accuracy")
    return np.array(matrix, dtype=float), accuracy


def normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    row_sums = matrix.sum(axis=1, keepdims=True)
    return np.divide(matrix, row_sums, out=np.zeros_like(matrix), where=row_sums != 0)


def crop_matrix(matrix: np.ndarray, max_labels: int) -> np.ndarray:
    size = matrix.shape[0]
    if size > max_labels:
        return matrix[:max_labels, :max_labels]
    return matrix


def main() -> None:
    args = parse_args()
    configure_fonts()
    normal_matrix, normal_acc = load_confusion(args.metrics_normal)
    obf_matrix, obf_acc = load_confusion(args.metrics_obfuscated)
    if args.normalize:
        normal_matrix = normalize_matrix(normal_matrix)
        obf_matrix = normalize_matrix(obf_matrix)
    normal_matrix = crop_matrix(normal_matrix, args.max_labels)
    obf_matrix = crop_matrix(obf_matrix, args.max_labels)

    size = max(normal_matrix.shape[0], obf_matrix.shape[0])
    fig, axes = plt.subplots(1, 2, figsize=(max(10, size * 0.5), max(5, size * 0.4)))
    cmap = plt.get_cmap("Blues")

    def render(ax, matrix: np.ndarray, title: str, accuracy: float | None) -> None:
        im = ax.imshow(matrix, cmap=cmap, vmin=0.0, vmax=1.0 if args.normalize else None)
        subtitle = title
        if accuracy is not None:
            subtitle = f"{title}\n准确率 ~{accuracy * 100:.1f}%"
        ax.set_title(subtitle, fontsize=args.font_size + 2)
        ax.set_xlabel("预测网站类别索引 (Predicted Label Index)", fontsize=args.font_size + 1)
        ax.set_ylabel("真实网站类别索引 (True Label Index)", fontsize=args.font_size + 1)
        ax.set_xticks(range(matrix.shape[0]))
        ax.set_yticks(range(matrix.shape[0]))
        ax.tick_params(axis="both", which="major", labelsize=args.font_size)
        return im

    im_left = render(axes[0], normal_matrix, args.title_normal, normal_acc)
    im_right = render(axes[1], obf_matrix, args.title_obfuscated, obf_acc)
    axes[1].set_ylabel("")

    divider = make_axes_locatable(axes[1])
    cax = divider.append_axes("right", size="4%", pad=0.15)
    cbar = fig.colorbar(im_right, cax=cax)
    cbar.ax.tick_params(labelsize=args.font_size)
    cbar.set_label("预测概率 (Probability)" if args.normalize else "计数 (Count)", fontsize=args.font_size + 1)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved confusion matrix comparison to {args.output}")


if __name__ == "__main__":
    main()
