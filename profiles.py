from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Sequence

from frames import DIR_DOWN, DIR_UP, Frame, FLAG_HANDSHAKE


@dataclass
class HandshakeSpec:
    direction: int
    size: int
    delay_ms: int


@dataclass
class ProtoProfile:
    proto_id: int
    handshake: Sequence[HandshakeSpec]
    frame_sizes: Sequence[int]
    extra_header_range: tuple[int, int]

    def pick_frame_size(self) -> int:
        return random.choice(self.frame_sizes)

    def pick_extra_header(self) -> bytes:
        low, high = self.extra_header_range
        length = random.randint(low, high)
        return random.randbytes(length) if length else b""


def default_profiles() -> List[ProtoProfile]:
    return [
        ProtoProfile(
            proto_id=1,
            handshake=[
                HandshakeSpec(direction=DIR_UP, size=32, delay_ms=5),
                HandshakeSpec(direction=DIR_DOWN, size=24, delay_ms=10),
            ],
            frame_sizes=[256, 384, 512],
            extra_header_range=(0, 4),
        ),
        ProtoProfile(
            proto_id=2,
            handshake=[
                HandshakeSpec(direction=DIR_UP, size=48, delay_ms=3),
                HandshakeSpec(direction=DIR_UP, size=16, delay_ms=6),
            ],
            frame_sizes=[300, 450, 600, 750],
            extra_header_range=(2, 8),
        ),
        ProtoProfile(
            proto_id=3,
            handshake=[
                HandshakeSpec(direction=DIR_DOWN, size=40, delay_ms=8),
                HandshakeSpec(direction=DIR_UP, size=20, delay_ms=5),
            ],
            frame_sizes=[200, 400, 800],
            extra_header_range=(4, 12),
        ),
    ]


def build_handshake_frames(
    session_id: int,
    window_id: int,
    profile: ProtoProfile,
    path_id: int,
) -> List[Frame]:
    frames: List[Frame] = []
    seq = 0
    for spec in profile.handshake:
        payload = random.randbytes(spec.size)
        frame = Frame(
            session_id=session_id,
            seq=seq,
            direction=spec.direction,
            path_id=path_id,
            window_id=window_id,
            proto_id=profile.proto_id,
            flags=FLAG_HANDSHAKE,
            frag_id=0,
            frag_total=1,
            payload=payload,
            extra_header=profile.pick_extra_header(),
        )
        frames.append(frame)
        seq += 1
    return frames
