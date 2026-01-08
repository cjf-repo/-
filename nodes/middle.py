from __future__ import annotations

import argparse
import asyncio
import random

from config import DEFAULT_CONFIG, PathConfig
from logger import setup_logger


LOGGER = setup_logger("middle")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, required=True)
    parser.add_argument("--exit-host", default=DEFAULT_CONFIG.exit_host)
    parser.add_argument("--exit-port", type=int, default=DEFAULT_CONFIG.exit_port)
    parser.add_argument("--base-delay", type=int, default=20)
    parser.add_argument("--jitter", type=int, default=10)
    parser.add_argument("--loss", type=float, default=0.0)
    return parser.parse_args()


async def bridge(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dest_reader: asyncio.StreamReader,
    dest_writer: asyncio.StreamWriter,
    config: PathConfig,
) -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            if random.random() < config.loss_rate:
                continue
            delay = config.base_delay_ms + random.randint(0, config.jitter_ms)
            await asyncio.sleep(delay / 1000)
            dest_writer.write(data)
            await dest_writer.drain()
    except ConnectionResetError:
        LOGGER.warning("Connection reset during bridge")
    finally:
        writer.close()
        dest_writer.close()
        await writer.wait_closed()
        await dest_writer.wait_closed()


async def handle_entry(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: PathConfig, exit_host: str, exit_port: int
) -> None:
    addr = writer.get_extra_info("peername")
    LOGGER.info("Entry connected %s", addr)
    exit_reader, exit_writer = await asyncio.open_connection(exit_host, exit_port)
    await asyncio.gather(
        bridge(reader, writer, exit_reader, exit_writer, config),
        bridge(exit_reader, exit_writer, reader, writer, config),
    )


async def main() -> None:
    args = parse_args()
    config = PathConfig(
        host="127.0.0.1",
        port=args.listen,
        base_delay_ms=args.base_delay,
        jitter_ms=args.jitter,
        loss_rate=args.loss,
    )
    server = await asyncio.start_server(
        lambda r, w: handle_entry(r, w, config, args.exit_host, args.exit_port),
        "127.0.0.1",
        args.listen,
    )
    LOGGER.info("Middle listening on %s -> exit %s:%s", args.listen, args.exit_host, args.exit_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
