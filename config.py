from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class PathConfig:
    host: str
    port: int
    base_delay_ms: int = 20
    jitter_ms: int = 10
    loss_rate: float = 0.0


@dataclass
class Config:
    entry_host: str = "127.0.0.1"
    entry_port: int = 9001

    middle_host: str = "127.0.0.1"
    middle_ports: List[int] = field(default_factory=lambda: [9101, 9102])

    exit_host: str = "127.0.0.1"
    exit_port: int = 9201

    server_host: str = "127.0.0.1"
    server_port: int = 9301

    window_size_sec: int = 10
    size_bins: List[int] = field(default_factory=lambda: [300, 600, 900, 1200])
    padding_alpha: float = 0.05
    jitter_ms: int = 20
    batch_size: int = 4
    redundancy: int = 0
    base_rate_bytes_per_sec: int = 50000
    obfuscation_level: int = 2
    mode: str = "normal"
    seed: int | None = None

    ack_timeout_sec: float = 2.0

    def paths(self) -> List[PathConfig]:
        configs = []
        for port in self.middle_ports:
            configs.append(PathConfig(host=self.middle_host, port=port))
        return configs


DEFAULT_CONFIG = Config()
