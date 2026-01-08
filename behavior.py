from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, List, Tuple

from frames import Frame, FLAG_PADDING


@dataclass
class BehaviorParams:
    size_bins: List[int]
    padding_alpha: float
    jitter_ms: int


@dataclass
class BehaviorState:
    window_id: int
    real_bytes: int = 0
    padding_bytes: int = 0
    padding_budget: int = 0


class BehaviorShaper:
    def __init__(self, params: BehaviorParams) -> None:
        self.params = params
        self.state = BehaviorState(window_id=0)

    def start_window(self, window_id: int) -> None:
        self.state = BehaviorState(
            window_id=window_id,
            real_bytes=0,
            padding_bytes=0,
            padding_budget=0,
        )

    def shape_payload(self, data: bytes) -> List[bytes]:
        chunks: List[bytes] = []
        remaining = data
        while remaining:
            target = random.choice(self.params.size_bins)
            piece = remaining[:target]
            remaining = remaining[target:]
            chunks.append(piece)
        self.state.real_bytes += len(data)
        self.state.padding_budget = int(self.state.real_bytes * self.params.padding_alpha)
        return chunks

    def make_padding_frames(
        self,
        template_frame: Frame,
        max_frames: int = 3,
    ) -> List[Frame]:
        frames: List[Frame] = []
        if self.state.padding_bytes >= self.state.padding_budget:
            return frames
        remaining = self.state.padding_budget - self.state.padding_bytes
        for _ in range(max_frames):
            if remaining <= 0:
                break
            size = min(random.choice(self.params.size_bins), remaining)
            payload = random.randbytes(size)
            pad_frame = Frame(
                session_id=template_frame.session_id,
                seq=template_frame.seq,
                direction=template_frame.direction,
                path_id=template_frame.path_id,
                window_id=template_frame.window_id,
                proto_id=template_frame.proto_id,
                flags=template_frame.flags | FLAG_PADDING,
                frag_id=0,
                frag_total=1,
                payload=payload,
                extra_header=template_frame.extra_header,
            )
            frames.append(pad_frame)
            remaining -= size
            self.state.padding_bytes += size
        return frames
