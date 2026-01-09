from __future__ import annotations

import argparse
import asyncio
import json
import random
import struct
import time
from dataclasses import replace
from typing import Dict, List

from behavior import BehaviorShaper, BehaviorParams
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
from strategy import StrategyEngine


LOGGER = setup_logger("entry")
ACK_STRUCT = struct.Struct("!Q")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.entry_port)
    parser.add_argument("--middle-ports", default="", help="覆盖中继端口列表，例如 9103,9102")
    return parser.parse_args()


class EntryNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        self.config = config
        self.session_id = random.randint(1, 2**32 - 1)
        self.window_id = 0
        self.seq_counter = 0
        self.proto = ProtoObfuscator()
        base_params = BehaviorParams(
            size_bins=config.size_bins,
            q_dist=[1 / len(config.size_bins) for _ in config.size_bins],
            padding_alpha=config.padding_alpha,
            jitter_ms=config.jitter_ms,
            rate_bytes_per_sec=config.base_rate_bytes_per_sec,
            burst_size=6,
            obfuscation_level=config.obfuscation_level,
        )
        self.behavior = BehaviorShaper(
            base_params,
            path_ids=list(range(len(config.middle_ports))),
        )
        self.strategy = StrategyEngine(
            size_bins=config.size_bins,
            base_padding=config.padding_alpha,
            base_jitter=config.jitter_ms,
            family_ids=[1, 2, 3],
            base_rate=config.base_rate_bytes_per_sec,
            obfuscation_level=config.obfuscation_level,
        )
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(config.middle_ports))),
            batch_size=config.batch_size,
        )
        self.timeout_events = 0
        self._window_task: asyncio.Task | None = None
        self._next_down_seq = 0
        self._pending_down: Dict[int, bytes] = {}
        self.family_by_path: Dict[int, int] = {
            path_id: 1 for path_id in range(len(config.middle_ports))
        }
        self.variant_by_path: Dict[int, int] = {
            path_id: 0 for path_id in range(len(config.middle_ports))
        }

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
            self.family_by_path = output.family_by_path
            self.variant_by_path = output.variant_by_path
            for path_id, params in output.behavior_by_path.items():
                self.behavior.set_params(path_id, params)
                drift = 0.02 if output.obfuscation_level == 1 else 0.05 if output.obfuscation_level == 2 else 0.08
                if output.obfuscation_level == 0:
                    drift = 0.0
                self.behavior.update_q_dist(path_id, drift, seed=self.window_id * 100 + path_id)
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id, output.family_by_path, output.variant_by_path)
            for path_id, stats in metrics.items():
                behavior = output.behavior_by_path[path_id]
                pad_bytes = self.behavior.path_states[path_id].padding_bytes
                log_entry = {
                    "window_id": self.window_id,
                    "path_id": path_id,
                    "obfuscation_level": output.obfuscation_level,
                    "alpha_padding": behavior.padding_alpha,
                    "rate_bytes_per_sec": behavior.rate_bytes_per_sec,
                    "jitter_ms": behavior.jitter_ms,
                    "proto_family": output.family_by_path[path_id],
                    "proto_variant": output.variant_by_path[path_id],
                    "padding_bytes": pad_bytes,
                    "rtt_ms": stats["rtt_ms"],
                    "loss": stats["loss"],
                }
                LOGGER.info(json.dumps(log_entry, ensure_ascii=False))
            self.timeout_events = 0

    async def send_handshake(self, conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]) -> None:
        for path_id, (_, writer) in enumerate(conns):
            family_id = self.family_by_path.get(path_id, 1)
            variant_id = self.variant_by_path.get(path_id, 0)
            for frame, delay_ms in self.proto.handshake_frames(
                self.session_id, path_id, family_id, variant_id
            ):
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
            self.proto.start_window(self.window_id, self.family_by_path, self.variant_by_path)
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
                await self.send_chunk(data, path_conns)
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
        remaining = data
        fragments: List[tuple[int, bytes]] = []
        while remaining:
            path_id = self.scheduler.choose_path()
            target_len = self.behavior.sample_target_len(path_id)
            piece = remaining[:target_len]
            remaining = remaining[target_len:]
            fragments.append((path_id, piece))
            self.behavior.note_real_bytes(path_id, len(piece))
        total = len(fragments)
        for frag_id, (path_id, payload) in enumerate(fragments):
            family_id = self.family_by_path.get(path_id, 1)
            variant_id = self.variant_by_path.get(path_id, 0)
            frame = Frame(
                session_id=self.session_id,
                seq=seq,
                direction=DIR_UP,
                path_id=path_id,
                window_id=self.window_id,
                proto_id=family_id,
                flags=FLAG_FRAGMENT,
                frag_id=frag_id,
                frag_total=total,
                payload=payload,
            )
            frame = self.proto.apply(frame, family_id, variant_id)
            frame = self.proto.encode_payload(frame, family_id, variant_id)
            self.scheduler.mark_sent(path_id, seq)
            await self.behavior.pace(path_id, len(payload))
            jitter_ms = self.behavior.params_by_path[path_id].jitter_ms
            await asyncio.sleep(jitter_ms / 1000 * random.random())
            _, writer = path_conns[path_id]
            writer.write(frame.encode())
            await writer.drain()
            if self.behavior.update_burst(path_id):
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
            if frame.flags & (FLAG_PADDING | FLAG_HANDSHAKE):
                continue
            if frame.direction != DIR_DOWN:
                continue
            if frame.flags & FLAG_FRAGMENT:
                if not (frame.flags & (FLAG_ACK | FLAG_HANDSHAKE | FLAG_PADDING)):
                    frame = self.proto.decode_payload(frame)
                complete, payload = fragment_buffer.add(frame)
                if not complete:
                    continue
                await self.enqueue_downlink(frame.seq, payload, client_writer)
            else:
                if not (frame.flags & (FLAG_ACK | FLAG_HANDSHAKE | FLAG_PADDING)):
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
    config = DEFAULT_CONFIG
    if args.middle_ports:
        ports = [int(port.strip()) for port in args.middle_ports.split(",") if port.strip()]
        config = replace(DEFAULT_CONFIG, middle_ports=ports)
    node = EntryNode(config)
    server = await asyncio.start_server(node.handle_client, config.entry_host, args.listen)
    LOGGER.info("入口节点监听 %s:%s", config.entry_host, args.listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
