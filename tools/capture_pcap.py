from __future__ import annotations

import argparse
import asyncio
import os
import shutil
from pathlib import Path

from logger import setup_logger

# 启动 tcpdump/tshark 采集指定端口的流量并输出 pcap 文件。


LOGGER = setup_logger("capture_pcap")


def resolve_capture_cmd() -> list[str] | None:
    # 优先使用 tcpdump，其次 tshark
    if shutil.which("tcpdump"):
        return ["tcpdump", "-i", "any", "-n", "-s", "0"]
    if shutil.which("tshark"):
        return ["tshark", "-i", "any", "-n"]
    return None


def maybe_with_sudo(cmd: list[str]) -> list[str]:
    # WSL/Linux 下抓包通常需要 root 权限
    if os.name == "nt":
        return cmd
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return cmd
    if shutil.which("sudo"):
        return ["sudo", "-n"] + cmd
    return cmd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default="out/pcap")
    parser.add_argument("--entry-port", type=int, default=9001)
    parser.add_argument("--exit-port", type=int, default=9201)
    parser.add_argument("--middle-ports", default="9101,9102")
    return parser.parse_args()


async def run_capture(cmd: list[str], output: Path, bpf: str) -> asyncio.subprocess.Process:
    output.parent.mkdir(parents=True, exist_ok=True)
    if "tcpdump" in cmd[0]:
        full_cmd = cmd + ["-w", str(output), bpf]
    else:
        # tshark 使用 -w 并通过 -f 指定捕获过滤器
        full_cmd = cmd + ["-f", bpf, "-w", str(output)]
    full_cmd = maybe_with_sudo(full_cmd)
    LOGGER.info("启动抓包: %s", " ".join(full_cmd))
    return await asyncio.create_subprocess_exec(*full_cmd)


async def main() -> None:
    args = parse_args()
    capture_cmd = resolve_capture_cmd()
    if capture_cmd is None:
        LOGGER.error("未找到 tcpdump/tshark，无法自动抓包")
        return

    middle_ports = [p.strip() for p in args.middle_ports.split(",") if p.strip()]
    base = Path(args.out_dir)

    tasks = []
    tasks.append(
        await run_capture(
            capture_cmd,
            base / "entry.pcap",
            f"tcp port {args.entry_port}",
        )
    )
    tasks.append(
        await run_capture(
            capture_cmd,
            base / "exit.pcap",
            f"tcp port {args.exit_port}",
        )
    )
    for idx, port in enumerate(middle_ports):
        tasks.append(
            await run_capture(
                capture_cmd,
                base / f"middle_{idx}.pcap",
                f"tcp port {port}",
            )
        )

    try:
        await asyncio.gather(*[proc.wait() for proc in tasks])
    except asyncio.CancelledError:
        pass
    finally:
        for proc in tasks:
            if proc.returncode is None:
                proc.terminate()


if __name__ == "__main__":
    asyncio.run(main())
