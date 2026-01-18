from __future__ import annotations

import argparse
import csv
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from frames import HEADER_STRUCT, FLAG_PADDING

PCAP_GLOBAL_HEADER_LEN = 24
PCAP_RECORD_HEADER_LEN = 16
DEFAULT_WINDOW_SIZE_SEC = 10
DEFAULT_SIZE_BINS = [300, 600, 900, 1200]


@dataclass
class Packet:
    ts: float
    src_ip: str
    src_port: int
    dst_ip: str
    dst_port: int
    direction: int
    payload_len: int
    total_len: int
    tcp_flags: int
    tcp_window: int
    payload: bytes




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 PCAP 提取特征并训练/评估 SVM 指纹模型",
    )
    parser.add_argument(
        "--pcap-root",
        type=Path,
        required=True,
        help="PCAP 根目录，默认结构: root/<group>/<label>/*.pcap 或 root/<label>/*.pcap",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("out/features.csv"),
        help="输出特征 CSV 路径",
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
    parser.add_argument("--test-size", type=float, default=0.3)
    parser.add_argument("--random-state", type=int, default=42)
    parser.add_argument("--kernel", type=str, default="rbf")
    parser.add_argument("--C", type=float, default=1.0)
    parser.add_argument("--gamma", type=str, default="scale")
    return parser.parse_args()


def load_pcap_packets(path: Path) -> list[Packet]:
    with path.open("rb") as handle:
        header = handle.read(PCAP_GLOBAL_HEADER_LEN)
        if len(header) < PCAP_GLOBAL_HEADER_LEN:
            raise RuntimeError(f"{path} 头部不完整")
        magic = struct.unpack("<I", header[:4])[0]
        if magic == 0xA1B2C3D4:
            endian = "<"
        elif magic == 0xD4C3B2A1:
            endian = ">"
        else:
            raise RuntimeError(f"{path} 不支持的 pcap 魔数")
        linktype = struct.unpack(endian + "I", header[20:24])[0]

        packets: list[Packet] = []
        while True:
            record_header = handle.read(PCAP_RECORD_HEADER_LEN)
            if len(record_header) < PCAP_RECORD_HEADER_LEN:
                break
            ts_sec, ts_usec, incl_len, _orig_len = struct.unpack(
                endian + "IIII", record_header
            )
            packet = handle.read(incl_len)
            parsed = parse_packet(packet, linktype)
            if parsed is None:
                continue
            src_ip, src_port, dst_ip, dst_port, payload, total_len, flags, window = parsed
            ts = ts_sec + ts_usec / 1_000_000
            packets.append(
                Packet(
                    ts=ts,
                    src_ip=src_ip,
                    src_port=src_port,
                    dst_ip=dst_ip,
                    dst_port=dst_port,
                    direction=0,
                    payload_len=len(payload),
                    total_len=total_len,
                    tcp_flags=flags,
                    tcp_window=window,
                    payload=payload,
                )
            )
        return assign_direction(packets)


def parse_packet(packet: bytes, linktype: int):
    if linktype == 1:
        if len(packet) < 14:
            return None
        eth_type = struct.unpack("!H", packet[12:14])[0]
        if eth_type != 0x0800:
            return None
        payload = packet[14:]
    elif linktype == 101:
        payload = packet
    else:
        return None

    if len(payload) < 20:
        return None
    ver_ihl = payload[0]
    version = ver_ihl >> 4
    if version != 4:
        return None
    ihl = (ver_ihl & 0x0F) * 4
    if len(payload) < ihl + 20:
        return None
    protocol = payload[9]
    if protocol != 6:
        return None
    total_len = struct.unpack("!H", payload[2:4])[0]
    src_ip = ".".join(str(b) for b in payload[12:16])
    dst_ip = ".".join(str(b) for b in payload[16:20])

    tcp = payload[ihl:total_len]
    if len(tcp) < 20:
        return None
    src_port, dst_port = struct.unpack("!HH", tcp[:4])
    data_offset = (tcp[12] >> 4) * 4
    flags = tcp[13]
    window = struct.unpack("!H", tcp[14:16])[0]
    if len(tcp) < data_offset:
        return None
    data = tcp[data_offset:]
    return src_ip, src_port, dst_ip, dst_port, data, total_len, flags, window


def assign_direction(packets: list[Packet]) -> list[Packet]:
    if not packets:
        return packets
    client_tuple = None
    for packet in packets:
        if packet.tcp_flags & 0x02 and not (packet.tcp_flags & 0x10):
            client_tuple = (packet.src_ip, packet.src_port)
            break
    if client_tuple is None:
        first = packets[0]
        client_tuple = (first.src_ip, first.src_port)
    for packet in packets:
        packet.direction = 1 if (packet.src_ip, packet.src_port) == client_tuple else -1
    return packets


def packet_sequences(packets: list[Packet], max_pkts: int) -> tuple[list[int], list[float]]:
    sizes = []
    times = []
    for pkt in packets[:max_pkts]:
        signed = pkt.payload_len if pkt.direction >= 0 else -pkt.payload_len
        sizes.append(signed)
        times.append(pkt.ts)
    if len(sizes) < max_pkts:
        sizes.extend([0] * (max_pkts - len(sizes)))
    deltas = []
    for i in range(1, min(len(times), max_pkts)):
        deltas.append((times[i] - times[i - 1]) * 1000.0)
    if len(deltas) < max_pkts - 1:
        deltas.extend([0.0] * (max_pkts - 1 - len(deltas)))
    return sizes, deltas


def burst_features(packets: list[Packet]) -> tuple[list[int], list[int], list[float]]:
    if not packets:
        return [], [], []
    counts: list[int] = []
    sizes: list[int] = []
    durations: list[float] = []
    current_dir = 1 if packets[0].direction >= 0 else -1
    burst_count = 0
    burst_size = 0
    burst_start = packets[0].ts
    for pkt in packets:
        direction = 1 if pkt.direction >= 0 else -1
        if direction != current_dir:
            counts.append(burst_count)
            sizes.append(burst_size)
            durations.append((pkt.ts - burst_start) * 1000.0)
            current_dir = direction
            burst_count = 0
            burst_size = 0
            burst_start = pkt.ts
        burst_count += 1
        burst_size += pkt.payload_len
    counts.append(burst_count)
    sizes.append(burst_size)
    durations.append((packets[-1].ts - burst_start) * 1000.0)
    return counts, sizes, durations


def stats(values: Iterable[float]) -> tuple[float, float, float, float, float, float, float]:
    values = list(values)
    if not values:
        return (0.0,) * 7
    arr = np.array(values, dtype=float)
    mean = float(arr.mean())
    var = float(arr.var())
    std = float(arr.std())
    if std == 0:
        skew = 0.0
        kurt = 0.0
    else:
        skew = float(((arr - mean) ** 3).mean() / (std**3))
        kurt = float(((arr - mean) ** 4).mean() / (std**4))
    return (
        mean,
        var,
        skew,
        kurt,
        float(arr.max()),
        float(arr.min()),
        float(np.median(arr)),
    )


def stats_short(values: Iterable[float]) -> tuple[float, float, float, float, float, float]:
    mean, var, skew, kurt, max_v, min_v, _median = stats(values)
    return mean, var, skew, kurt, max_v, min_v


def payload_entropy(payloads: list[bytes]) -> float:
    data = b"".join(payloads)
    if not data:
        return 0.0
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256)
    probs = counts / counts.sum()
    return float(-(probs * np.log2(probs + 1e-12)).sum())


