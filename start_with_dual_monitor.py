from __future__ import annotations

import asyncio
import os
import sys
import uuid

from config import DEFAULT_CONFIG


async def run() -> None:
    processes = []
    python = sys.executable
    run_id = f"{uuid.uuid4().hex[:8]}"
    out_dir = f"out/{run_id}"
    base_env = os.environ | {"RUN_ID": run_id, "OUT_DIR": out_dir}

    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.server", env=base_env))
    await asyncio.sleep(0.2)
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.exit", env=base_env))
    await asyncio.sleep(0.2)

    monitor_ports = [9103, 9104]
    target_ports = DEFAULT_CONFIG.middle_ports[:2]

    for monitor_port, target_port in zip(monitor_ports, target_ports):
        processes.append(
            await asyncio.create_subprocess_exec(
                python,
                "-m",
                "tools.monitor_live",
                "--listen-port",
                str(monitor_port),
                "--target-port",
                str(target_port),
                env=base_env,
            )
        )

    for port in DEFAULT_CONFIG.middle_ports:
        path_id = DEFAULT_CONFIG.middle_ports.index(port)
        processes.append(
            await asyncio.create_subprocess_exec(
                python,
                "-m",
                "nodes.middle",
                "--listen",
                str(port),
                "--exit-port",
                str(DEFAULT_CONFIG.exit_port),
                "--path-id",
                str(path_id),
                env=base_env,
            )
        )
    await asyncio.sleep(0.2)

    middle_ports = [monitor_ports[0], monitor_ports[1]] + DEFAULT_CONFIG.middle_ports[2:]
    entry_proc = await asyncio.create_subprocess_exec(
        python,
        "-m",
        "nodes.entry",
        "--listen",
        str(DEFAULT_CONFIG.entry_port),
        "--middle-ports",
        ",".join(str(port) for port in middle_ports),
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
