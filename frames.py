from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Optional, Tuple

# 帧结构定义与分片缓冲。

FLAG_PADDING = 1 << 0
FLAG_HANDSHAKE = 1 << 1
FLAG_FRAGMENT = 1 << 2
FLAG_REDUNDANT = 1 << 3
FLAG_ACK = 1 << 4

DIR_UP = 0
DIR_DOWN = 1

# 固定头部格式：便于帧解析
HEADER_STRUCT = struct.Struct("!I Q b B I H B H H I")


@dataclass
class Frame:
    # 会话 ID
    session_id: int
    # 序列号
    seq: int
    # 方向（上行/下行）
    direction: int
    # 路径 ID
    path_id: int
    # 窗口 ID
    window_id: int
    # 协议族 ID
    proto_id: int
    # 标志位
    flags: int
    # 分片编号
    frag_id: int
    # 分片总数
    frag_total: int
    # 有效载荷
    payload: bytes
    # 可扩展的额外头部
    extra_header: bytes = b""

    def encode(self) -> bytes:
        # 序列化为字节流
        extra_len = len(self.extra_header)
        payload_len = len(self.payload)
        # 头部 + 可变额外头 + flags + payload
        header = HEADER_STRUCT.pack(
            self.session_id,
            self.seq,
            self.direction,
            self.path_id,
            self.window_id,
            self.proto_id,
            extra_len,
            self.frag_id,
            self.frag_total,
            payload_len,
        )
        return header + self.extra_header + struct.pack("!B", self.flags) + self.payload

    @staticmethod
    async def read_from(reader: asyncio.StreamReader) -> Optional["Frame"]:
        # 从流中读取并解析一帧
        header_data = await reader.readexactly(HEADER_STRUCT.size)
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
        ) = HEADER_STRUCT.unpack(header_data)
        extra_header = b""
        if extra_len:
            extra_header = await reader.readexactly(extra_len)
        flags_data = await reader.readexactly(1)
        flags = struct.unpack("!B", flags_data)[0]
        payload = b""
        if payload_len:
            payload = await reader.readexactly(payload_len)
        return Frame(
            session_id=session_id,
            seq=seq,
            direction=direction,
            path_id=path_id,
            window_id=window_id,
            proto_id=proto_id,
            flags=flags,
            frag_id=frag_id,
            frag_total=frag_total,
            payload=payload,
            extra_header=extra_header,
        )


class FragmentBuffer:
    def __init__(self) -> None:
        # 缓存未完成分片
        self._buffers: dict[int, dict[int, bytes]] = {}
        self._totals: dict[int, int] = {}

    def add(self, frame: Frame) -> Tuple[bool, Optional[bytes]]:
        # 按 seq 收集分片，收齐后拼接
        if frame.seq not in self._buffers:
            self._buffers[frame.seq] = {}
            self._totals[frame.seq] = frame.frag_total
        self._buffers[frame.seq][frame.frag_id] = frame.payload
        if len(self._buffers[frame.seq]) >= self._totals[frame.seq]:
            parts = [self._buffers[frame.seq][idx] for idx in range(frame.frag_total)]
            payload = b"".join(parts)
            self._buffers.pop(frame.seq, None)
            self._totals.pop(frame.seq, None)
            return True, payload
        return False, None
