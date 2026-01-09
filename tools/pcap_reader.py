from __future__ import annotations

import argparse
import ipaddress
import struct
from dataclasses import dataclass

from frames import HEADER_STRUCT
from logger import setup_logger


LOGGER = setup_logger("pcap_reader")


@dataclass
class FrameSummary:
    session_id: int
    seq: int
    direction: int
    path_id: int
    window_id: int
    proto_id: int
    flags: int
    frag_id: int
    frag_total: int
    extra_len: int
    payload_len: int


class FrameTap:
    def __init__(self, label: str) -> None:
        self.label = label
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)
        while True:
            summary = self._try_parse_one()
            if summary is None:
                break
            LOGGER.info(
                "%s 帧: session=%s seq=%s dir=%s path=%s window=%s proto=%s flags=0x%02x frag=%s/%s extra=%s payload=%s",
                self.label,
                summary.session_id,
                summary.seq,
                summary.direction,
                summary.path_id,
                summary.window_id,
                summary.proto_id,
                summary.flags,
                summary.frag_id,
                summary.frag_total,
                summary.extra_len,
                summary.payload_len,
            )

    def _try_parse_one(self) -> FrameSummary | None:
        if len(self._buffer) < HEADER_STRUCT.size:
            return None
        header = self._buffer[: HEADER_STRUCT.size]
        (
            session_id,
            seq,
            direction,
            path_id,
            window_id,
            proto_id,
            extra_len,
            frag_id,
            frag_total,
            payload_len,
        ) = HEADER_STRUCT.unpack(header)
        total_len = HEADER_STRUCT.size + extra_len + 1 + payload_len
        if len(self._buffer) < total_len:
            return None
        flags_index = HEADER_STRUCT.size + extra_len
        flags = self._buffer[flags_index]
        del self._buffer[:total_len]
        return FrameSummary(
            session_id=session_id,
            seq=seq,
            direction=direction,
            path_id=path_id,
            window_id=window_id,
            proto_id=proto_id,
            flags=flags,
            frag_id=frag_id,
            frag_total=frag_total,
            extra_len=extra_len,
            payload_len=payload_len,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcap", required=True, help="pcap 文件路径（libpcap 格式）")
    parser.add_argument("--port", type=int, action="append", help="只解析匹配的端口，可多次传入")
    return parser.parse_args()


def parse_pcap(path: str, ports: set[int]) -> None:
    with open(path, "rb") as handle:
        header = handle.read(24)
        if len(header) < 24:
            raise RuntimeError("pcap 头部不完整")
        magic = struct.unpack("<I", header[:4])[0]
        if magic == 0xA1B2C3D4:
            endian = "<"
        elif magic == 0xD4C3B2A1:
            endian = ">"
        else:
            raise RuntimeError("不支持的 pcap 魔数")
        linktype = struct.unpack(endian + "I", header[20:24])[0]

        taps: dict[tuple[str, int, str, int], FrameTap] = {}

        while True:
            record_header = handle.read(16)
            if len(record_header) < 16:
                break
            ts_sec, ts_usec, incl_len, orig_len = struct.unpack(endian + "IIII", record_header)
            packet = handle.read(incl_len)
            payload = extract_tcp_payload(packet, linktype)
            if payload is None:
                continue
            src_ip, src_port, dst_ip, dst_port, data = payload
            if ports and src_port not in ports and dst_port not in ports:
                continue
            key = (src_ip, src_port, dst_ip, dst_port)
            if key not in taps:
                label = f"{src_ip}:{src_port} -> {dst_ip}:{dst_port}"
                taps[key] = FrameTap(label)
            taps[key].feed(data)


def extract_tcp_payload(packet: bytes, linktype: int) -> tuple[str, int, str, int, bytes] | None:
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
    src_ip = str(ipaddress.IPv4Address(payload[12:16]))
    dst_ip = str(ipaddress.IPv4Address(payload[16:20]))

    tcp = payload[ihl:total_len]
    if len(tcp) < 20:
        return None
    src_port, dst_port = struct.unpack("!HH", tcp[:4])
    data_offset = (tcp[12] >> 4) * 4
    if len(tcp) < data_offset:
        return None
    data = tcp[data_offset:]
    if not data:
        return None
    return src_ip, src_port, dst_ip, dst_port, data


def main() -> None:
    args = parse_args()
    ports = set(args.port or [])
    parse_pcap(args.pcap, ports)


if __name__ == "__main__":
    main()
