from __future__ import annotations

import asyncio
import os
import sys
import uuid

from config import DEFAULT_CONFIG

# 启动脚本：带监控进程的单机实验启动。


async def run() -> None:
    # 管理子进程
    processes = []
    python = sys.executable
    # 为监控场景生成 run_id 与输出目录
    run_id = f"{uuid.uuid4().hex[:8]}"
    out_dir = f"out/{run_id}"
    base_env = os.environ | {"RUN_ID": run_id, "OUT_DIR": out_dir}

    # 启动各节点
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.server", env=base_env))
    await asyncio.sleep(0.2)
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.exit", env=base_env))
    await asyncio.sleep(0.2)

    first_hop_ports = DEFAULT_CONFIG.first_hop_ports()
    route_paths = DEFAULT_CONFIG.route_paths()
    monitor_port = 9103
    target_middle_port = first_hop_ports[0]
    processes.append(
            await asyncio.create_subprocess_exec(
                python,
                "-m",
                "tools.monitor_live",
                "--listen-port",
                str(monitor_port),
                "--target-port",
                str(target_middle_port),
                env=base_env,
            )
        )

    for route in route_paths:
        for hop in route.hops:
            processes.append(
                await asyncio.create_subprocess_exec(
                    python,
                    "-m",
                    "nodes.middle",
                    "--listen",
                    str(hop.port),
                    "--exit-host",
                    str(hop.next_host or DEFAULT_CONFIG.exit_host),
                    "--exit-port",
                    str(hop.next_port or DEFAULT_CONFIG.exit_port),
                    "--base-delay",
                    str(hop.base_delay_ms),
                    "--jitter",
                    str(hop.jitter_ms),
                    "--loss",
                    str(hop.loss_rate),
                    "--path-id",
                    str(route.path_id),
                    env=base_env,
                )
            )
    await asyncio.sleep(0.2)

    override_ports = [monitor_port] + first_hop_ports[1:]
    entry_proc = await asyncio.create_subprocess_exec(
        python,
        "-m",
        "nodes.entry",
        "--listen",
        str(DEFAULT_CONFIG.entry_port),
        "--middle-ports",
        ",".join(str(port) for port in override_ports),
        env=base_env,
    )
    processes.append(entry_proc)

    await asyncio.sleep(0.5)
    client_proc = await asyncio.create_subprocess_exec(
        python,
        "-m",
        "nodes.client_app",
        "--duration",
        "20",
        "--interval",
        "0.5",
        env=base_env,
    )
    processes.append(client_proc)

    await client_proc.wait()
    for proc in processes:
        if proc.returncode is None:
            proc.terminate()
    await asyncio.gather(*[proc.wait() for proc in processes], return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
