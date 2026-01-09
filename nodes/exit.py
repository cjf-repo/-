from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
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
from strategy import StrategyEngine
from run_context import get_run_context


LOGGER = setup_logger("exit")
ACK_STRUCT = struct.Struct("!Q")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.exit_port)
    return parser.parse_args()


class ExitNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        self.config = config
        self.run_context = get_run_context(config)
        self.proto = ProtoObfuscator()
        base_params = BehaviorParams(
            size_bins=config.size_bins,
            q_dist=[1 / len(config.size_bins) for _ in config.size_bins],
            padding_alpha=config.padding_alpha,
            jitter_ms=config.jitter_ms,
            rate_bytes_per_sec=config.base_rate_bytes_per_sec,
            burst_size=6,
            obfuscation_level=config.obfuscation_level,
            enable_shaping=True,
            enable_padding=True,
            enable_pacing=True,
            enable_jitter=True,
        )
        self.active_middle_ports = (
            [config.middle_ports[0]]
            if config.mode.startswith("baseline")
            else list(config.middle_ports)
        )
        self.behavior = BehaviorShaper(
            base_params,
            path_ids=list(range(len(self.active_middle_ports))),
        )
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(self.active_middle_ports))),
            batch_size=config.batch_size,
        )
        self.strategy = StrategyEngine(
            size_bins=config.size_bins,
            base_padding=config.padding_alpha,
            base_jitter=config.jitter_ms,
            family_ids=[1, 2, 3],
            base_rate=config.base_rate_bytes_per_sec,
            obfuscation_level=config.obfuscation_level,
            mode=config.mode,
            proto_switch_period=config.proto_switch_period,
            adaptive_paths=config.adaptive_paths,
            adaptive_behavior=config.adaptive_behavior,
            adaptive_proto=config.adaptive_proto,
        )
        self.fragment_buffer = FragmentBuffer()
        self.path_writers: Dict[int, asyncio.StreamWriter] = {}
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None
        self._server_lock = asyncio.Lock()
        self._window_task: asyncio.Task | None = None
        self.window_id = 0
        self.family_by_path: Dict[int, int] = {
            path_id: 1 for path_id in range(len(self.active_middle_ports))
        }
        self.variant_by_path: Dict[int, int] = {
            path_id: 0 for path_id in range(len(self.active_middle_ports))
        }

    async def connect_server(self) -> None:
        reader, writer = await asyncio.open_connection(
            self.config.server_host, self.config.server_port
        )
        self.server_reader = reader
        self.server_writer = writer
        LOGGER.info("已连接到目标服务 %s:%s", self.config.server_host, self.config.server_port)

    async def handle_middle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        addr = writer.get_extra_info("peername")
        LOGGER.info("中继节点已连接 %s", addr)
        if self._window_task is None:
            self._window_task = asyncio.create_task(self.start_window_loop())
        try:
            while True:
                frame = await Frame.read_from(reader)
                self.path_writers[frame.path_id] = writer
                if frame.flags & (FLAG_PADDING | FLAG_HANDSHAKE | FLAG_ACK):
                    continue
                frame = self.proto.decode_payload(frame)
                if frame.flags & FLAG_FRAGMENT:
                    complete, payload = self.fragment_buffer.add(frame)
                    if not complete:
                        continue
                    await self.forward_to_server(frame, payload)
                    await self.send_ack(frame)
                else:
                    await self.forward_to_server(frame, frame.payload)
                    await self.send_ack(frame)
        except asyncio.IncompleteReadError:
            LOGGER.info("中继节点已断开 %s", addr)

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
        async with self._server_lock:
            if self.server_writer is None:
                await self.connect_server()
            assert self.server_writer and self.server_reader
            # 连接上游 server 的读写需串行，避免并发 readexactly 冲突
            self.server_writer.write(payload)
            await self.server_writer.drain()
            response = await self.server_reader.readexactly(len(payload))
        await self.send_downlink(frame, response)

    async def send_downlink(self, frame: Frame, data: bytes) -> None:
        remaining = data
        fragments: List[tuple[int, bytes]] = []
        while remaining:
            available_paths = list(self.path_writers.keys())
            if not available_paths:
                return
            # 回程仅在可用路径内调度，避免分片丢失
            path_id = self.scheduler.choose_path_from(available_paths)
            target_len = self.behavior.sample_target_len(path_id)
            params = self.behavior.params_by_path[path_id]
            if not params.enable_shaping:
                target_len = len(remaining)
            piece = remaining[:target_len]
            remaining = remaining[target_len:]
            fragments.append((path_id, piece))
            self.behavior.note_real_bytes(path_id, len(piece))
        total = len(fragments)
        for frag_id, (path_id, payload) in enumerate(fragments):
            writer = self.path_writers.get(path_id)
            if not writer:
                continue
            family_id = self.family_by_path.get(path_id, 1)
            variant_id = self.variant_by_path.get(path_id, 0)
            out_frame = Frame(
                session_id=frame.session_id,
                seq=frame.seq,
                direction=DIR_DOWN,
                path_id=path_id,
                window_id=frame.window_id,
                proto_id=family_id,
                flags=FLAG_FRAGMENT,
                frag_id=frag_id,
                frag_total=total,
                payload=payload,
            )
            out_frame = self.proto.apply(out_frame, family_id, variant_id)
            out_frame = self.proto.encode_payload(out_frame, family_id, variant_id)
            await self.behavior.pace(path_id, len(payload))
            jitter_ms = self.behavior.params_by_path[path_id].jitter_ms
            if self.behavior.params_by_path[path_id].enable_jitter:
                await asyncio.sleep(jitter_ms / 1000 * random.random())
            writer.write(out_frame.encode())
            await writer.drain()
            if self.behavior.update_burst(path_id):
                template = out_frame
                for padding in self.behavior.make_padding_frames(template):
                    writer.write(padding.encode())
                    await writer.drain()

    async def start_window_loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.window_size_sec)
            self.window_id += 1
            metrics = self.scheduler.snapshot()
            output = self.strategy.evaluate(metrics, 0, self.window_id)
            self.scheduler.update_weights(output.weights)
            self.family_by_path = output.family_by_path
            self.variant_by_path = output.variant_by_path
            for path_id, params in output.behavior_by_path.items():
                self.behavior.set_params(path_id, params)
                drift = 0.02 if output.obfuscation_level == 1 else 0.05 if output.obfuscation_level == 2 else 0.08
                if output.obfuscation_level == 0:
                    drift = 0.0
                if output.adaptive_flags["adaptive_behavior"]:
                    self.behavior.update_q_dist(path_id, drift, seed=self.window_id * 100 + path_id)
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id, output.family_by_path, output.variant_by_path)
            for path_id, stats in metrics.items():
                behavior = output.behavior_by_path[path_id]
                pad_bytes = self.behavior.path_states[path_id].padding_bytes
                real_bytes = self.behavior.path_states[path_id].real_bytes
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
                    "real_bytes": real_bytes,
                    "rtt_ms": stats["rtt_ms"],
                    "loss": stats["loss"],
                    "trigger": output.trigger,
                    "action": output.action,
                    "adaptive_flags": output.adaptive_flags,
                }
                self.run_context.write_window_log(log_entry)
                LOGGER.info(json.dumps(log_entry, ensure_ascii=False))


async def main() -> None:
    args = parse_args()
    node = ExitNode(DEFAULT_CONFIG)
    server = await asyncio.start_server(node.handle_middle, DEFAULT_CONFIG.exit_host, args.listen)
    LOGGER.info("出口节点监听 %s:%s", DEFAULT_CONFIG.exit_host, args.listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
