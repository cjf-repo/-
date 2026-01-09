from __future__ import annotations

import itertools
import os
import subprocess
import sys
import uuid


def run_one(env: dict[str, str], sessions: int) -> None:
    run_id = env.get("RUN_ID") or uuid.uuid4().hex[:8]
    env["RUN_ID"] = run_id
    env["OUT_DIR"] = f"out/{run_id}"
    env["SESSION_COUNT"] = str(sessions)
    subprocess.run([sys.executable, "start_all.py"], check=True, env=env)
    subprocess.run([sys.executable, "metrics.py", "--run-dir", env["OUT_DIR"]], check=True, env=env)


def main() -> None:
    base_env = os.environ.copy()
    path_counts = [2, 3, 4]
    obfuscation_levels = [0, 1, 2, 3]
    alpha_paddings = [0.02, 0.05, 0.1]
    proto_switch_periods = [1, 3, 5]
    adaptive_modes = [
        "static",
        "adaptive_paths_only",
        "adaptive_behavior_only",
        "adaptive_proto_only",
        "full_adaptive",
    ]
    sessions_per_run = 3

    for path_count, level, alpha, switch_period, adaptive_mode in itertools.product(
        path_counts,
        obfuscation_levels,
        alpha_paddings,
        proto_switch_periods,
        adaptive_modes,
    ):
        env = base_env.copy()
        env["PATH_COUNT"] = str(path_count)
        env["OBFUSCATION_LEVEL"] = str(level)
        env["ALPHA_PADDING"] = str(alpha)
        env["PROTO_SWITCH_PERIOD"] = str(switch_period)
        if adaptive_mode == "static":
            env["ADAPTIVE_PATHS"] = "0"
            env["ADAPTIVE_BEHAVIOR"] = "0"
            env["ADAPTIVE_PROTO"] = "0"
        elif adaptive_mode == "adaptive_paths_only":
            env["ADAPTIVE_PATHS"] = "1"
            env["ADAPTIVE_BEHAVIOR"] = "0"
            env["ADAPTIVE_PROTO"] = "0"
        elif adaptive_mode == "adaptive_behavior_only":
            env["ADAPTIVE_PATHS"] = "0"
            env["ADAPTIVE_BEHAVIOR"] = "1"
            env["ADAPTIVE_PROTO"] = "0"
        elif adaptive_mode == "adaptive_proto_only":
            env["ADAPTIVE_PATHS"] = "0"
            env["ADAPTIVE_BEHAVIOR"] = "0"
            env["ADAPTIVE_PROTO"] = "1"
        else:
            env["ADAPTIVE_PATHS"] = "1"
            env["ADAPTIVE_BEHAVIOR"] = "1"
            env["ADAPTIVE_PROTO"] = "1"
        env["MODE"] = "normal"
        run_one(env, sessions_per_run)

    for mode in ["baseline_delay", "baseline_padding"]:
        env = base_env.copy()
        env["MODE"] = mode
        env["PATH_COUNT"] = "1"
        run_one(env, sessions_per_run)


if __name__ == "__main__":
    main()
