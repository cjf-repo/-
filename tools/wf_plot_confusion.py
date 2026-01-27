from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制双混淆矩阵热力图（正常/伪装对比）")
    parser.add_argument("--metrics-normal", type=Path, required=True, help="正常流量 metrics JSON")
    parser.add_argument("--metrics-obfuscated", type=Path, required=True, help="伪装流量 metrics JSON")
    parser.add_argument("--output", type=Path, default=Path("out/confusion_matrix_compare.png"))
    parser.add_argument("--title-normal", type=str, default="(a) 基准单路径场景（DS-Normal）")
    parser.add_argument("--title-obfuscated", type=str, default="(b) 多路径传输场景（DS-Multipath）")
    parser.add_argument("--normalize", action="store_true", help="按行归一化显示百分比")
    parser.add_argument("--max-labels", type=int, default=100, help="最多展示的类别数（过多会裁剪）")
    parser.add_argument("--font-size", type=int, default=10, help="坐标轴刻度字体大小")
    parser.add_argument("--cbar-font-size", type=int, default=30, help="颜色条刻度字体大小（单独控制）")
    parser.add_argument("--axis-label-size", type=int, default=50, help="x/y轴标签（Predicted/True）字体大小")
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
    matrix = load_confusion(args.metrics_json)
    
    # 归一化处理
    if args.normalize:
        normal_matrix = normalize_matrix(normal_matrix)
        obf_matrix = normalize_matrix(obf_matrix)
    normal_matrix = crop_matrix(normal_matrix, args.max_labels)
    obf_matrix = crop_matrix(obf_matrix, args.max_labels)

    # 裁剪矩阵到最大类别数
    size = matrix.shape[0]
    if size > args.max_labels:
        matrix = matrix[: args.max_labels, : args.max_labels]
        size = args.max_labels

    # 创建画布和子图
    fig, ax = plt.subplots(figsize=(max(6, size * 0.4), max(5, size * 0.4)))
    cmap = plt.get_cmap("Blues")
    im = ax.imshow(matrix, cmap=cmap)
    
    # 设置标题和坐标轴标签（使用单独的轴标签字体大小参数）
    ax.set_title(args.title, fontsize=30)
    ax.set_xlabel("Predicted Label", fontsize=args.axis_label_size)
    ax.set_ylabel("True Label", fontsize=args.axis_label_size)
    
    # 设置坐标轴刻度
    ax.set_xticks(range(size))
    ax.set_yticks(range(size))
    ax.tick_params(axis="both", which="major", labelsize=args.font_size)

    
    # 关键修改：设置颜色条刻度字体大小（使用新增的cbar-font-size参数）
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.ax.tick_params(labelsize=args.cbar_font_size)  # 改用单独的颜色条字体参数
    
    # 保存图片
    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved confusion matrix comparison to {args.output}")


if __name__ == "__main__":
    main()