from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


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
    parser = argparse.ArgumentParser(
        description="绘制完整会话视图与单路径局部观测下的分片序列示意对比图"
    )
    parser.add_argument("--path-count", type=int, default=4, choices=[2, 3, 4], help="路径数量")
    parser.add_argument("--fragment-count", type=int, default=84, help="会话分片总数")
    parser.add_argument(
        "--profile",
        choices=("mild", "medium", "strong"),
        default="strong",
        help="分散强度，越强表示路径切换越频繁、局部视图越碎片化。",
    )
    parser.add_argument("--seed", type=int, default=20260415, help="随机种子")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/session_view_comparison.png"),
        help="输出图片路径",
    )
    parser.add_argument(
        "--save-json",
        type=Path,
        default=Path("out/session_view_comparison.json"),
        help="保存示意数据",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="图标题",
    )
    parser.add_argument("--font-size", type=int, default=11, help="字体大小")
    return parser.parse_args()


def build_path_sequence(path_count: int, fragment_count: int, profile: str, seed: int) -> list[int]:
    rng = np.random.default_rng(seed)
    profile_batches = {
        "mild": np.array([2, 3, 4, 5, 6]),
        "medium": np.array([1, 2, 3, 4, 5]),
        "strong": np.array([1, 1, 2, 2, 3, 4]),
    }
    profile_probs = {
        "mild": np.array([0.08, 0.18, 0.28, 0.28, 0.18]),
        "medium": np.array([0.18, 0.28, 0.27, 0.18, 0.09]),
        "strong": np.array([0.24, 0.22, 0.21, 0.17, 0.10, 0.06]),
    }
    batches = profile_batches[profile]
    probs = profile_probs[profile]

    # 轻度偏置，让某条路径偶尔承担较多段，避免“完全均匀”显得假。
    base_weights = {
        2: np.array([0.55, 0.45]),
        3: np.array([0.38, 0.34, 0.28]),
        4: np.array([0.31, 0.27, 0.23, 0.19]),
    }[path_count].astype(float)

    sequence: list[int] = []
    current_path = int(rng.choice(path_count, p=base_weights / base_weights.sum()))
    while len(sequence) < fragment_count:
        batch_len = int(rng.choice(batches, p=probs / probs.sum()))
        batch_len = min(batch_len, fragment_count - len(sequence))
        sequence.extend([current_path] * batch_len)

        next_weights = base_weights.copy()
        next_weights[current_path] *= 0.35 if profile == "strong" else 0.45 if profile == "medium" else 0.6
        current_path = int(rng.choice(path_count, p=next_weights / next_weights.sum()))
    return sequence


def compute_runs(sequence: list[int], path_id: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for idx, owner in enumerate(sequence, start=1):
        if owner == path_id and start is None:
            start = idx
        elif owner != path_id and start is not None:
            runs.append((start, idx - start))
            start = None
    if start is not None:
        runs.append((start, len(sequence) + 1 - start))
    return runs


def save_payload(path: Path, sequence: list[int], path_count: int) -> None:
    payload = {
        "fragment_count": len(sequence),
        "path_count": path_count,
        "sequence_path_ids": [int(item) + 1 for item in sequence],
        "runs": {
            str(path_id + 1): [{"start": start, "length": length} for start, length in compute_runs(sequence, path_id)]
            for path_id in range(path_count)
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    sequence = build_path_sequence(args.path_count, args.fragment_count, args.profile, args.seed)
    save_payload(args.save_json, sequence, args.path_count)

    colors = {
        0: "#A8C8E6",
        1: "#6F9FCB",
        2: "#2F5D8A",
        3: "#163A63",
    }
    full_color = "#B8BDC3"

    labels = ["完整会话"] + [f"路径{i}" for i in range(1, args.path_count + 1)]
    y_positions = list(range(len(labels), 0, -1))
    row_height = 0.56

    fig, ax = plt.subplots(figsize=(10.0, 4.8))

    # 完整视图显示为一条连续带
    ax.broken_barh([(1, args.fragment_count)], (y_positions[0] - row_height / 2, row_height), facecolors=full_color)

    # 各路径仅显示本路径可见的连续片段
    for path_id in range(args.path_count):
        runs = compute_runs(sequence, path_id)
        bars = [(start, length) for start, length in runs]
        ax.broken_barh(
            bars,
            (y_positions[path_id + 1] - row_height / 2, row_height),
            facecolors=colors[path_id],
        )

    ax.set_title(args.title, fontsize=args.font_size + 1)
    ax.set_xlabel("分片序号", fontsize=args.font_size)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, fontsize=args.font_size)
    ax.set_xlim(1, args.fragment_count + 1)
    if args.fragment_count <= 60:
        tick_step = 5
    elif args.fragment_count <= 100:
        tick_step = 10
    else:
        tick_step = 20
    ax.set_xticks(np.arange(1, args.fragment_count + 1, tick_step))
    ax.grid(axis="x", linestyle="--", linewidth=0.7, alpha=0.28)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=220)
    print(f"Saved session view figure to {args.output}")
    print(f"Saved sequence data to {args.save_json}")


if __name__ == "__main__":
    main()
