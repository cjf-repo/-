from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from frames import Frame, FLAG_PADDING


@dataclass
class BehaviorParams:
    size_bins: List[int]
    q_dist: List[float]
    padding_alpha: float
    jitter_ms: int
    rate_bytes_per_sec: int
    burst_size: int
    obfuscation_level: int
    enable_shaping: bool
    enable_padding: bool
    enable_pacing: bool
    enable_jitter: bool
    fixed_q_dist: List[float] | None = None


@dataclass
class BehaviorState:
    window_id: int
    real_bytes: int = 0
    padding_bytes: int = 0
    padding_budget: int = 0
    burst_count: int = 0
    last_ts: float = 0.0
    tokens: float = 0.0


class BehaviorShaper:
    def __init__(self, params: BehaviorParams, path_ids: List[int]) -> None:
        self.params = params
        self.params_by_path: Dict[int, BehaviorParams] = {
            path_id: params for path_id in path_ids
        }
        self.state = BehaviorState(window_id=0)
        self.path_states: Dict[int, BehaviorState] = {
            path_id: BehaviorState(window_id=0) for path_id in path_ids
        }
        self.q_dist_by_path: Dict[int, List[float]] = {
            path_id: params.q_dist[:] for path_id in path_ids
        }

    def start_window(self, window_id: int) -> None:
        self.state = BehaviorState(
            window_id=window_id,
            real_bytes=0,
            padding_bytes=0,
            padding_budget=0,
            burst_count=0,
            last_ts=0.0,
            tokens=0.0,
        )
        for path_id, state in self.path_states.items():
            self.path_states[path_id] = BehaviorState(
                window_id=window_id,
                real_bytes=0,
                padding_bytes=0,
                padding_budget=0,
                burst_count=0,
                last_ts=0.0,
                tokens=0.0,
            )

    def set_params(self, path_id: int, params: BehaviorParams) -> None:
        self.params_by_path[path_id] = params
        if path_id not in self.path_states:
            self.path_states[path_id] = BehaviorState(window_id=self.state.window_id)
        if path_id not in self.q_dist_by_path:
            self.q_dist_by_path[path_id] = params.q_dist[:]

    def update_q_dist(self, path_id: int, drift: float, seed: int | None = None) -> None:
        params = self.params_by_path[path_id]
        base = params.fixed_q_dist or params.q_dist
        rng = random.Random(seed)
        jittered = []
        for prob in base:
            jittered.append(max(0.01, prob + rng.uniform(-drift, drift)))
        total = sum(jittered)
        self.q_dist_by_path[path_id] = [prob / total for prob in jittered]

    def sample_target_len(self, path_id: int) -> int:
        params = self.params_by_path[path_id]
        probs = self.q_dist_by_path.get(path_id, params.q_dist)
        return random.choices(params.size_bins, weights=probs, k=1)[0]

    def note_real_bytes(self, path_id: int, size: int) -> None:
        state = self.path_states[path_id]
        state.real_bytes += size
        state.padding_budget = int(
            state.real_bytes * self.params_by_path[path_id].padding_alpha
        )

    def update_burst(self, path_id: int) -> bool:
        state = self.path_states[path_id]
        state.burst_count += 1
        if state.burst_count >= self.params_by_path[path_id].burst_size:
            state.burst_count = 0
            return True
        return False

    async def pace(self, path_id: int, size: int) -> None:
        state = self.path_states[path_id]
        if not self.params_by_path[path_id].enable_pacing:
            return
        current = state.last_ts or 0.0
        if current == 0.0:
            state.last_ts = asyncio.get_event_loop().time()
            state.tokens = 0.0
            current = state.last_ts
        now = asyncio.get_event_loop().time()
        elapsed = max(0.0, now - current)
        state.last_ts = now
        rate = self.params_by_path[path_id].rate_bytes_per_sec
        state.tokens += elapsed * rate
        if state.tokens < size:
            needed = (size - state.tokens) / max(rate, 1)
            await asyncio.sleep(needed)
            state.tokens = 0.0
        else:
            state.tokens -= size

    def make_padding_frames(
        self,
        template_frame: Frame,
        max_frames: int = 3,
    ) -> List[Frame]:
        frames: List[Frame] = []
        state = self.path_states[template_frame.path_id]
        if not self.params_by_path[template_frame.path_id].enable_padding:
            return frames
        # padding 预算用尽则停止
        if state.padding_bytes >= state.padding_budget:
            return frames
        remaining = state.padding_budget - state.padding_bytes
        for _ in range(max_frames):
            if remaining <= 0:
                break
            size = min(self.sample_target_len(template_frame.path_id), remaining)
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
            state.padding_bytes += size
        return frames
