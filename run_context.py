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
    run_id: str
    out_dir: Path
    traces_dir: Path
    attacker_path_id: int
    seed: int
    window_log_path: Path

    def write_window_log(self, record: Dict[str, Any]) -> None:
        with self.window_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


_CONTEXT: RunContext | None = None


def get_run_context(config: Config = DEFAULT_CONFIG) -> RunContext:
    global _CONTEXT
    if _CONTEXT is not None:
        return _CONTEXT

    run_id = os.environ.get("RUN_ID") or time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    out_dir = Path(os.environ.get("OUT_DIR") or Path("out") / run_id)
    traces_dir = out_dir / "traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    traces_dir.mkdir(parents=True, exist_ok=True)

    seed_env = os.environ.get("SEED")
    seed = int(seed_env) if seed_env is not None else (config.seed or random.randint(1, 10_000_000))
    random.seed(seed)

    meta_path = out_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        attacker_path_id = int(meta.get("attacker_path_id", 0))
    else:
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

    config_dump_path = out_dir / "config_dump.json"
    if not config_dump_path.exists():
        config_dump_path.write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    window_log_path = out_dir / "window_logs.jsonl"
    _CONTEXT = RunContext(
        run_id=run_id,
        out_dir=out_dir,
        traces_dir=traces_dir,
        attacker_path_id=attacker_path_id,
        seed=seed,
        window_log_path=window_log_path,
    )
    return _CONTEXT
