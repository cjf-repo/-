from __future__ import annotations

import argparse
import asyncio
import os

from config import DEFAULT_CONFIG
from logger import setup_logger


LOGGER = setup_logger("client_app")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-host", default=DEFAULT_CONFIG.entry_host)
    parser.add_argument("--entry-port", type=int, default=DEFAULT_CONFIG.entry_port)
    parser.add_argument("--size", type=int, default=4096)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    reader, writer = await asyncio.open_connection(args.entry_host, args.entry_port)
    payload = os.urandom(args.size)
    LOGGER.info("发送 %s 字节", len(payload))
    writer.write(payload)
    await writer.drain()
    response = await reader.readexactly(len(payload))
    if response == payload:
        LOGGER.info("回显校验通过（%s 字节）", len(response))
    else:
        LOGGER.error("回显校验失败")
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
