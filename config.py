from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
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
    # 中继模拟时延（毫秒）
    middle_base_delay_ms: int = 20
    # 中继模拟抖动（毫秒）
    middle_jitter_ms: int = 10
    # 中继模拟丢包率
    middle_loss_rate: float = 0.0
    # CONNECT 隧道模式下最小发送 chunk 大小（字节，<=0 表示不放大）
    tunnel_min_chunk_bytes: int = 0

    # 出口节点监听地址/端口
    exit_host: str = "127.0.0.1"
    exit_port: int = 9201

    # 目标服务地址/端口
    server_host: str = "127.0.0.1"
    server_port: int = 9301
    # 服务器模式：echo（本地回显）/ forward（转发外部真实服务）
    server_mode: str = "forward"
    # 是否使用外部真实服务（跳过本地 echo server）
    external_server: bool = False
    # 入口是否作为 HTTP 代理（仅支持明文 HTTP）
    proxy_mode: bool = True

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

    # 多路径开关
    enable_multipath: bool = True
    # 行为伪装开关（整形/填充/抖动/限速）
    enable_behavior: bool = False
    # 属性伪装开关（协议模板/握手/编码）
    enable_obfuscation: bool = False
    # 中继 trace 记录开关
    enable_trace: bool = True

    # 主动探测间隔（秒，<=0 禁用）
    probe_interval_sec: float = 10
    # 探测载荷长度（字节）
    probe_payload_len: int = 8
    # 威胁等级模式（auto/fixed/random）
    threat_mode: str = "auto"
    # 固定威胁等级（0-3）
    threat_level: int = 2

    # ACK 超时时间
    ack_timeout_sec: float = 2.0
    # 上游 HTTP 响应流空闲超时（秒）
    upstream_idle_timeout_sec: float = 3.0
    # 代理模式下，单个缺失下行序号允许的超时次数（超过才断开）
    proxy_missing_seq_tolerance: int = 3
    # 下行写回客户端的批量 flush 阈值（字节）
    downlink_drain_bytes: int = 32768

    # 抓包开关
    capture_pcap: bool = False
    # 抓包输出目录
    capture_dir: str | None = None
    # 是否输出节点日志到控制台
    console_log: bool = True

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


def load_config_from_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("配置文件必须是 JSON 对象。")
    required = ["entry_host", "entry_port", "exit_port", "middle_ports", "server_host", "server_port"]
    missing = [key for key in required if key not in data]
    if missing:
        raise SystemExit(f"配置文件缺少字段: {', '.join(missing)}")
    allowed_keys = {
        "entry_host",
        "entry_port",
        "middle_host",
        "middle_ports",
        "middle_base_delay_ms",
        "middle_jitter_ms",
        "middle_loss_rate",
        "tunnel_min_chunk_bytes",
        "exit_host",
        "exit_port",
        "server_host",
        "server_port",
        "server_mode",
        "external_server",
        "proxy_mode",
        "capture_pcap",
        "capture_dir",
        "console_log",
        "ack_timeout_sec",
        "upstream_idle_timeout_sec",
        "proxy_missing_seq_tolerance",
        "downlink_drain_bytes",
    }
    overrides = {key: data[key] for key in allowed_keys if key in data}
    middle_ports = overrides.get("middle_ports")
    if not isinstance(middle_ports, list) or not middle_ports:
        raise SystemExit("middle_ports 必须是非空列表。")
    overrides["middle_ports"] = [int(port) for port in middle_ports]
    overrides["entry_port"] = int(overrides["entry_port"])
    overrides["exit_port"] = int(overrides["exit_port"])
    overrides["server_port"] = int(overrides["server_port"])
    if "middle_base_delay_ms" in overrides:
        overrides["middle_base_delay_ms"] = int(overrides["middle_base_delay_ms"])
    if "middle_jitter_ms" in overrides:
        overrides["middle_jitter_ms"] = int(overrides["middle_jitter_ms"])
    if "middle_loss_rate" in overrides:
        overrides["middle_loss_rate"] = float(overrides["middle_loss_rate"])
    if "tunnel_min_chunk_bytes" in overrides:
        overrides["tunnel_min_chunk_bytes"] = int(overrides["tunnel_min_chunk_bytes"])
    if "ack_timeout_sec" in overrides:
        overrides["ack_timeout_sec"] = float(overrides["ack_timeout_sec"])
    if "upstream_idle_timeout_sec" in overrides:
        overrides["upstream_idle_timeout_sec"] = float(overrides["upstream_idle_timeout_sec"])
    if "proxy_missing_seq_tolerance" in overrides:
        overrides["proxy_missing_seq_tolerance"] = int(overrides["proxy_missing_seq_tolerance"])
    if "downlink_drain_bytes" in overrides:
        overrides["downlink_drain_bytes"] = int(overrides["downlink_drain_bytes"])
    if "console_log" in overrides and not isinstance(overrides["console_log"], bool):
        raise SystemExit("console_log 必须是布尔值 true/false。")
    return overrides


