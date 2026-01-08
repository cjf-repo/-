from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Tuple

from behavior import BehaviorParams


@dataclass
class StrategyOutput:
    weights: Dict[int, float]
    behavior: BehaviorParams
    proto_id: int


class StrategyEngine:
    def __init__(
        self,
        size_bins: List[int],
        base_padding: float,
        base_jitter: int,
        proto_ids: List[int],
    ) -> None:
        self.size_bins = size_bins
        self.base_padding = base_padding
        self.base_jitter = base_jitter
        self.proto_ids = proto_ids
        self._proto_index = 0

    def evaluate(
        self,
        metrics: Dict[int, Dict[str, float]],
        timeout_events: int,
    ) -> StrategyOutput:
        weights: Dict[int, float] = {}
        for path_id, stats in metrics.items():
            weight = 1.0
            if stats["loss"] > 0.1 or stats["rtt_ms"] > 200:
                weight *= 0.5
            weights[path_id] = weight

        mean_loss = sum(stats["loss"] for stats in metrics.values()) / max(
            len(metrics), 1
        )
        mean_rtt = sum(stats["rtt_ms"] for stats in metrics.values()) / max(
            len(metrics), 1
        )

        padding = self.base_padding
        jitter = self.base_jitter
        if mean_loss > 0.2 or mean_rtt > 250:
            padding = max(0.01, padding * 0.5)
            jitter = max(5, int(jitter * 0.5))

        size_bins = [
            int(b * random.uniform(0.9, 1.1)) for b in self.size_bins
        ]

        if timeout_events > 2:
            self._proto_index = (self._proto_index + 1) % len(self.proto_ids)
        proto_id = self.proto_ids[self._proto_index]

        return StrategyOutput(
            weights=weights,
            behavior=BehaviorParams(size_bins=size_bins, padding_alpha=padding, jitter_ms=jitter),
            proto_id=proto_id,
        )
