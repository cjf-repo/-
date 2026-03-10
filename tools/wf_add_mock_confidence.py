from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为评估 JSON 添加模拟置信度（用于 ROC 示意图）")
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("out/wf_json_output"),
        help="模型评估 JSON 根目录",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已有 roc_demo 字段",
    )
    parser.add_argument(
        "--dense-points",
        type=int,
        default=0,
        help="在相邻点之间插入的点数（用于让曲线更平滑）",
    )
    return parser.parse_args()


def densify_curve(fpr: list[float], tpr: list[float], extra: int) -> tuple[list[float], list[float]]:
    if extra <= 0 or len(fpr) < 2:
        return fpr, tpr
    fpr_out: list[float] = []
    tpr_out: list[float] = []
    for i in range(len(fpr) - 1):
        x0, x1 = fpr[i], fpr[i + 1]
        y0, y1 = tpr[i], tpr[i + 1]
        fpr_out.append(x0)
        tpr_out.append(y0)
        for j in range(1, extra + 1):
            ratio = j / (extra + 1)
            fpr_out.append(x0 + (x1 - x0) * ratio)
            tpr_out.append(y0 + (y1 - y0) * ratio)
    fpr_out.append(fpr[-1])
    tpr_out.append(tpr[-1])
    return fpr_out, tpr_out


def normalize_model_key(raw: str) -> str:
    key = raw.strip().upper().replace("-", "").replace("_", "")
    if key in {"VARCNN", "VARCN"}:
        return "VARCNN"
    if key in {"CNN", "DF", "RF", "SVM"}:
        return key
    return key

# """

    # "DF":     [0.0, 0.12, 0.20, 0.28, 0.35, 0.45, 0.55, 0.65, 0.75, 0.88, 1.0],
    # "RF":     [0.0, 0.11, 0.18, 0.26, 0.33, 0.43, 0.53, 0.63, 0.73, 0.86, 1.0],
    # "CNN":    [0.0, 0.10, 0.17, 0.24, 0.31, 0.41, 0.51, 0.61, 0.71, 0.84, 1.0],
    # "VARCNN": [0.0, 0.09, 0.16, 0.22, 0.29, 0.39, 0.49, 0.59, 0.69, 0.82, 1.0],
    # "SVM":    [0.0, 0.08, 0.15, 0.20, 0.27, 0.37, 0.47, 0.57, 0.67, 0.82, 1.0],

# """


def build_mock_roc(model_key: str, *, dense_points: int) -> dict:
    fpr = [0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0]
    templates = {
    # DF (90.3%)：性能最优，TPR显著高于其他模型
    "DF": [0.0, 0.40, 0.68, 0.82, 0.86, 0.90, 0.92, 0.94, 0.95, 0.97, 1.0],
    # RF (89.3%)：次优，与DF保持明显间隔
    "RF": [0.0, 0.45, 0.63, 0.79, 0.83, 0.87, 0.89, 0.91, 0.92, 0.94, 1.0],
    # CNN (85.3%)：性能中等，与RF拉开差距
    "CNN": [0.0, 0.40, 0.58, 0.75, 0.79, 0.82, 0.85, 0.87, 0.89, 0.91, 1.0],
    # VarCNN (54.8%)：性能偏低，与CNN明显区分
    "VARCNN": [0.0, 0.35, 0.52, 0.68, 0.72, 0.75, 0.78, 0.80, 0.82, 0.85, 1.0],
    # SVM (50.6%)：性能最低，与VarCNN保持间隔
    "SVM": [0.0, 0.32, 0.50, 0.64, 0.69, 0.72, 0.74, 0.76, 0.78, 0.81, 1.0]
    }
    tpr = templates.get(model_key, templates["SVM"])
    fpr_dense, tpr_dense = densify_curve(fpr, tpr, dense_points)
    return {"fpr": fpr_dense, "tpr": tpr_dense, "note": "synthetic_roc_curve_for_visualization_only"}


def template_keys() -> set[str]:
    return {"VARCNN", "DF", "CNN", "RF", "SVM"}


def main() -> None:
    args = parse_args()
    json_files = sorted(args.input_root.glob("*/*.json"))
    if not json_files:
        raise SystemExit(f"未找到 JSON 文件: {args.input_root}")
    for path in json_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        metrics = data.get("metrics") or {}
        if "roc_demo" in data and not args.overwrite:
            continue
        raw_key = data.get("model") or path.parent.name
        model_key = normalize_model_key(str(raw_key))
        if model_key not in template_keys():
            name_lower = path.stem.lower()
            if "varcnn" in name_lower:
                model_key = "VARCNN"
            elif "df" in name_lower:
                model_key = "DF"
            elif "cnn" in name_lower:
                model_key = "CNN"
            elif "rf" in name_lower:
                model_key = "RF"
            elif "svm" in name_lower:
                model_key = "SVM"
        roc_demo = build_mock_roc(model_key, dense_points=args.dense_points)
        data["roc_demo"] = {
            **roc_demo,
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Updated {path}")


if __name__ == "__main__":
    main()
