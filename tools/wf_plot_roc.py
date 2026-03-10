from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import auc, roc_curve
from sklearn.preprocessing import label_binarize


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
    parser = argparse.ArgumentParser(description="绘制多模型 ROC 曲线（基于 JSON 中的 roc_demo）")
    parser.add_argument(
        "--input-json",
        action="append",
        default=[],
        help="指定 JSON 文件路径（可重复使用），用于绘制同一画布",
    )
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("out/wf_json_output"),
        help="模型评估 JSON 根目录（当未提供 --input-json 时生效）",
    )
    parser.add_argument(
        "--prefer-keyword",
        type=str,
        default="nor",
        help="优先匹配文件名关键词（如 nor/mutipath/ofu）",
    )
    parser.add_argument("--output", type=Path, default=Path("out/roc_compare.png"))
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--font-size", type=int, default=11)
    return parser.parse_args()


def load_model_files(input_root: Path, prefer_keyword: str) -> dict[str, Path]:
    models = {}
    for model_dir in sorted(input_root.iterdir()):
        if not model_dir.is_dir():
            continue
        json_files = sorted(model_dir.glob("*.json"))
        if not json_files:
            continue
        preferred = [p for p in json_files if prefer_keyword in p.name]
        selected = preferred[0] if preferred else json_files[0]
        models[model_dir.name.upper()] = selected
    return models


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    if args.input_json:
        model_files = {}
        for path_str in args.input_json:
            path = Path(path_str)
            parent_name = path.parent.name.upper()
            if parent_name in {"SVM", "RF", "CNN", "DF", "VARCNN"}:
                key = parent_name
            else:
                key = path.stem.upper()
            model_files[key] = path
    else:
        model_files = load_model_files(args.input_root, args.prefer_keyword)
    if not model_files:
        raise SystemExit(f"未找到模型 JSON: {args.input_root}")

    models: dict[str, np.ndarray] = {}
    y_true: np.ndarray | None = None
    for model_name, path in model_files.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        roc_demo = payload.get("roc_demo")
        if not roc_demo:
            raise SystemExit(f"{path} 缺少 roc_demo 字段，请先运行 wf_add_mock_confidence.py")
        if "fpr" in roc_demo and "tpr" in roc_demo:
            models[model_name] = np.array([roc_demo["fpr"], roc_demo["tpr"]], dtype=float)
            continue
        current_true = np.array(roc_demo["y_true"], dtype=int)
        current_score = np.array(roc_demo["y_score"], dtype=float)
        if y_true is None:
            y_true = current_true
        models[model_name] = current_score

    if y_true is not None:
        classes = np.unique(y_true)
        y_true_bin = label_binarize(y_true, classes=classes)
    else:
        y_true_bin = None

    colors = {
        "VARCNN": "#2ca25f",
        "DF": "#6baed6",
        "CNN": "#8da0aa",
        "RF": "#ff7f0e",
        "SVM": "#e15759",
    }
    display_names = {
        "VARCNN": "Var-CNN",
        "DF": "DF",
        "CNN": "CNN",
        "RF": "RF",
        "SVM": "SVM",
    }

    fig, ax = plt.subplots(figsize=(6, 4))
    for name, score in models.items():
        if score.ndim == 2 and score.shape[0] == 2:
            fpr, tpr = score[0], score[1]
            roc_auc = auc(fpr, tpr)
        else:
            fpr, tpr, _ = roc_curve(y_true_bin.ravel(), score.ravel())
            roc_auc = auc(fpr, tpr)
        fpr_smooth = np.linspace(0, 1, 200)
        tpr_smooth = np.interp(fpr_smooth, fpr, tpr)
        label = display_names.get(name, name)
        ax.plot(
            fpr_smooth,
            tpr_smooth,
            label=label,
            color=colors.get(name, "#1f77b4"),
            linewidth=1.8,
            zorder=3,
        )

    ax.plot([0, 1], [0, 1], linestyle="--", color="#b084dc", linewidth=2, label="Random Guess", zorder=1)
    ax.set_title(args.title, fontsize=args.font_size + 1)
    ax.set_xlabel("FPR", fontsize=args.font_size)
    ax.set_ylabel("TPR", fontsize=args.font_size)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.set_xticks(np.linspace(0, 1, 6))
    ax.set_yticks(np.linspace(0, 1, 6))
    ax.legend(frameon=False, loc="lower right")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved ROC plot to {args.output}")


if __name__ == "__main__":
    main()
