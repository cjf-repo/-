from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import List

# 配置模块：集中管理默认配置，并支持从环境变量加载实验参数。


@dataclass
class PathConfig:
    # 中继节点地址
    host: str
    # 中继节点端口
    port: int
    # 单路径基础延迟
    base_delay_ms: int = 20
    # 抖动延迟
    jitter_ms: int = 10
    # 丢包率
    loss_rate: float = 0.0


@dataclass
class Config:
    # 入口节点监听地址/端口
    entry_host: str = "127.0.0.1"
    entry_port: int = 9001

    # 中继节点主机与端口列表
    middle_host: str = "127.0.0.1"
    middle_ports: List[int] = field(default_factory=lambda: [9101, 9102])

    # 出口节点监听地址/端口
    exit_host: str = "127.0.0.1"
    exit_port: int = 9201

    # 目标服务地址/端口
    server_host: str = "127.0.0.1"
    server_port: int = 9301

    # 窗口大小（秒）
    window_size_sec: int = 10
    # 采样长度桶
    size_bins: List[int] = field(default_factory=lambda: [300, 600, 900, 1200])
    # padding 系数
    padding_alpha: float = 0.05
    # 默认抖动
    jitter_ms: int = 20
    # 批量发送大小
    batch_size: int = 4
    # 冗余份数
    redundancy: int = 0
    # 基础速率
    base_rate_bytes_per_sec: int = 50000
    # 混淆等级
    obfuscation_level: int = 2
    # 模式（normal / baseline_*）
    mode: str = "normal"
    # 随机种子
    seed: int | None = None
    # 协议切换周期
    proto_switch_period: int = 3
    # 自适应路径开关
    adaptive_paths: bool = True
    # 自适应行为开关
    adaptive_behavior: bool = True
    # 自适应协议开关
    adaptive_proto: bool = True

    # ACK 超时时间
    ack_timeout_sec: float = 2.0

    def paths(self) -> List[PathConfig]:
        # 根据端口列表生成路径配置
        configs = []
        for port in self.middle_ports:
            configs.append(PathConfig(host=self.middle_host, port=port))
        return configs


def _env_int(name: str, default: int) -> int:
    # 从环境变量读取整数
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    # 从环境变量读取浮点数
    value = os.environ.get(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    # 从环境变量读取布尔值
    value = os.environ.get(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "y"}


def _env_str(name: str, default: str) -> str:
    # 从环境变量读取字符串
    return os.environ.get(name) or default


def load_config_from_env() -> Config:
    # 使用默认配置构建，然后覆盖环境变量
    config = Config()
    # 路径数量通过裁剪端口列表实现
    path_count = _env_int("PATH_COUNT", len(config.middle_ports))
    config.middle_ports = config.middle_ports[:path_count]
    # 按需覆盖实验参数
    config.padding_alpha = _env_float("ALPHA_PADDING", config.padding_alpha)
    config.obfuscation_level = _env_int("OBFUSCATION_LEVEL", config.obfuscation_level)
    config.mode = _env_str("MODE", config.mode)
    config.proto_switch_period = _env_int("PROTO_SWITCH_PERIOD", config.proto_switch_period)
    config.adaptive_paths = _env_bool("ADAPTIVE_PATHS", config.adaptive_paths)
    config.adaptive_behavior = _env_bool("ADAPTIVE_BEHAVIOR", config.adaptive_behavior)
    config.adaptive_proto = _env_bool("ADAPTIVE_PROTO", config.adaptive_proto)
    # 如提供 SEED 则固定随机种子
    seed = os.environ.get("SEED")
    if seed is not None:
        config.seed = int(seed)
    return config


# 默认配置：启动时即读取环境变量
DEFAULT_CONFIG = load_config_from_env()