def parse_frames(payloads: list[bytes]) -> tuple[int, int, int, int]:
    buffer = bytearray()
    padding_bytes = 0
    total_bytes = 0
    proto_ids = set()
    variant_ids = set()
    for payload in payloads:
        buffer.extend(payload)
        while True:
            if len(buffer) < HEADER_STRUCT.size:
                break
            header = buffer[: HEADER_STRUCT.size]
            (
                _session_id,
                _seq,
                _direction,
                _path_id,
                _window_id,
                proto_id,
                extra_len,
                _frag_id,
                _frag_total,
                payload_len,
            ) = HEADER_STRUCT.unpack(header)
            total_len = HEADER_STRUCT.size + extra_len + 1 + payload_len
            if len(buffer) < total_len:
                break
            variant_id = buffer[HEADER_STRUCT.size] if extra_len else 0
            flags_index = HEADER_STRUCT.size + extra_len
            flags = buffer[flags_index]
            proto_ids.add(proto_id)
            variant_ids.add(variant_id)
            total_bytes += payload_len
            if flags & FLAG_PADDING:
                padding_bytes += payload_len
            del buffer[:total_len]
    return padding_bytes, total_bytes, len(proto_ids), len(variant_ids)


def kl_divergence(sample_counts: np.ndarray, reference_counts: np.ndarray) -> float:
    sample = sample_counts / max(sample_counts.sum(), 1.0)
    ref = reference_counts / max(reference_counts.sum(), 1.0)
    return float((sample * np.log((sample + 1e-12) / (ref + 1e-12))).sum())


