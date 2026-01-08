from __future__ import annotations

import argparse
import asyncio
import random
import struct
from typing import Dict, List

from behavior import BehaviorParams, BehaviorShaper
from config import DEFAULT_CONFIG
from frames import (
    DIR_DOWN,
    DIR_UP,
    FLAG_ACK,
    FLAG_FRAGMENT,
    FLAG_HANDSHAKE,
    FLAG_PADDING,
    Frame,
    FragmentBuffer,
)
from logger import setup_logger
from obfuscation import ProtoObfuscator
from scheduler import MultiPathScheduler


LOGGER = setup_logger("exit")
ACK_STRUCT = struct.Struct("!Q")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.exit_port)
    return parser.parse_args()


class ExitNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        self.config = config
        self.proto = ProtoObfuscator()
        self.behavior = BehaviorShaper(
            BehaviorParams(
                size_bins=config.size_bins,
                padding_alpha=config.padding_alpha,
                jitter_ms=config.jitter_ms,
            )
        )
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(config.middle_ports))),
            batch_size=config.batch_size,
        )
        self.fragment_buffer = FragmentBuffer()
        self.path_writers: Dict[int, asyncio.StreamWriter] = {}
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None

    async def connect_server(self) -> None:
        reader, writer = await asyncio.open_connection(
            self.config.server_host, self.config.server_port
        )
        self.server_reader = reader
        self.server_writer = writer
        LOGGER.info("Connected to server %s:%s", self.config.server_host, self.config.server_port)

    async def handle_middle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        LOGGER.info("Middle connected %s", addr)
        while True:
            frame = await Frame.read_from(reader)
            self.path_writers[frame.path_id] = writer
            if frame.flags & (FLAG_PADDING | FLAG_HANDSHAKE):
                continue
            if frame.flags & FLAG_FRAGMENT:
                complete, payload = self.fragment_buffer.add(frame)
                if not complete:
                    continue
                await self.forward_to_server(frame, payload)
                await self.send_ack(frame)
            else:
                await self.forward_to_server(frame, frame.payload)
                await self.send_ack(frame)

    async def send_ack(self, frame: Frame) -> None:
        writer = self.path_writers.get(frame.path_id)
        if not writer:
            return
        ack_frame = Frame(
            session_id=frame.session_id,
            seq=frame.seq,
            direction=DIR_DOWN,
            path_id=frame.path_id,
            window_id=frame.window_id,
            proto_id=frame.proto_id,
            flags=FLAG_ACK,
            frag_id=0,
            frag_total=1,
            payload=ACK_STRUCT.pack(frame.seq),
        )
        writer.write(ack_frame.encode())
        await writer.drain()

    async def forward_to_server(self, frame: Frame, payload: bytes) -> None:
        if self.server_writer is None:
            await self.connect_server()
        assert self.server_writer and self.server_reader
        self.server_writer.write(payload)
        await self.server_writer.drain()
        response = await self.server_reader.readexactly(len(payload))
        await self.send_downlink(frame, response)

    async def send_downlink(self, frame: Frame, data: bytes) -> None:
        profile = self.proto.pick_profile()
        frame_size = profile.pick_frame_size()
        fragments = [data[i : i + frame_size] for i in range(0, len(data), frame_size)]
        total = len(fragments)
        for frag_id, payload in enumerate(fragments):
            path_id = self.scheduler.choose_path()
            writer = self.path_writers.get(path_id)
            if not writer:
                continue
            out_frame = Frame(
                session_id=frame.session_id,
                seq=frame.seq,
                direction=DIR_DOWN,
                path_id=path_id,
                window_id=frame.window_id,
                proto_id=profile.proto_id,
                flags=FLAG_FRAGMENT,
                frag_id=frag_id,
                frag_total=total,
                payload=payload,
            )
            out_frame = self.proto.apply(out_frame)
            await asyncio.sleep(self.behavior.params.jitter_ms / 1000 * random.random())
            writer.write(out_frame.encode())
            await writer.drain()


async def main() -> None:
    args = parse_args()
    node = ExitNode(DEFAULT_CONFIG)
    server = await asyncio.start_server(node.handle_middle, DEFAULT_CONFIG.exit_host, args.listen)
    LOGGER.info("Exit listening on %s:%s", DEFAULT_CONFIG.exit_host, args.listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
