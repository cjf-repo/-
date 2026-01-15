from __future__ import annotations

import asyncio
import os
import signal
import sys
import uuid

from config import DEFAULT_CONFIG

# 一键启动脚本：启动 server/exit/middle/entry/client。


async def run() -> None:
    # 管理子进程列表
    processes = []
    stop_event = asyncio.Event()
    python = sys.executable
    # 支持环境变量覆盖 run_id 与输出目录
    run_id = os.environ.get("RUN_ID") or f"{uuid.uuid4().hex[:8]}"
    out_dir = os.environ.get("OUT_DIR") or f"out/{run_id}"
    base_env = os.environ | {"RUN_ID": run_id, "OUT_DIR": out_dir}
    # 启动目标服务（外部真实服务模式可跳过）
    if os.environ.get("EXTERNAL_SERVER") != "1":
        processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.server", env=base_env))
        await asyncio.sleep(0.2)
    # 启动出口节点
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.exit", env=base_env))
    await asyncio.sleep(0.2)
    # 启动中继节点
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
    # 启动入口节点
    processes.append(await asyncio.create_subprocess_exec(python, "-m", "nodes.entry", env=base_env))
    await asyncio.sleep(0.5)
    # 可选：启动抓包
    capture_proc = None
    if DEFAULT_CONFIG.capture_pcap or os.environ.get("CAPTURE_PCAP") == "1":
        capture_dir = os.environ.get("CAPTURE_DIR") or DEFAULT_CONFIG.capture_dir or f"{out_dir}/pcap"
        capture_proc = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "tools.capture_pcap",
            "--out-dir",
            capture_dir,
            "--entry-port",
            str(DEFAULT_CONFIG.entry_port),
            "--exit-port",
            str(DEFAULT_CONFIG.exit_port),
            "--middle-ports",
            ",".join([str(port) for port in DEFAULT_CONFIG.middle_ports]),
            env=base_env,
        )
        processes.append(capture_proc)
    # 启动客户端应用（代理模式下由浏览器/curl 触发）
    client_proc = None
    if os.environ.get("PROXY_MODE") != "1":
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

    # 等待客户端完成后回收子进程
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if client_proc is not None:
        done, pending = await asyncio.wait(
            {asyncio.create_task(client_proc.wait()), asyncio.create_task(stop_event.wait())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    else:
        await stop_event.wait()
    for proc in processes:
        if proc.returncode is None:
            proc.terminate()
    await asyncio.gather(*[proc.wait() for proc in processes], return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run())
