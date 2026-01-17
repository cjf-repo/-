from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from pathlib import Path

from config import DEFAULT_CONFIG


def parse_ports(value: str) -> list[int]:
    ports: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str)
            end = int(end_str)
            if start > end:
                start, end = end, start
            ports.extend(range(start, end + 1))
        else:
            ports.append(int(part))
    return ports

# 一键启动脚本：启动 server/exit/middle/entry/client。


async def run() -> None:
    # 管理子进程列表
    processes = []
    stream_tasks: list[asyncio.Task] = []
    stop_event = asyncio.Event()
    python = sys.executable
    # 支持环境变量覆盖 run_id 与输出目录
    run_id = os.environ.get("RUN_ID") or f"{uuid.uuid4().hex[:8]}"
    out_dir = os.environ.get("OUT_DIR") or f"out/{run_id}"
    base_env = os.environ | {"RUN_ID": run_id, "OUT_DIR": out_dir}
    base_env["ENABLE_TRACE"] = "0"
    if os.environ.get("START_ALL_ENABLE_TRACE") == "1":
        base_env["ENABLE_TRACE"] = "1"
    base_env["CAPTURE_PCAP"] = "0"
    if os.environ.get("START_ALL_CAPTURE_PCAP") == "1":
        base_env["CAPTURE_PCAP"] = "1"
    json_log_path = os.environ.get("START_ALL_JSON_LOG") or f"{out_dir}/start_all_logs.jsonl"
    Path(json_log_path).parent.mkdir(parents=True, exist_ok=True)
    json_log_file = open(json_log_path, "a", encoding="utf-8")
    json_lock = asyncio.Lock()

    async def read_stream(stream: asyncio.StreamReader, *, source: str, is_err: bool) -> None:
        target = sys.stderr if is_err else sys.stdout
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace").rstrip("\n")
            target.write(text + "\n")
            target.flush()
            payload = {
                "ts": time.time(),
                "source": source,
                "stream": "stderr" if is_err else "stdout",
                "message": text,
            }
            async with json_lock:
                json_log_file.write(json.dumps(payload, ensure_ascii=False) + "\n")
                json_log_file.flush()
    # 启动目标服务（外部真实服务模式可跳过）
    if os.environ.get("EXTERNAL_SERVER") != "1":
        server_proc = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "nodes.server",
            env=base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(server_proc)
        stream_tasks.append(asyncio.create_task(read_stream(server_proc.stdout, source="server", is_err=False)))
        stream_tasks.append(asyncio.create_task(read_stream(server_proc.stderr, source="server", is_err=True)))
        await asyncio.sleep(0.2)
    entry_ports = [DEFAULT_CONFIG.entry_port]
    if DEFAULT_CONFIG.batch_proxy_ports:
        entry_ports = parse_ports(DEFAULT_CONFIG.batch_proxy_ports)
    base_entry_port = DEFAULT_CONFIG.entry_port
    base_exit_port = DEFAULT_CONFIG.exit_port
    base_middle_ports = list(DEFAULT_CONFIG.middle_ports)

    middle_stride = len(base_middle_ports)
    for idx, entry_port in enumerate(entry_ports):
        exit_port = base_exit_port + idx
        middle_offset = idx * middle_stride
        middle_ports = [port + middle_offset for port in base_middle_ports]
        exit_proc = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "nodes.exit",
            "--listen",
            str(exit_port),
            env=base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(exit_proc)
        exit_source = f"exit_{entry_port}"
        stream_tasks.append(asyncio.create_task(read_stream(exit_proc.stdout, source=exit_source, is_err=False)))
        stream_tasks.append(asyncio.create_task(read_stream(exit_proc.stderr, source=exit_source, is_err=True)))
        await asyncio.sleep(0.2)
        for idx, port in enumerate(middle_ports):
            middle_proc = await asyncio.create_subprocess_exec(
                python,
                "-m",
                "nodes.middle",
                "--listen",
                str(port),
                "--exit-port",
                str(exit_port),
                "--path-id",
                str(idx),
                env=base_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(middle_proc)
            source = f"middle_{entry_port}_{idx}"
            stream_tasks.append(asyncio.create_task(read_stream(middle_proc.stdout, source=source, is_err=False)))
            stream_tasks.append(asyncio.create_task(read_stream(middle_proc.stderr, source=source, is_err=True)))
        await asyncio.sleep(0.2)
        entry_proc = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "nodes.entry",
            "--listen",
            str(entry_port),
            "--middle-ports",
            ",".join(str(port) for port in middle_ports),
            env=base_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(entry_proc)
        entry_source = f"entry_{entry_port}"
        stream_tasks.append(asyncio.create_task(read_stream(entry_proc.stdout, source=entry_source, is_err=False)))
        stream_tasks.append(asyncio.create_task(read_stream(entry_proc.stderr, source=entry_source, is_err=True)))
        await asyncio.sleep(0.5)
    # 可选：启动抓包
    capture_proc = None
    if DEFAULT_CONFIG.capture_pcap or base_env.get("CAPTURE_PCAP") == "1":
        capture_base_dir = os.environ.get("CAPTURE_DIR") or DEFAULT_CONFIG.capture_dir or f"{out_dir}/pcap"
        for idx, entry_port in enumerate(entry_ports):
            exit_port = base_exit_port + idx
            middle_offset = idx * middle_stride
            middle_ports = [port + middle_offset for port in base_middle_ports]
            capture_dir = str(Path(capture_base_dir) / f"entry_{entry_port}")
            capture_proc = await asyncio.create_subprocess_exec(
                python,
                "-m",
                "tools.capture_pcap",
                "--out-dir",
                capture_dir,
                "--entry-port",
                str(entry_port),
                "--exit-port",
                str(exit_port),
                "--middle-ports",
                ",".join([str(port) for port in middle_ports]),
                env=base_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(capture_proc)
            source = f"capture_pcap_{entry_port}"
            stream_tasks.append(asyncio.create_task(read_stream(capture_proc.stdout, source=source, is_err=False)))
            stream_tasks.append(asyncio.create_task(read_stream(capture_proc.stderr, source=source, is_err=True)))
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
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        processes.append(client_proc)
        stream_tasks.append(asyncio.create_task(read_stream(client_proc.stdout, source="client_app", is_err=False)))
        stream_tasks.append(asyncio.create_task(read_stream(client_proc.stderr, source="client_app", is_err=True)))

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
    for task in stream_tasks:
        task.cancel()
    await asyncio.gather(*stream_tasks, return_exceptions=True)
    json_log_file.close()


if __name__ == "__main__":
    asyncio.run(run())
