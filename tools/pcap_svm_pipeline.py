from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

from pcap_feature_extractor import extract_features, load_features_npz, write_features


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 PCAP 提取特征并训练/评估 SVM 指纹模型",
    )
    parser.add_argument(
        "--pcap-root",
        type=Path,
        default=None,
        help="PCAP 根目录，默认结构: root/<group>/<label>/*.pcap 或 root/<label>/*.pcap",
    )
    parser.add_argument(
        "--features-npz",
        type=Path,
        default=None,
        help="已导出的 NPZ 特征文件路径（优先使用该文件训练）",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("out/features.npz"),
        help="输出特征文件路径",
    )
    parser.add_argument(
        "--output-format",
        choices=("csv", "jsonl", "npz"),
        default="npz",
        help="输出特征格式（csv/jsonl/npz）",
    )
    parser.add_argument(
        "--group-level",
        action="store_true",
        help="启用 group 目录结构 root/<group>/<label>/*.pcap",
    )
    parser.add_argument(
        "--pcap-suffix",
        type=str,
        default="entry",
        help=(
            "过滤节点 PCAP 文件后缀（如 entry/exit/middle_0/middle_1）。"
            "默认使用 entry，仅提取每次访问对应的入口节点流量。"
        ),
    )
    parser.add_argument(
        "--kl-reference-root",
        type=Path,
        default=None,
        help="用于 KL 散度的参考 PCAP 根目录（通常是无防护组）",
    )
    parser.add_argument("--max-pkts", type=int, default=500)
    parser.add_argument("--max-bursts", type=int, default=50)
    parser.add_argument("--min-pkts", type=int, default=1, help="丢弃包数量过少的样本。")
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--kernel", type=str, default="rbf")
    parser.add_argument("--C", type=float, default=1.0)
    parser.add_argument("--gamma", type=str, default="scale")
    parser.add_argument("--class-weight", type=str, default="balanced")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.features_npz is not None:
        feature_matrix, labels, _groups, _feature_names = load_features_npz(args.features_npz)
    else:
        if args.pcap_root is None:
            raise RuntimeError("请提供 --features-npz 或 --pcap-root。")
        rows, feature_len, skipped, _total_pcaps, bad_pcaps = extract_features(
            args.pcap_root,
            group_level=args.group_level,
            suffix=args.pcap_suffix,
            max_pkts=args.max_pkts,
            max_bursts=args.max_bursts,
            min_pkts=args.min_pkts,
            reference_root=args.kl_reference_root,
        )
        write_features(args.output_csv, rows, output_format=args.output_format)
        if skipped:
            print(f"Skipped {skipped} samples with < {args.min_pkts} packets.")
        if bad_pcaps:
            print("Skipped corrupt pcaps:")
            for msg in bad_pcaps[:10]:
                print(f"  - {msg}")
            if len(bad_pcaps) > 10:
                print(f"  ... and {len(bad_pcaps) - 10} more")
        labels = [row["label"] for row in rows]
        feature_matrix = np.array([[row[f"f{i}"] for i in range(feature_len)] for row in rows])

    label_counts: dict[str, int] = {}
    for label in labels:
        label_counts[label] = label_counts.get(label, 0) + 1
    keep_mask = [label_counts[label] >= 2 for label in labels]
    dropped_labels = sorted({label for label, count in label_counts.items() if count < 2})
    if dropped_labels:
        print(f"Skipped labels with < 2 samples: {', '.join(dropped_labels)}")
    if not all(keep_mask):
        labels = [label for label, keep in zip(labels, keep_mask) if keep]
        feature_matrix = feature_matrix[[idx for idx, keep in enumerate(keep_mask) if keep]]

    unique_labels = sorted(set(labels))
    if len(unique_labels) < 2:
        raise RuntimeError("分类类别少于 2 个，无法进行 SVM 训练。")
    if len(labels) < 2:
        raise RuntimeError("样本数量不足（<2），无法进行训练/测试划分。")

    test_count = max(1, int(round(len(labels) * args.test_size)))
    stratify_labels = labels
    if test_count < len(unique_labels):
        print(
            "Warning: 测试集样本数不足以覆盖所有类别，"
            "已自动关闭 stratify（可通过减小类别数或增大样本数修复）。"
        )
        stratify_labels = None

    x_train, x_test, y_train, y_test = train_test_split(
        feature_matrix,
        labels,
        test_size=args.test_size,
        random_state=args.random_state,
        stratify=stratify_labels,
    )

    scaler = StandardScaler()
    x_train = scaler.fit_transform(x_train)
    x_test = scaler.transform(x_test)

    class_weight = None if args.class_weight == "none" else args.class_weight
    model = SVC(kernel=args.kernel, C=args.C, gamma=args.gamma, class_weight=class_weight)
    model.fit(x_train, y_train)
    preds = model.predict(x_test)

    print("SVM accuracy:", accuracy_score(y_test, preds))
    print(classification_report(y_test, preds))


if __name__ == "__main__":
    main()
