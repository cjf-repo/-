from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Sequence, Tuple

from frames import DIR_DOWN, DIR_UP, Frame, FLAG_HANDSHAKE

# 协议族配置：定义握手模板、变体参数与混淆方式。


@dataclass
class HandshakeSpec:
    # 握手方向
    direction: int
    # 握手帧大小
    size: int
    # 帧间延迟（毫秒）
    delay_ms: int


class ObfuscationMode(Enum):
    # 不混淆
    NONE = "none"
    # XOR 混淆
    XOR = "xor"
    # XOR 后反转
    XOR_REVERSE = "xor_reverse"


@dataclass
class ProtoVariant:
    # 变体 ID
    variant_id: int
    # 可选帧大小
    frame_sizes: Sequence[int]
    # 额外头长度范围
    extra_header_range: tuple[int, int]
    # 混淆模式
    obfuscation_mode: ObfuscationMode
    # 是否在额外头中添加 padding
    padding_header: bool


@dataclass
class ProtoFamily:
    # 协议族 ID
    family_id: int
    # 握手模板序列
    handshake: Sequence[HandshakeSpec]
    # 变体集合
    variants: Sequence[ProtoVariant]

    def pick_frame_size(self, variant: ProtoVariant) -> int:
        # 从变体帧大小中随机选择
        return random.choice(variant.frame_sizes)

    def pick_extra_header(self, variant: ProtoVariant) -> bytes:
        # 生成额外头（含变体 ID 与随机 padding）
        low, high = variant.extra_header_range
        length = random.randint(low, high)
        padding = random.randbytes(length) if length else b""
        if variant.padding_header:
            pad_len = random.randint(1, 4)
            padding = bytes([pad_len]) + random.randbytes(pad_len) + padding
        return bytes([variant.variant_id]) + padding

    def encode_payload(self, payload: bytes, variant: ProtoVariant) -> bytes:
        # 根据混淆模式对 payload 编码
        if not payload:
            return payload
        if variant.obfuscation_mode is ObfuscationMode.NONE:
            return payload
        key = random.randint(1, 255)
        obfuscated = bytes([b ^ key for b in payload])
        if variant.obfuscation_mode is ObfuscationMode.XOR_REVERSE:
            obfuscated = obfuscated[::-1]
        return bytes([key]) + obfuscated

    def decode_payload(self, payload: bytes, variant: ProtoVariant) -> bytes:
        # 还原混淆后的 payload
        if not payload:
            return payload
        if variant.obfuscation_mode is ObfuscationMode.NONE:
            return payload
        key = payload[0]
        data = payload[1:]
        if variant.obfuscation_mode is ObfuscationMode.XOR_REVERSE:
            data = data[::-1]
        return bytes([b ^ key for b in data])


def default_profiles() -> List[ProtoFamily]:
    # 默认协议族配置列表
    return [
        ProtoFamily(
            family_id=1,
            handshake=[
                HandshakeSpec(direction=DIR_UP, size=32, delay_ms=5),
                HandshakeSpec(direction=DIR_DOWN, size=24, delay_ms=10),
            ],
            variants=[
                ProtoVariant(
                    variant_id=0,
                    frame_sizes=[256, 384, 512],
                    extra_header_range=(0, 4),
                    obfuscation_mode=ObfuscationMode.NONE,
                    padding_header=False,
                ),
                ProtoVariant(
                    variant_id=1,
                    frame_sizes=[200, 300, 500],
                    extra_header_range=(1, 6),
                    obfuscation_mode=ObfuscationMode.NONE,
                    padding_header=True,
                ),
            ],
        ),
        ProtoFamily(
            family_id=2,
            handshake=[
                HandshakeSpec(direction=DIR_UP, size=48, delay_ms=3),
                HandshakeSpec(direction=DIR_UP, size=16, delay_ms=6),
            ],
            variants=[
                ProtoVariant(
                    variant_id=0,
                    frame_sizes=[300, 450, 600, 750],
                    extra_header_range=(2, 8),
                    obfuscation_mode=ObfuscationMode.XOR,
                    padding_header=False,
                ),
                ProtoVariant(
                    variant_id=1,
                    frame_sizes=[280, 420, 560],
                    extra_header_range=(4, 10),
                    obfuscation_mode=ObfuscationMode.XOR,
                    padding_header=True,
                ),
            ],
        ),
        ProtoFamily(
            family_id=3,
            handshake=[
                HandshakeSpec(direction=DIR_DOWN, size=40, delay_ms=8),
                HandshakeSpec(direction=DIR_UP, size=20, delay_ms=5),
            ],
            variants=[
                ProtoVariant(
                    variant_id=0,
                    frame_sizes=[200, 400, 800],
                    extra_header_range=(4, 12),
                    obfuscation_mode=ObfuscationMode.XOR_REVERSE,
                    padding_header=True,
                ),
                ProtoVariant(
                    variant_id=1,
                    frame_sizes=[240, 480, 720],
                    extra_header_range=(2, 12),
                    obfuscation_mode=ObfuscationMode.XOR_REVERSE,
                    padding_header=False,
                ),
            ],
        ),
    ]


def build_handshake_frames(
    session_id: int,
    window_id: int,
    family: ProtoFamily,
    path_id: int,
    variant_id: int,
) -> List[Tuple[Frame, int]]:
    frames: List[Tuple[Frame, int]] = []
    seq = 0
    variant = family.variants[variant_id % len(family.variants)]
    for spec in family.handshake:
        payload = random.randbytes(spec.size)
        frame = Frame(
            session_id=session_id,
            seq=seq,
            direction=spec.direction,
            path_id=path_id,
            window_id=window_id,
            proto_id=family.family_id,
            flags=FLAG_HANDSHAKE,
            frag_id=0,
            frag_total=1,
            payload=payload,
            extra_header=family.pick_extra_header(variant),
        )
        frames.append((frame, spec.delay_ms))
        seq += 1
    return frames
