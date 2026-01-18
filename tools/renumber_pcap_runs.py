from __future__ import annotations

import argparse
import re
from pathlib import Path


PCAP_NAME_RE = re.compile(r"^(?P<count>\\d+)_(?P<suffix>.+\\.pcap)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按 entry/middle/exit 对齐重编号 PCAP 文件。",
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="包含各 URL 子目录的根目录。",
    )
    parser.add_argument(
        "--start",
        type=int,
        default=31,
        help="新编号起始值（默认 31）。",
    )
    parser.add_argument(
        "--suffixes",
        default="entry,exit,middle_0,middle_1",
        help="需要对齐的后缀列表（逗号分隔，不含 .pcap）。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅打印重命名计划，不实际修改文件。",
    )
    return parser.parse_args()


def iter_label_dirs(root: Path) -> list[Path]:
    if not root.exists():
        raise SystemExit(f"目录不存在: {root}")
    return [p for p in root.iterdir() if p.is_dir()]


def collect_by_suffix(
    label_dir: Path,
    suffixes: list[str],
    *,
    min_index: int,
) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {suffix: [] for suffix in suffixes}
    for path in label_dir.glob("*.pcap"):
        match = PCAP_NAME_RE.match(path.name)
        if not match:
            continue
        count = int(match.group("count"))
        if count < min_index:
            continue
        suffix = match.group("suffix").removesuffix(".pcap")
        if suffix not in grouped:
            continue
        grouped[suffix].append(path)
    for suffix in grouped:
        grouped[suffix].sort(key=lambda p: int(PCAP_NAME_RE.match(p.name).group("count")))
    return grouped


def plan_renames(label_dir: Path, start: int, suffixes: list[str]) -> list[tuple[Path, Path]]:
    grouped = collect_by_suffix(label_dir, suffixes, min_index=start)
    max_len = max((len(files) for files in grouped.values()), default=0)
    renames: list[tuple[Path, Path]] = []
    for idx in range(max_len):
        new_index = start + idx
        for suffix in suffixes:
            files = grouped[suffix]
            if idx >= len(files):
                continue
            src = files[idx]
            dst = label_dir / f"{new_index}_{suffix}.pcap"
            renames.append((src, dst))
    return renames


def execute_renames(renames: list[tuple[Path, Path]], *, dry_run: bool) -> None:
    if dry_run:
        for src, dst in renames:
            print(f"{src} -> {dst}")
        return
    temp_pairs: list[tuple[Path, Path]] = []
    for src, dst in renames:
        temp = src.with_name(f".tmp_{src.name}")
        src.rename(temp)
        temp_pairs.append((temp, dst))
    for temp, dst in temp_pairs:
        temp.rename(dst)


def main() -> None:
    args = parse_args()
    suffixes = [item.strip() for item in args.suffixes.split(",") if item.strip()]
    if not suffixes:
        raise SystemExit("suffixes 不能为空。")
    for label_dir in iter_label_dirs(args.root):
        renames = plan_renames(label_dir, args.start, suffixes)
        execute_renames(renames, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
