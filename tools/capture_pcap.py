from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import signal
from pathlib import Path

from logger import setup_logger

# 启动 tcpdump/tshark 采集指定端口的流量并输出 pcap 文件。


LOGGER = setup_logger("capture_pcap")


def resolve_capture_cmd() -> list[str] | None:
    # 优先使用 tcpdump，其次 tshark
    if shutil.which("tcpdump"):
        return ["tcpdump", "-i", "any", "-n", "-s", "0", "-U"]
    if shutil.which("tshark"):
        return ["tshark", "-i", "any", "-n"]
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="out/pcap")
    parser.add_argument("--ready-file", default=None, help="抓包启动完成后写入的标记文件路径。")
    parser.add_argument("--config", default=None, help="可选 JSON 配置文件，覆盖端口配置。")
    parser.add_argument("--entry-port", type=int, default=9001)
    parser.add_argument("--exit-port", type=int, default=9201)
    parser.add_argument("--middle-ports", default="9101,9102")
    return parser.parse_args()


async def run_capture(
    cmd: list[str],
    output: Path,
    bpf: str,
    *,
    check_startup: bool = False,
) -> asyncio.subprocess.Process | None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.touch(exist_ok=True)
    if "tcpdump" in cmd[0]:
        full_cmd = cmd + ["-w", str(output), bpf]
    else:
        # tshark 使用 -w 并通过 -f 指定捕获过滤器
        full_cmd = cmd + ["-f", bpf, "-w", str(output)]
    LOGGER.info("启动抓包: %s", " ".join(full_cmd))
    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    if check_startup:
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT)
            LOGGER.info("抓包启动被取消")
            return None
        if proc.returncode is not None:
            _, stderr = await proc.communicate()
            stderr_text = (stderr or b"").decode(errors="ignore").strip()
            if stderr_text:
                LOGGER.error("抓包启动失败: %s", stderr_text)
            else:
                LOGGER.error("抓包启动失败：未知错误")
            return None
    return proc


async def main() -> None:
    args = parse_args()
    capture_cmd = resolve_capture_cmd()
    if capture_cmd is None:
        LOGGER.error("未找到 tcpdump/tshark，无法自动抓包")
        return
    middle_targets: list[tuple[str, int]] = []
    config_path = args.config or os.environ.get("CONFIG_PATH")
    if config_path:
        os.environ["CONFIG_PATH"] = config_path
        from config import DEFAULT_CONFIG

        args.entry_port = DEFAULT_CONFIG.entry_port
        args.exit_port = DEFAULT_CONFIG.exit_port
        single_hop_only = all(len(route.hops) == 1 for route in DEFAULT_CONFIG.route_paths())
        for route in DEFAULT_CONFIG.route_paths():
            for hop_idx, hop in enumerate(route.hops):
                if single_hop_only:
                    label = f"middle_{route.path_id}"
                else:
                    label = f"middle_p{route.path_id}_h{hop_idx}"
                middle_targets.append((label, hop.port))
    else:
        middle_ports = [p.strip() for p in args.middle_ports.split(",") if p.strip()]
        middle_targets = [(f"middle_{idx}", int(port)) for idx, port in enumerate(middle_ports)]
    base = Path(args.out_dir)

    tasks: list[asyncio.subprocess.Process] = []
    first_proc = await run_capture(
        capture_cmd,
        base / "entry.pcap",
        f"tcp port {args.entry_port}",
        check_startup=True,
    )
    if first_proc is None:
        LOGGER.warning("抓包权限不足或环境不支持，可设置 CAPTURE_PCAP=0 跳过抓包。")
        return
    tasks.append(first_proc)
    exit_proc = await run_capture(
        capture_cmd,
        base / "exit.pcap",
        f"tcp port {args.exit_port}",
        check_startup=True,
    )
    if exit_proc is None:
        for proc in tasks:
            if proc.returncode is None:
                proc.terminate()
        return
    tasks.append(exit_proc)
    for label, port in middle_targets:
        proc = await run_capture(
            capture_cmd,
            base / f"{label}.pcap",
            f"tcp port {port}",
            check_startup=True,
        )
        if proc is None:
            for started in tasks:
                if started.returncode is None:
                    started.terminate()
            return
        tasks.append(proc)

    if args.ready_file:
        Path(args.ready_file).parent.mkdir(parents=True, exist_ok=True)
        Path(args.ready_file).write_text("ready", encoding="utf-8")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    try:
        await asyncio.wait(
            [asyncio.create_task(proc.wait()) for proc in tasks] + [asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
    except asyncio.CancelledError:
        pass
    finally:
        for proc in tasks:
            if proc.returncode is None:
                proc.send_signal(signal.SIGINT)
        waiters = []
        for proc in tasks:
            if proc.returncode is None:
                waiters.append(asyncio.wait_for(proc.wait(), timeout=2))
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        for proc in tasks:
            if proc.returncode is None:
                proc.terminate()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
