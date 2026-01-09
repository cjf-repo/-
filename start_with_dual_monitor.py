from __future__ import annotations

import asyncio
import sys

from config import DEFAULT_CONFIG


async def run() -> None:
    processes = []
    python = sys.executable

    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.server"))
    await asyncio.sleep(0.2)
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.exit"))
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
            )
        )

    for port in DEFAULT_CONFIG.middle_ports:
        processes.append(
            await asyncio.create_subprocess_exec(
                python,
                "-m",
                "nodes.middle",
                "--listen",
                str(port),
                "--exit-port",
                str(DEFAULT_CONFIG.exit_port),
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
    )
    processes.append(client_proc)

    await client_proc.wait()
    for proc in processes:
        if proc.returncode is None:
            proc.terminate()
    await asyncio.gather(*[proc.wait() for proc in processes], return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
