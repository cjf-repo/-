from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import LabelEncoder, StandardScaler
from sklearn.svm import SVC

from pcap_feature_extractor import load_features_npz


@dataclass
class Dataset:
    features: np.ndarray
    labels: list[str]
    label_encoder: LabelEncoder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一的网站指纹攻击训练/评估入口")
    parser.add_argument("--features-npz", type=Path, required=True, help="特征 NPZ 文件")
    parser.add_argument(
        "--model",
        choices=("svm", "rf", "cnn", "df", "varcnn"),
        default="svm",
        help="攻击模型类型",
    )
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--output-json", type=Path, default=Path("out/wf_metrics.json"))
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


def filter_labels(features: np.ndarray, labels: list[str]) -> tuple[np.ndarray, list[str]]:
    label_counts: dict[str, int] = {}
    for label in labels:
        label_counts[label] = label_counts.get(label, 0) + 1
    keep_mask = [label_counts[label] >= 2 for label in labels]
    dropped_labels = sorted({label for label, count in label_counts.items() if count < 2})
    if dropped_labels:
        print(f"Skipped labels with < 2 samples: {', '.join(dropped_labels)}")
    if not all(keep_mask):
        labels = [label for label, keep in zip(labels, keep_mask) if keep]
        features = features[[idx for idx, keep in enumerate(keep_mask) if keep]]
    return features, labels


def load_dataset(npz_path: Path) -> Dataset:
    features, labels, _groups, _feature_names = load_features_npz(npz_path)
    features, labels = filter_labels(features, labels)
    encoder = LabelEncoder()
    encoder.fit(labels)
    return Dataset(features=features, labels=labels, label_encoder=encoder)


def stratified_split(
    features: np.ndarray,
    labels: list[str],
    test_size: float,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    unique_labels = sorted(set(labels))
    if len(unique_labels) < 2:
        raise RuntimeError("分类类别少于 2 个，无法训练/评估。")
    if len(labels) < 2:
        raise RuntimeError("样本数量不足（<2），无法训练/测试划分。")
    test_count = max(1, int(round(len(labels) * test_size)))
    stratify_labels: list[str] | None = labels
    if test_count < len(unique_labels):
        print(
            "Warning: 测试集样本数不足以覆盖所有类别，"
            "已自动关闭 stratify（可通过减小类别数或增大样本数修复）。"
        )
        stratify_labels = None
    return train_test_split(
        features,
        labels,
        test_size=test_size,
        random_state=random_state,
        stratify=stratify_labels,
    )


def summarize_metrics(y_true: list[str], y_pred: list[str]) -> dict[str, object]:
    return {
        "accuracy": accuracy_score(y_true, y_pred),
        "precision_macro": precision_score(y_true, y_pred, average="macro", zero_division=0),
        "recall_macro": recall_score(y_true, y_pred, average="macro", zero_division=0),
        "f1_macro": f1_score(y_true, y_pred, average="macro", zero_division=0),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
        "classification_report": classification_report(y_true, y_pred, zero_division=0),
    }


def train_svm(
    x_train: np.ndarray,
    x_test: np.ndarray,
    y_train: list[str],
    y_test: list[str],
    args: argparse.Namespace,
) -> dict[str, object]:
    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_test = scaler.transform(x_test)
    class_weight = None if args.class_weight == "none" else args.class_weight
    model = SVC(kernel=args.kernel, C=args.C, gamma=args.gamma, class_weight=class_weight)
    model.fit(x_train, y_train)
    preds = model.predict(x_test)
    return summarize_metrics(y_test, preds)


def train_rf(
    x_train: np.ndarray,
    x_test: np.ndarray,
    y_train: list[str],
    y_test: list[str],
    args: argparse.Namespace,
) -> dict[str, object]:
    max_depth = None if args.max_depth <= 0 else args.max_depth
    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=max_depth,
        random_state=args.random_state,
        n_jobs=-1,
        class_weight=None if args.class_weight == "none" else args.class_weight,
    )
    model.fit(x_train, y_train)
    preds = model.predict(x_test)
    return summarize_metrics(y_test, preds)


def build_deep_model(model_type: str, input_shape: tuple[int, int], num_classes: int, args: argparse.Namespace):
    import tensorflow as tf
    from tensorflow import keras

    inputs = keras.Input(shape=input_shape)
    if model_type == "cnn":
        x = keras.layers.Conv1D(64, 7, activation="relu", padding="same")(inputs)
        x = keras.layers.MaxPooling1D(2)(x)
        x = keras.layers.Conv1D(128, 5, activation="relu", padding="same")(x)
        x = keras.layers.MaxPooling1D(2)(x)
    elif model_type == "df":
        x = keras.layers.Conv1D(32, 5, activation="relu", padding="same")(inputs)
        x = keras.layers.Conv1D(64, 5, activation="relu", padding="same")(x)
        x = keras.layers.MaxPooling1D(2)(x)
        x = keras.layers.Conv1D(128, 5, activation="relu", padding="same")(x)
        x = keras.layers.MaxPooling1D(2)(x)
    elif model_type == "varcnn":
        x = keras.layers.Conv1D(64, 8, dilation_rate=1, activation="relu", padding="same")(inputs)
        x = keras.layers.Conv1D(64, 24, dilation_rate=2, activation="relu", padding="same")(x)
        x = keras.layers.Conv1D(64, 72, dilation_rate=4, activation="relu", padding="same")(x)
    else:
        raise ValueError(f"Unsupported deep model: {model_type}")
    x = keras.layers.GlobalAveragePooling1D()(x)
    x = keras.layers.Dropout(args.dropout)(x)
    outputs = keras.layers.Dense(num_classes, activation="softmax")(x)
    model = keras.Model(inputs=inputs, outputs=outputs)
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=args.learning_rate),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def train_deep(
    x_train: np.ndarray,
    x_test: np.ndarray,
    y_train: list[str],
    y_test: list[str],
    model_type: str,
    encoder: LabelEncoder,
    args: argparse.Namespace,
) -> dict[str, object]:
    import tensorflow as tf

    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_test = scaler.transform(x_test)
    x_train = x_train[..., np.newaxis]
    x_test = x_test[..., np.newaxis]
    y_train_enc = encoder.transform(y_train)
    y_test_enc = encoder.transform(y_test)

    tf.random.set_seed(args.random_state)
    model = build_deep_model(model_type, x_train.shape[1:], len(encoder.classes_), args)
    history = model.fit(
        x_train,
        y_train_enc,
        epochs=args.epochs,
        batch_size=args.batch_size,
        verbose=2,
    )
    preds = model.predict(x_test, verbose=0)
    pred_labels = encoder.inverse_transform(np.argmax(preds, axis=1))
    metrics = summarize_metrics(y_test, pred_labels)
    metrics["training_loss"] = [float(value) for value in history.history.get("loss", [])]
    return metrics


def main() -> None:
    args = parse_args()
    dataset = load_dataset(args.features_npz)
    x_train, x_test, y_train, y_test = stratified_split(
        dataset.features, dataset.labels, args.test_size, args.random_state
    )
    if args.model == "svm":
        metrics = train_svm(x_train, x_test, y_train, y_test, args)
    elif args.model == "rf":
        metrics = train_rf(x_train, x_test, y_train, y_test, args)
    else:
        metrics = train_deep(
            x_train,
            x_test,
            y_train,
            y_test,
            args.model,
            dataset.label_encoder,
            args,
        )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    payload = {"model": args.model, "metrics": metrics}
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
