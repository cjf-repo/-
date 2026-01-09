from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PathStats:
    sent: int = 0
    acked: int = 0
    rtt_ms: float = 0.0
    last_send_ts: Dict[int, float] = field(default_factory=dict)


class MultiPathScheduler:
    def __init__(self, path_ids: List[int], batch_size: int) -> None:
        self.path_ids = path_ids
        self.weights = {path_id: 1.0 for path_id in path_ids}
        self.batch_size = batch_size
        self._batch_remaining = 0
        self._current_path = random.choice(path_ids)
        self.stats = {path_id: PathStats() for path_id in path_ids}

    def update_weights(self, weights: Dict[int, float]) -> None:
        for path_id, weight in weights.items():
            self.weights[path_id] = max(weight, 0.1)

    def choose_path(self) -> int:
        # 批量随机：同一批次走同一路径，降低乱序
        if self._batch_remaining <= 0:
            self._current_path = random.choices(
                self.path_ids, weights=[self.weights[p] for p in self.path_ids]
            )[0]
            self._batch_remaining = self.batch_size
        self._batch_remaining -= 1
        return self._current_path

    def choose_path_from(self, allowed_paths: List[int]) -> int:
        if not allowed_paths:
            raise ValueError("allowed_paths 不能为空")
        if self._current_path not in allowed_paths or self._batch_remaining <= 0:
            self._current_path = random.choices(
                allowed_paths, weights=[self.weights[p] for p in allowed_paths]
            )[0]
            self._batch_remaining = self.batch_size
        self._batch_remaining -= 1
        return self._current_path

    def mark_sent(self, path_id: int, seq: int) -> None:
        self.stats[path_id].sent += 1
        self.stats[path_id].last_send_ts[seq] = time.time()

    def mark_ack(self, path_id: int, seq: int) -> None:
        path_stats = self.stats[path_id]
        path_stats.acked += 1
        sent_ts = path_stats.last_send_ts.pop(seq, None)
        if sent_ts is not None:
            # RTT 采用平滑估计
            rtt = (time.time() - sent_ts) * 1000
            path_stats.rtt_ms = (path_stats.rtt_ms * 0.7) + (rtt * 0.3)

    def snapshot(self) -> Dict[int, Dict[str, float]]:
        data = {}
        for path_id, stats in self.stats.items():
            loss = 0.0
            if stats.sent > 0:
                loss = max(0.0, 1 - stats.acked / stats.sent)
            data[path_id] = {
                "rtt_ms": stats.rtt_ms,
                "loss": loss,
            }
        return data