def load_config_from_env() -> Config:
    # 使用默认配置构建，然后覆盖配置文件
    config = Config()
    config_path = os.environ.get("CONFIG_PATH")
    if not config_path:
        raise SystemExit("需要设置 CONFIG_PATH 指向配置文件。")
    overrides = load_config_from_file(Path(config_path))
    config = replace(config, **overrides)
    # 按需覆盖实验参数
    config.padding_alpha = _env_float("ALPHA_PADDING", config.padding_alpha)
    config.obfuscation_level = _env_int("OBFUSCATION_LEVEL", config.obfuscation_level)
    config.mode = _env_str("MODE", config.mode)
    config.server_mode = _env_str("SERVER_MODE", config.server_mode)
    config.external_server = _env_bool("EXTERNAL_SERVER", config.external_server)
    config.proxy_mode = _env_bool("PROXY_MODE", config.proxy_mode)
    config.proto_switch_period = _env_int("PROTO_SWITCH_PERIOD", config.proto_switch_period)
    config.adaptive_paths = _env_bool("ADAPTIVE_PATHS", config.adaptive_paths)
    config.adaptive_behavior = _env_bool("ADAPTIVE_BEHAVIOR", config.adaptive_behavior)
    config.adaptive_proto = _env_bool("ADAPTIVE_PROTO", config.adaptive_proto)
    config.enable_multipath = _env_bool("ENABLE_MULTIPATH", config.enable_multipath)
    config.enable_behavior = _env_bool("ENABLE_BEHAVIOR", config.enable_behavior)
    config.enable_obfuscation = _env_bool("ENABLE_OBFUSCATION", config.enable_obfuscation)
    config.enable_trace = _env_bool("ENABLE_TRACE", config.enable_trace)
    config.middle_base_delay_ms = _env_int("MIDDLE_BASE_DELAY_MS", config.middle_base_delay_ms)
    config.middle_jitter_ms = _env_int("MIDDLE_JITTER_MS", config.middle_jitter_ms)
    config.middle_loss_rate = _env_float("MIDDLE_LOSS_RATE", config.middle_loss_rate)
    config.tunnel_min_chunk_bytes = _env_int("TUNNEL_MIN_CHUNK_BYTES", config.tunnel_min_chunk_bytes)
    config.capture_pcap = _env_bool("CAPTURE_PCAP", config.capture_pcap)
    config.capture_dir = os.environ.get("CAPTURE_DIR") or config.capture_dir
    config.probe_interval_sec = _env_float("PROBE_INTERVAL_SEC", config.probe_interval_sec)
    config.probe_payload_len = _env_int("PROBE_PAYLOAD_LEN", config.probe_payload_len)
    config.threat_mode = _env_str("THREAT_MODE", config.threat_mode)
    config.threat_level = _env_int("THREAT_LEVEL", config.obfuscation_level)
    config.ack_timeout_sec = _env_float("ACK_TIMEOUT_SEC", config.ack_timeout_sec)
    config.upstream_idle_timeout_sec = _env_float("UPSTREAM_IDLE_TIMEOUT_SEC", config.upstream_idle_timeout_sec)
    config.proxy_missing_seq_tolerance = _env_int("PROXY_MISSING_SEQ_TOLERANCE", config.proxy_missing_seq_tolerance)
    config.downlink_drain_bytes = _env_int("DOWNLINK_DRAIN_BYTES", config.downlink_drain_bytes)
    # 如提供 SEED 则固定随机种子
    seed = os.environ.get("SEED")
    if seed is not None:
        config.seed = int(seed)
    return config


# 默认配置：启动时即读取环境变量
DEFAULT_CONFIG = load_config_from_env()
