from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from pcap_feature_extractor import load_pcap_packets

# 从单次请求对应的 pcap 文件估计端到端延迟（TTFB 或完成时间）。


@dataclass
class PcapEntry:
    path: Path
    label: str
    group: str
    count: Optional[int]
    suffix: str


PCAP_NAME_RE = re.compile(r"^(?P<count>\d+)[_.](?P<suffix>.+)\.pcap$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从单次请求的 pcap 提取延迟并输出 JSONL")
    parser.add_argument("--pcap-root", type=Path, required=True, help="PCAP 根目录")
    parser.add_argument(
        "--pcap-suffix",
        type=str,
        default="entry",
        help="仅处理指定节点后缀（entry/exit/middle_0 等）",
    )
    parser.add_argument(
        "--group-level",
        action="store_true",
        help="启用 group 目录结构 root/<group>/<label>/*.pcap",
    )
    parser.add_argument(
        "--mode",
        choices=("ttfb", "complete"),
        default="ttfb",
        help="延迟计算方式：ttfb=首个下行包减首个上行包，complete=最后下行包减首个上行包",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("out/latency_logs.jsonl"),
        help="输出 JSONL 路径",
    )
    return parser.parse_args()


def iter_pcaps(root: Path, group_level: bool) -> Iterable[PcapEntry]:
    if group_level:
        for group_dir in root.iterdir():
            if not group_dir.is_dir():
                continue
            for label_dir in group_dir.iterdir():
                if not label_dir.is_dir():
                    continue
                yield from _iter_label_dir(label_dir, group_dir.name)
    else:
        for label_dir in root.iterdir():
            if label_dir.is_dir():
                yield from _iter_label_dir(label_dir, "default")
        for path in root.glob("*.pcap"):
            entry = parse_pcap_name(path)
            if entry is None:
                continue
            yield PcapEntry(path=path, label=root.name, group="default", count=entry[0], suffix=entry[1])


def _iter_label_dir(label_dir: Path, group: str) -> Iterable[PcapEntry]:
    for path in label_dir.glob("*.pcap"):
        entry = parse_pcap_name(path)
        if entry is None:
            continue
        count, suffix = entry
        yield PcapEntry(path=path, label=label_dir.name, group=group, count=count, suffix=suffix)


def parse_pcap_name(path: Path) -> Optional[tuple[int, str]]:
    match = PCAP_NAME_RE.match(path.name)
    if not match:
        return None
    return int(match.group("count")), match.group("suffix")


def estimate_latency_ms(path: Path, mode: str) -> tuple[bool, float]:
    packets = load_pcap_packets(path)
    if not packets:
        return False, 0.0
    up_times = [pkt.ts for pkt in packets if pkt.payload_len > 0 and pkt.direction >= 0]
    if not up_times:
        return False, 0.0
    t_up = min(up_times)
    down_times = [pkt.ts for pkt in packets if pkt.payload_len > 0 and pkt.direction < 0 and pkt.ts >= t_up]
    if not down_times:
        return False, 0.0
    t_down = min(down_times) if mode == "ttfb" else max(down_times)
    return True, (t_down - t_up) * 1000.0


def main() -> None:
    args = parse_args()
    entries = [e for e in iter_pcaps(args.pcap_root, args.group_level) if e.suffix == args.pcap_suffix]
    if not entries:
        raise SystemExit(f"未找到后缀为 {args.pcap_suffix} 的 pcap 文件。")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    seq = 1
    with args.output.open("w", encoding="utf-8") as handle:
        for entry in sorted(entries, key=lambda e: (e.group, e.label, e.count or 0, e.path.name)):
            ok, latency_ms = estimate_latency_ms(entry.path, args.mode)
            record = {
                "seq": seq,
                "ok": ok,
                "latency_ms": latency_ms,
                "label": entry.label,
                "group": entry.group,
                "count": entry.count,
                "pcap": str(entry.path),
                "mode": args.mode,
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            seq += 1


if __name__ == "__main__":
    main()
