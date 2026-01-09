from __future__ import annotations

import json
import os
import random
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from config import Config, DEFAULT_CONFIG


@dataclass
class RunContext:
    # 单次运行唯一标识
    run_id: str
    # 输出目录
    out_dir: Path
    # 轨迹目录
    traces_dir: Path
    # 攻击者可观测路径 ID
    attacker_path_id: int
    # 随机种子
    seed: int
    # 窗口日志路径
    window_log_path: Path
    # 延迟日志路径
    latency_log_path: Path

    def write_window_log(self, record: Dict[str, Any]) -> None:
        # 追加写窗口日志（JSONL）
        with self.window_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_latency_log(self, record: Dict[str, Any]) -> None:
        # 追加写延迟日志（JSONL）
        with self.latency_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


_CONTEXT: RunContext | None = None


def get_run_context(config: Config = DEFAULT_CONFIG) -> RunContext:
    # 全局单例：避免多个模块各自创建输出目录
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT

    # 运行级唯一标识，用于输出目录与实验复现
    run_id = os.environ.get("RUN_ID") or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    # 输出目录支持环境变量覆盖
    out_dir = Path(os.environ.get("OUT_DIR") or Path("out") / run_id)
    traces_dir = out_dir / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)

    # 固定随机种子，保证策略漂移/分配可复现
    seed_env = os.environ.get("SEED")
    seed = int(seed_env) if seed_env is not None else (config.seed or random.randint(1, 10_000_000))
    random.seed(seed)

    # 元信息：首次运行写入 meta.json
    meta_path = out_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        attacker_path_id = int(meta.get("attacker_path_id", 0))
    else:
        # 选择攻击者可观测路径（固定/随机）
        attacker_env = os.environ.get("ATTACKER_PATH_ID")
        if attacker_env is not None:
            attacker_path_id = int(attacker_env)
        else:
            rng = random.Random(seed)
            attacker_path_id = rng.randint(0, max(0, len(config.middle_ports) - 1))
        meta = {
            "run_id": run_id,
            "seed": seed,
            "attacker_path_id": attacker_path_id,
            "start_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写入配置快照，便于复现
    config_dump_path = out_dir / "config_dump.json"
    if not config_dump_path.exists():
        config_dump_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # 初始化日志路径
    window_log_path = out_dir / "window_logs.jsonl"
    latency_log_path = out_dir / "latency_logs.jsonl"
    _CONTEXT = RunContext(
        run_id=run_id,
        out_dir=out_dir,
        traces_dir=traces_dir,
        attacker_path_id=attacker_path_id,
        seed=seed,
        window_log_path=window_log_path,
        latency_log_path=latency_log_path,
    )
    return _CONTEXT