def size_distribution(payloads: list[int], bins: list[int]) -> np.ndarray:
    if not payloads:
        return np.zeros(len(bins) + 1)
    counts = np.zeros(len(bins) + 1)
    for size in payloads:
        placed = False
        for idx, limit in enumerate(bins):
            if size <= limit:
                counts[idx] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    return counts


def window_drift(packets: list[Packet], window_sec: float) -> float:
    if not packets:
        return 0.0
    start = packets[0].ts
    buckets: dict[int, list[int]] = {}
    for pkt in packets:
        bucket = int((pkt.ts - start) / max(window_sec, 1e-3))
        buckets.setdefault(bucket, []).append(pkt.payload_len)
    means = [np.mean(values) for values in buckets.values() if values]
    if len(means) < 2:
        return 0.0
    mean = float(np.mean(means))
    std = float(np.std(means))
    return 0.0 if mean == 0 else std / mean


def handshake_features(packets: list[Packet]) -> list[float]:
    syn = next((p for p in packets if p.tcp_flags & 0x02 and not (p.tcp_flags & 0x10)), None)
    syn_ack = next((p for p in packets if p.tcp_flags & 0x12 == 0x12), None)
    ack = next((p for p in packets if p.tcp_flags & 0x10 and not (p.tcp_flags & 0x02)), None)
    if not syn or not syn_ack or not ack:
        return [0.0] * 6
    return [
        syn.payload_len,
        syn_ack.payload_len,
        ack.payload_len,
        (syn_ack.ts - syn.ts) * 1000.0,
        (ack.ts - syn_ack.ts) * 1000.0,
        syn.tcp_window,
    ]


