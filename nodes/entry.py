from __future__ import annotations

import argparse
import asyncio
import random
import struct
import time
from typing import Dict, List

from behavior import BehaviorShaper, BehaviorParams
from config import DEFAULT_CONFIG
from frames import (
    DIR_DOWN,
    DIR_UP,
    FLAG_ACK,
    FLAG_FRAGMENT,
    Frame,
    FragmentBuffer,
)
from logger import setup_logger
from obfuscation import ProtoObfuscator
from scheduler import MultiPathScheduler
from strategy import StrategyEngine


LOGGER = setup_logger("entry")
ACK_STRUCT = struct.Struct("!Q")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.entry_port)
    return parser.parse_args()


class EntryNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        self.config = config
        self.session_id = random.randint(1, 2**32 - 1)
        self.window_id = 0
        self.seq_counter = 0
        self.proto = ProtoObfuscator()
        self.behavior = BehaviorShaper(
            BehaviorParams(
                size_bins=config.size_bins,
                padding_alpha=config.padding_alpha,
                jitter_ms=config.jitter_ms,
            )
        )
        self.strategy = StrategyEngine(
            size_bins=config.size_bins,
            base_padding=config.padding_alpha,
            base_jitter=config.jitter_ms,
            proto_ids=[1, 2, 3],
        )
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(config.middle_ports))),
            batch_size=config.batch_size,
        )
        self.timeout_events = 0
        self._window_task: asyncio.Task | None = None
        self._next_down_seq = 0
        self._pending_down: Dict[int, bytes] = {}

    async def connect_paths(self) -> List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        conns = []
        for port in self.config.middle_ports:
            reader, writer = await asyncio.open_connection(self.config.middle_host, port)
            conns.append((reader, writer))
            LOGGER.info("已连接到中继 %s", port)
        return conns

    async def start_window_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.window_size_sec)
            now = time.time()
            for stats in self.scheduler.stats.values():
                # 超时未确认的 seq 计入风险事件
                expired = [seq for seq, ts in stats.last_send_ts.items() if now - ts > self.config.ack_timeout_sec]
                for seq in expired:
                    stats.last_send_ts.pop(seq, None)
                    self.timeout_events += 1
            self.window_id += 1
            metrics = self.scheduler.snapshot()
            output = self.strategy.evaluate(metrics, self.timeout_events)
            self.scheduler.update_weights(output.weights)
            self.behavior.params = output.behavior
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id, output.proto_id)
            LOGGER.info(
                "时间窗 %s 更新：权重=%s 填充=%.3f 抖动=%sms 协议=%s",
                self.window_id,
                output.weights,
                output.behavior.padding_alpha,
                output.behavior.jitter_ms,
                output.proto_id,
            )
            self.timeout_events = 0

    async def send_handshake(self, conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]) -> None:
        for path_id, (_, writer) in enumerate(conns):
            for frame, delay_ms in self.proto.handshake_frames(self.session_id, path_id):
                writer.write(frame.encode())
                await writer.drain()
                await asyncio.sleep(delay_ms / 1000)

    async def handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        addr = writer.get_extra_info("peername")
        LOGGER.info("客户端已连接 %s", addr)
        path_conns = await self.connect_paths()
        await self.send_handshake(path_conns)
        if self._window_task is None:
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id)
            self._window_task = asyncio.create_task(self.start_window_loop())
        self._next_down_seq = 0
        self._pending_down = {}
        fragment_buffer = FragmentBuffer()
        downlink_task = asyncio.create_task(
            self.read_from_paths(path_conns, writer, fragment_buffer)
        )
        try:
            while True:
                data = await reader.read(2048)
                if not data:
                    break
                chunks = self.behavior.shape_payload(data)
                for chunk in chunks:
                    await self.send_chunk(chunk, path_conns)
        except asyncio.IncompleteReadError:
            LOGGER.info("客户端已断开 %s", addr)
        finally:
            downlink_task.cancel()
            writer.close()
            await writer.wait_closed()
            for _, path_writer in path_conns:
                path_writer.close()
                await path_writer.wait_closed()

    async def send_chunk(self, data: bytes, path_conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]) -> None:
        seq = self.seq_counter
        self.seq_counter += 1
        profile = self.proto.pick_profile()
        frame_size = profile.pick_frame_size()
        # 上行按模板粒度拆分成多个分片
        fragments = [data[i : i + frame_size] for i in range(0, len(data), frame_size)]
        total = len(fragments)
        for frag_id, payload in enumerate(fragments):
            path_id = self.scheduler.choose_path()
            frame = Frame(
                session_id=self.session_id,
                seq=seq,
                direction=DIR_UP,
                path_id=path_id,
                window_id=self.window_id,
                proto_id=profile.proto_id,
                flags=FLAG_FRAGMENT,
                frag_id=frag_id,
                frag_total=total,
                payload=payload,
            )
            frame = self.proto.apply(frame)
            frame = self.proto.encode_payload(frame)
            self.scheduler.mark_sent(path_id, seq)
            await asyncio.sleep(self.behavior.params.jitter_ms / 1000 * random.random())
            _, writer = path_conns[path_id]
            writer.write(frame.encode())
            await writer.drain()
            template = frame
            for padding in self.behavior.make_padding_frames(template):
                writer.write(padding.encode())
                await writer.drain()

    async def read_from_paths(
        self,
        path_conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
        client_writer: asyncio.StreamWriter,
        fragment_buffer: FragmentBuffer,
    ) -> None:
        readers = [reader for reader, _ in path_conns]
        tasks = [asyncio.create_task(self.read_path(reader, client_writer, fragment_buffer)) for reader in readers]
        await asyncio.gather(*tasks)

    async def read_path(
        self,
        reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        fragment_buffer: FragmentBuffer,
    ) -> None:
        while True:
            frame = await Frame.read_from(reader)
            if frame.flags & FLAG_ACK:
                seq = ACK_STRUCT.unpack(frame.payload)[0]
                self.scheduler.mark_ack(frame.path_id, seq)
                continue
            if frame.direction != DIR_DOWN:
                continue
            if frame.flags & FLAG_FRAGMENT:
                if not (frame.flags & FLAG_ACK):
                    frame = self.proto.decode_payload(frame)
                complete, payload = fragment_buffer.add(frame)
                if not complete:
                    continue
                await self.enqueue_downlink(frame.seq, payload, client_writer)
            else:
                if not (frame.flags & FLAG_ACK):
                    frame = self.proto.decode_payload(frame)
                await self.enqueue_downlink(frame.seq, frame.payload, client_writer)

    async def enqueue_downlink(
        self,
        seq: int,
        payload: bytes,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        # 按 seq 重排，确保回程数据按顺序交付给 client
        self._pending_down[seq] = payload
        while self._next_down_seq in self._pending_down:
            data = self._pending_down.pop(self._next_down_seq)
            client_writer.write(data)
            await client_writer.drain()
            self._next_down_seq += 1


async def main() -> None:
    args = parse_args()
    node = EntryNode(DEFAULT_CONFIG)
    server = await asyncio.start_server(node.handle_client, DEFAULT_CONFIG.entry_host, args.listen)
    LOGGER.info("入口节点监听 %s:%s", DEFAULT_CONFIG.entry_host, args.listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
