from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


PCAP_NAME_RE = re.compile(r"^(?P<count>\d+)_(?P<rest>.+\.pcap)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="合并两次抓包目录，把同一 URL 的 pcaps 合并到同一子目录并重排编号。",
    )
    parser.add_argument(
        "--src-a",
        type=Path,
        required=True,
        help="已有的 pcap 运行目录（作为合并目标的基准）。",
    )
    parser.add_argument(
        "--src-b",
        type=Path,
        required=True,
        help="需要合并进 src-a 的 pcap 运行目录。",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="合并输出目录（默认直接合并到 src-a）。",
    )
    parser.add_argument(
        "--remove-empty",
        action="store_true",
        help="合并完成后删除空目录（src-b 及其子目录）。",
    )
    return parser.parse_args()


def iter_label_dirs(root: Path) -> list[Path]:
    if not root.exists():
        raise SystemExit(f"目录不存在: {root}")
    return [p for p in root.iterdir() if p.is_dir()]


def max_existing_index(label_dir: Path) -> int:
    max_index = 0
    for path in label_dir.glob("*.pcap"):
        match = PCAP_NAME_RE.match(path.name)
        if not match:
            continue
        max_index = max(max_index, int(match.group("count")))
    return max_index


def merge_label_dir(src_dir: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    next_index = max_existing_index(dest_dir) + 1
    files = sorted(src_dir.glob("*.pcap"))
    for path in files:
        match = PCAP_NAME_RE.match(path.name)
        if match is None:
            target = dest_dir / path.name
            if target.exists():
                target = dest_dir / f"{next_index}_{path.name}"
                next_index += 1
            shutil.move(str(path), str(target))
            continue
        rest = match.group("rest")
        target = dest_dir / f"{next_index}_{rest}"
        next_index += 1
        shutil.move(str(path), str(target))


def main() -> None:
    args = parse_args()
    src_a = args.src_a.resolve()
    src_b = args.src_b.resolve()
    dest = args.dest.resolve() if args.dest else src_a

    if dest != src_a:
        dest.mkdir(parents=True, exist_ok=True)

    for label_dir in iter_label_dirs(src_a):
        target = dest / label_dir.name
        if dest != src_a:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(label_dir, target)

    for label_dir in iter_label_dirs(src_b):
        target = dest / label_dir.name
        merge_label_dir(label_dir, target)

    if args.remove_empty:
        for label_dir in iter_label_dirs(src_b):
            if not any(label_dir.iterdir()):
                label_dir.rmdir()
        if not any(src_b.iterdir()):
            src_b.rmdir()


if __name__ == "__main__":
    main()
