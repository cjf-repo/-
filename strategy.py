from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

from behavior import BehaviorParams


@dataclass
class StrategyOutput:
    weights: Dict[int, float]
    behavior_by_path: Dict[int, BehaviorParams]
    family_by_path: Dict[int, int]
    variant_by_path: Dict[int, int]
    obfuscation_level: int


class StrategyEngine:
    def __init__(
        self,
        size_bins: List[int],
        base_padding: float,
        base_jitter: int,
        family_ids: List[int],
        base_rate: int,
        obfuscation_level: int,
        mode: str,
    ) -> None:
        self.size_bins = size_bins
        self.base_padding = base_padding
        self.base_jitter = base_jitter
        self.family_ids = family_ids
        self.base_rate = base_rate
        self.obfuscation_level = obfuscation_level
        self.mode = mode
        self._family_index = 0
        self._variant_seed = 0

    def evaluate(
        self,
        metrics: Dict[int, Dict[str, float]],
        timeout_events: int,
    ) -> StrategyOutput:
        # 简化的规则引擎：根据 RTT/丢包调整权重与行为参数
        weights: Dict[int, float] = {}
        behavior_by_path: Dict[int, BehaviorParams] = {}
        family_by_path: Dict[int, int] = {}
        variant_by_path: Dict[int, int] = {}
        for path_id, stats in metrics.items():
            weight = 1.0
            if stats["loss"] > 0.1 or stats["rtt_ms"] > 200:
                weight *= 0.5
            weights[path_id] = weight

        mean_loss = sum(stats["loss"] for stats in metrics.values()) / max(len(metrics), 1)
        mean_rtt = sum(stats["rtt_ms"] for stats in metrics.values()) / max(len(metrics), 1)

        padding = self.base_padding
        jitter = self.base_jitter
        rate = self.base_rate
        drift = 0.02
        burst = 6
        level = self.obfuscation_level
        enable_shaping = True
        enable_padding = True
        enable_pacing = True
        enable_jitter = True

        if level == 0:
            padding = 0.0
            jitter = 0
            drift = 0.0
            burst = 1
            rate = self.base_rate * 2
            enable_padding = False
            enable_shaping = False
            enable_pacing = False
            enable_jitter = False
        elif level == 1:
            drift = 0.02
            burst = 4
            rate = int(self.base_rate * 1.2)
        elif level == 2:
            drift = 0.05
            burst = 6
            rate = self.base_rate
        else:
            drift = 0.08
            burst = 8
            rate = int(self.base_rate * 0.8)

        if mean_loss > 0.2 or mean_rtt > 250:
            padding = max(0.01, padding * 0.5)
            jitter = max(5, int(jitter * 0.5))
            rate = int(rate * 0.8)

        size_bins = [int(b * random.uniform(0.9, 1.1)) for b in self.size_bins]
        q_dist = [1 / len(size_bins) for _ in size_bins]

        if timeout_events > 2:
            self._family_index = (self._family_index + 1) % len(self.family_ids)
            self._variant_seed += 1

        for path_id in metrics.keys():
            if self.mode == "baseline_delay":
                family_id = 1
                variant_id = 0
                enable_shaping = False
                enable_padding = False
                enable_pacing = True
                enable_jitter = True
            elif self.mode == "baseline_padding":
                family_id = 1
                variant_id = 0
                enable_shaping = True
                enable_padding = True
                enable_pacing = False
                enable_jitter = False
            else:
                family_id = self.family_ids[(self._family_index + path_id) % len(self.family_ids)]
                variant_id = (self._variant_seed + path_id) % 2
            family_by_path[path_id] = family_id
            variant_by_path[path_id] = variant_id
            behavior_by_path[path_id] = BehaviorParams(
                size_bins=size_bins,
                q_dist=q_dist,
                padding_alpha=padding,
                jitter_ms=jitter,
                rate_bytes_per_sec=rate,
                burst_size=burst,
                obfuscation_level=level,
                enable_shaping=enable_shaping,
                enable_padding=enable_padding,
                enable_pacing=enable_pacing,
                enable_jitter=enable_jitter,
            )

        return StrategyOutput(
            weights=weights,
            behavior_by_path=behavior_by_path,
            family_by_path=family_by_path,
            variant_by_path=variant_by_path,
            obfuscation_level=level,
        )
