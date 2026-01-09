from __future__ import annotations

import argparse
import asyncio
import os
import time

from config import DEFAULT_CONFIG
from logger import setup_logger


LOGGER = setup_logger("client_app")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-host", default=DEFAULT_CONFIG.entry_host)
    parser.add_argument("--entry-port", type=int, default=DEFAULT_CONFIG.entry_port)
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--duration", type=float, default=20.0)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    reader, writer = await asyncio.open_connection(args.entry_host, args.entry_port)
    start_ts = time.monotonic()
    sent = 0
    while True:
        if args.count > 0 and sent >= args.count:
            break
        if args.count == 0 and args.duration > 0 and time.monotonic() - start_ts >= args.duration:
            break
        payload = os.urandom(args.size)
        sent += 1
        LOGGER.info("发送第 %s 条（%s 字节）", sent, len(payload))
        writer.write(payload)
        await writer.drain()
        response = await reader.readexactly(len(payload))
        if response == payload:
            LOGGER.info("回显校验通过（第 %s 条）", sent)
        else:
            LOGGER.error("回显校验失败（第 %s 条）", sent)
            break
        await asyncio.sleep(args.interval)
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
