from __future__ import annotations

import argparse
import json
from pathlib import Path

from wf_attack_runner import (
    load_dataset,
    stratified_split,
    train_deep,
    train_rf,
    train_svm,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量运行多模型网站指纹攻击并汇总准确率")
    parser.add_argument("--features-npz", type=Path, required=True, help="特征 NPZ 文件")
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--output-json", type=Path, default=Path("out/wf_metrics_all.json"))
    parser.add_argument("--epochs", type=int, default=20, help="深度模型训练轮数")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--dropout", type=float, default=0.5)
    parser.add_argument("--n-estimators", type=int, default=200)
    parser.add_argument("--max-depth", type=int, default=0, help="0 表示不限制深度")
    parser.add_argument("--kernel", type=str, default="rbf")
    parser.add_argument("--C", type=float, default=1.0)
    parser.add_argument("--gamma", type=str, default="scale")
    parser.add_argument("--class-weight", type=str, default="balanced")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = load_dataset(args.features_npz)
    x_train, x_test, y_train, y_test = stratified_split(
        dataset.features, dataset.labels, args.test_size, args.random_state
    )
    results: dict[str, dict[str, object]] = {}
    results["svm"] = train_svm(x_train, x_test, y_train, y_test, args)
    results["rf"] = train_rf(x_train, x_test, y_train, y_test, args)
    for model_name in ("cnn", "df", "varcnn"):
        results[model_name] = train_deep(
            x_train,
            x_test,
            y_train,
            y_test,
            model_name,
            dataset.label_encoder,
            args,
        )
    summary = {
        "models": results,
        "accuracy": {name: metrics["accuracy"] for name, metrics in results.items()},
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary["accuracy"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
