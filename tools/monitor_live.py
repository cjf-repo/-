from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from frames import HEADER_STRUCT
from logger import setup_logger


LOGGER = setup_logger("monitor_live")


@dataclass
class FrameSummary:
    session_id: int
    seq: int
    direction: int
    path_id: int
    window_id: int
    proto_id: int
    flags: int
    frag_id: int
    frag_total: int
    extra_len: int
    payload_len: int


class FrameTap:
    def __init__(self, label: str) -> None:
        self.label = label
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)
        while True:
            summary = self._try_parse_one()
            if summary is None:
                break
            LOGGER.info(
                "%s 帧: session=%s seq=%s dir=%s path=%s window=%s proto=%s flags=0x%02x frag=%s/%s extra=%s payload=%s",
                self.label,
                summary.session_id,
                summary.seq,
                summary.direction,
                summary.path_id,
                summary.window_id,
                summary.proto_id,
                summary.flags,
                summary.frag_id,
                summary.frag_total,
                summary.extra_len,
                summary.payload_len,
            )

    def _try_parse_one(self) -> FrameSummary | None:
        if len(self._buffer) < HEADER_STRUCT.size:
            return None
        header = self._buffer[: HEADER_STRUCT.size]
        (
            session_id,
            seq,
            direction,
            path_id,
            window_id,
            proto_id,
            extra_len,
            frag_id,
            frag_total,
            payload_len,
        ) = HEADER_STRUCT.unpack(header)
        total_len = HEADER_STRUCT.size + extra_len + 1 + payload_len
        if len(self._buffer) < total_len:
            return None
        flags_index = HEADER_STRUCT.size + extra_len
        flags = self._buffer[flags_index]
        del self._buffer[:total_len]
        return FrameSummary(
            session_id=session_id,
            seq=seq,
            direction=direction,
            path_id=path_id,
            window_id=window_id,
            proto_id=proto_id,
            flags=flags,
            frag_id=frag_id,
            frag_total=frag_total,
            extra_len=extra_len,
            payload_len=payload_len,
        )


async def relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    tap: FrameTap,
) -> None:
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            tap.feed(data)
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
    await asyncio.gather(
        relay(reader, upstream_writer, FrameTap("上行")),
        relay(upstream_reader, writer, FrameTap("下行")),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, required=True)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, args.target_host, args.target_port),
        args.listen_host,
        args.listen_port,
    )
    LOGGER.info(
        "监听 %s:%s -> 转发到 %s:%s",
        args.listen_host,
        args.listen_port,
        args.target_host,
        args.target_port,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
