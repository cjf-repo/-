from __future__ import annotations

from dataclasses import dataclass, field
import os
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
    proto_switch_period: int = 3
    adaptive_paths: bool = True
    adaptive_behavior: bool = True
    adaptive_proto: bool = True

    ack_timeout_sec: float = 2.0

    def paths(self) -> List[PathConfig]:
        configs = []
        for port in self.middle_ports:
            configs.append(PathConfig(host=self.middle_host, port=port))
        return configs


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name) or default


def load_config_from_env() -> Config:
    config = Config()
    path_count = _env_int("PATH_COUNT", len(config.middle_ports))
    config.middle_ports = config.middle_ports[:path_count]
    config.padding_alpha = _env_float("ALPHA_PADDING", config.padding_alpha)
    config.obfuscation_level = _env_int("OBFUSCATION_LEVEL", config.obfuscation_level)
    config.mode = _env_str("MODE", config.mode)
    config.proto_switch_period = _env_int("PROTO_SWITCH_PERIOD", config.proto_switch_period)
    config.adaptive_paths = _env_bool("ADAPTIVE_PATHS", config.adaptive_paths)
    config.adaptive_behavior = _env_bool("ADAPTIVE_BEHAVIOR", config.adaptive_behavior)
    config.adaptive_proto = _env_bool("ADAPTIVE_PROTO", config.adaptive_proto)
    seed = os.environ.get("SEED")
    if seed is not None:
        config.seed = int(seed)
    return config


DEFAULT_CONFIG = load_config_from_env()