def build_feature_vector(
    packets: list[Packet],
    max_pkts: int,
    max_bursts: int,
    ref_distribution: np.ndarray | None,
    size_bins: list[int],
) -> list[float]:
    sizes, deltas = packet_sequences(packets, max_pkts)
    burst_counts, burst_sizes, burst_durations = burst_features(packets)
    burst_total = len(burst_counts)

    burst_counts = (burst_counts + [0] * max_bursts)[:max_bursts]
    burst_sizes = (burst_sizes + [0] * max_bursts)[:max_bursts]
    burst_durations = (burst_durations + [0.0] * max_bursts)[:max_bursts]

    size_stats = stats(sizes)
    delta_stats = stats_short(deltas)

    up_count = sum(1 for pkt in packets if pkt.direction >= 0)
    down_count = sum(1 for pkt in packets if pkt.direction < 0)
    total_pkts = max(up_count + down_count, 1)
    max_same_dir = 0
    current = 0
    last_dir = None
    for pkt in packets:
        direction = 1 if pkt.direction >= 0 else -1
        if direction == last_dir:
            current += 1
        else:
            current = 1
            last_dir = direction
        max_same_dir = max(max_same_dir, current)

    burst_gap_mean = 0.0
    if len(packets) > 1:
        gaps = [
            (packets[i].ts - packets[i - 1].ts) * 1000.0
            for i in range(1, len(packets))
        ]
        burst_gap_mean = float(np.mean(gaps))

    payloads = [pkt.payload for pkt in packets if pkt.payload_len > 0]
    padding_bytes, total_bytes, proto_count, variant_count = parse_frames(payloads)
    padding_ratio = 0.0 if total_bytes == 0 else padding_bytes / total_bytes

    size_dist = size_distribution([abs(s) for s in sizes if s > 0], size_bins)
    kl_div = 0.0
    if ref_distribution is not None:
        kl_div = kl_divergence(size_dist, ref_distribution)

    drift = window_drift(packets, DEFAULT_WINDOW_SIZE_SEC)
    entropy = payload_entropy(payloads)
    handshake = handshake_features(packets)

    features = []
    features.extend(sizes)
    features.extend(deltas)
    features.extend(burst_counts)
    features.extend(burst_sizes)
    features.extend(burst_durations)
    features.extend(size_stats)
    features.extend(delta_stats)
    features.extend(
        [
            up_count / total_pkts,
            down_count / total_pkts,
            max_same_dir,
            burst_total,
            float(np.mean(burst_counts)) if burst_counts else 0.0,
            max(burst_sizes) if burst_sizes else 0.0,
            burst_gap_mean,
        ]
    )
    features.extend([kl_div, drift, padding_ratio, proto_count, variant_count])
    features.extend([entropy])
    features.extend(handshake)
    return features


def collect_pcaps(root: Path, group_level: bool, suffix: str) -> list[tuple[Path, str, str]]:
    entries: list[tuple[Path, str, str]] = []
    if group_level:
        for group_dir in root.iterdir():
            if not group_dir.is_dir():
                continue
            for label_dir in group_dir.iterdir():
                if not label_dir.is_dir():
                    continue
                for pcap in label_dir.glob(f"*_{suffix}.pcap"):
                    entries.append((pcap, label_dir.name, group_dir.name))
    else:
        for label_dir in root.iterdir():
            if not label_dir.is_dir():
                continue
            for pcap in label_dir.glob(f"*_{suffix}.pcap"):
                entries.append((pcap, label_dir.name, "default"))
    return entries


def build_reference_distribution(
    root: Path, group_level: bool, bins: list[int], suffix: str
) -> np.ndarray:
    counts = np.zeros(len(bins) + 1)
    for pcap, _label, _group in collect_pcaps(root, group_level, suffix):
        packets = load_pcap_packets(pcap)
        sizes = [pkt.payload_len for pkt in packets if pkt.payload_len > 0]
        counts += size_distribution(sizes, bins)
    return counts


def main() -> None:
    args = parse_args()
    size_bins = DEFAULT_SIZE_BINS
    reference = None
    if args.kl_reference_root is not None:
        reference = build_reference_distribution(
            args.kl_reference_root, args.group_level, size_bins, args.pcap_suffix
        )

    rows = []
    feature_len = None
    for pcap, label, group in collect_pcaps(
        args.pcap_root, args.group_level, args.pcap_suffix
    ):
        packets = load_pcap_packets(pcap)
        features = build_feature_vector(
            packets,
            args.max_pkts,
            args.max_bursts,
            reference,
            size_bins,
        )
        if feature_len is None:
            feature_len = len(features)
        rows.append({"label": label, "group": group, **{f"f{i}": v for i, v in enumerate(features)}})

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    if feature_len is None:
        raise RuntimeError("未生成特征，请检查 PCAP 输入。")
    labels = [row["label"] for row in rows]
    feature_matrix = np.array([[row[f"f{i}"] for i in range(feature_len)] for row in rows])

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

    model = SVC(kernel=args.kernel, C=args.C, gamma=args.gamma)
    model.fit(x_train, y_train)
    preds = model.predict(x_test)

    print("SVM accuracy:", accuracy_score(y_test, preds))
    print(classification_report(y_test, preds))


if __name__ == "__main__":
    main()
