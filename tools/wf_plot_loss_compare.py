from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绘制训练损失对比曲线")
    parser.add_argument("--metrics-normal", type=Path, required=True, help="基准场景 metrics JSON")
    parser.add_argument("--metrics-obfuscated", type=Path, required=True, help="防御场景 metrics JSON")
    parser.add_argument("--output", type=Path, default=Path("out/loss_compare.png"))
    parser.add_argument("--title", type=str, default="图 3-8 DF模型训练过程中的损失函数(Loss)变化对比")
    parser.add_argument("--label-normal", type=str, default="基准场景 (Normal)")
    parser.add_argument("--label-obfuscated", type=str, default="防御场景 (Multipath)")
    parser.add_argument("--epochs", type=int, nargs="+", default=[10, 20, 50, 100, 150])
    parser.add_argument("--font-size", type=int, default=10)
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


def load_loss(path: Path) -> list[float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    metrics = payload.get("metrics", payload)
    loss_curve = metrics.get("training_loss")
    if not loss_curve:
        raise SystemExit(f"{path} 未包含 training_loss 字段，请先使用 wf_attack_runner 训练输出。")
    return [float(value) for value in loss_curve]


def sample_losses(loss_curve: list[float], epochs: list[int]) -> tuple[list[int], list[float]]:
    max_epoch = len(loss_curve)
    sampled_epochs = [epoch for epoch in epochs if 1 <= epoch <= max_epoch]
    sampled_loss = [loss_curve[epoch - 1] for epoch in sampled_epochs]
    return sampled_epochs, sampled_loss


def main() -> None:
    args = parse_args()
    configure_fonts()
    plt.rcParams.update({"font.size": args.font_size})

    normal_curve = load_loss(args.metrics_normal)
    obf_curve = load_loss(args.metrics_obfuscated)

    normal_epochs, normal_losses = sample_losses(normal_curve, args.epochs)
    obf_epochs, obf_losses = sample_losses(obf_curve, args.epochs)

    if not normal_epochs or not obf_epochs:
        raise SystemExit("指定的 Epoch 超出训练轮次，无法绘图。")

    fig, ax = plt.subplots(figsize=(8.5, 5))
    ax.plot(normal_epochs, normal_losses, marker="o", color="#1f77b4", label=args.label_normal)
    ax.plot(obf_epochs, obf_losses, marker="o", color="#2ca02c", label=args.label_obfuscated)
    ax.set_title(args.title, fontsize=args.font_size + 2)
    ax.set_xlabel("训练轮次 (Epoch)", fontsize=args.font_size + 1)
    ax.set_ylabel("Loss Value", fontsize=args.font_size + 1)
    ax.set_xticks(sorted(set(normal_epochs + obf_epochs)))
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend()

    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=200)
    print(f"Saved loss comparison to {args.output}")


if __name__ == "__main__":
    main()
