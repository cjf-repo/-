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
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.entry"))
    await asyncio.sleep(0.5)
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.client_app"))

    await asyncio.sleep(2)
    for proc in processes:
        if proc.returncode is None:
            proc.terminate()
    await asyncio.gather(*[proc.wait() for proc in processes], return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
