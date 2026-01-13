from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from frames import Frame, FLAG_PADDING

# 行为层：负责对真实流量做整形、节流、抖动与填充。


@dataclass
class BehaviorParams:
    # 目标长度候选集合（用于采样分片长度）
    size_bins: List[int]
    # 目标长度采样概率分布
    q_dist: List[float]
    # padding 预算系数（真实字节 * alpha）
    padding_alpha: float
    # 抖动时间（毫秒）
    jitter_ms: int
    # 限速速率（字节/秒）
    rate_bytes_per_sec: int
    # 触发 padding 的 burst 门限
    burst_size: int
    # 混淆等级（影响参数漂移幅度）
    obfuscation_level: int
    # 是否启用整形
    enable_shaping: bool
    # 是否启用 padding
    enable_padding: bool
    # 是否启用 pacing
    enable_pacing: bool
    # 是否启用 jitter
    enable_jitter: bool
    # 固定分布：用于关闭自适应时锁定分布
    fixed_q_dist: List[float] | None = None


@dataclass
class BehaviorState:
    # 当前窗口编号
    window_id: int
    # 当前窗口内累计真实字节数
    real_bytes: int = 0
    # 当前窗口内累计 padding 字节数
    padding_bytes: int = 0
    # 当前窗口内可用 padding 预算
    padding_budget: int = 0
    # 连续 burst 计数
    burst_count: int = 0
    # pacing 上一次时间戳
    last_ts: float = 0.0
    # pacing 令牌桶
    tokens: float = 0.0


class BehaviorShaper:
    def __init__(self, params: BehaviorParams, path_ids: List[int]) -> None:
        # 初始参数：用于创建每条路径的基线配置
        self.params = params
        # 每条路径的参数（可被策略更新）
        self.params_by_path: Dict[int, BehaviorParams] = {
            path_id: params for path_id in path_ids
        }
        # 全局窗口状态
        self.state = BehaviorState(window_id=0)
        # 每条路径的状态
        self.path_states: Dict[int, BehaviorState] = {
            path_id: BehaviorState(window_id=0) for path_id in path_ids
        }
        # 每条路径的采样分布（可逐窗漂移）
        self.q_dist_by_path: Dict[int, List[float]] = {
            path_id: params.q_dist[:] for path_id in path_ids
        }

    def start_window(self, window_id: int) -> None:
        # 进入新窗口时重置全局统计
        self.state = BehaviorState(
            window_id=window_id,
            real_bytes=0,
            padding_bytes=0,
            padding_budget=0,
            burst_count=0,
            last_ts=0.0,
            tokens=0.0,
        )
        # 进入新窗口时重置每条路径统计
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
        # 更新指定路径的参数（策略输出）
        self.params_by_path[path_id] = params
        # 首次出现该路径时初始化状态
        if path_id not in self.path_states:
            self.path_states[path_id] = BehaviorState(window_id=self.state.window_id)
        # 首次出现该路径时初始化分布
        if path_id not in self.q_dist_by_path:
            self.q_dist_by_path[path_id] = params.q_dist[:]

    def update_q_dist(self, path_id: int, drift: float, seed: int | None = None) -> None:
        # 使用固定分布或当前分布作为基线
        params = self.params_by_path[path_id]
        base = params.fixed_q_dist or params.q_dist
        # 基于种子构造可复现的漂移
        rng = random.Random(seed)
        jittered = []
        for prob in base:
            # 对每个概率加噪声并限制下界
            jittered.append(max(0.01, prob + rng.uniform(-drift, drift)))
        # 归一化为概率分布
        total = sum(jittered)
        self.q_dist_by_path[path_id] = [prob / total for prob in jittered]

    def sample_target_len(self, path_id: int) -> int:
        # 从分布中采样目标长度
        params = self.params_by_path[path_id]
        probs = self.q_dist_by_path.get(path_id, params.q_dist)
        return random.choices(params.size_bins, weights=probs, k=1)[0]

    def note_real_bytes(self, path_id: int, size: int) -> None:
        # 记录真实字节并更新 padding 预算
        state = self.path_states[path_id]
        state.real_bytes += size
        state.padding_budget = int(
            state.real_bytes * self.params_by_path[path_id].padding_alpha
        )

    def update_burst(self, path_id: int) -> bool:
        # 计数 burst，达到阈值则触发 padding
        state = self.path_states[path_id]
        state.burst_count += 1
        if state.burst_count >= self.params_by_path[path_id].burst_size:
            state.burst_count = 0
            return True
        return False

    async def pace(self, path_id: int, size: int) -> None:
        # pacing：根据速率令牌桶进行限速
        state = self.path_states[path_id]
        if not self.params_by_path[path_id].enable_pacing:
            return
        current = state.last_ts or 0.0
        if current == 0.0:
            # 首次 pacing 初始化时间戳与令牌
            state.last_ts = asyncio.get_event_loop().time()
            state.tokens = 0.0
            current = state.last_ts
        now = asyncio.get_event_loop().time()
        elapsed = max(0.0, now - current)
        state.last_ts = now
        rate = self.params_by_path[path_id].rate_bytes_per_sec
        state.tokens += elapsed * rate
        if state.tokens < size:
            # 令牌不足则等待补足
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
        # 根据 padding 预算生成填充帧列表
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
