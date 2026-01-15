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

# 出口节点：解码上行分片、转发到目标服务，并回传响应。


LOGGER = setup_logger("exit")
ACK_STRUCT = struct.Struct("!Q")


def parse_args() -> argparse.Namespace:
    # 命令行参数解析
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.exit_port)
    return parser.parse_args()


class ExitNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        # 保存配置与运行上下文
        self.config = config
        self.run_context = get_run_context(config)
        # 协议混淆器
        self.proto = ProtoObfuscator()
        # 基线行为参数
        enable_behavior = config.enable_behavior
        base_params = BehaviorParams(
            size_bins=config.size_bins,
            q_dist=[1 / len(config.size_bins) for _ in config.size_bins],
            padding_alpha=config.padding_alpha,
            jitter_ms=config.jitter_ms,
            rate_bytes_per_sec=config.base_rate_bytes_per_sec,
            burst_size=6,
            obfuscation_level=config.obfuscation_level,
            enable_shaping=enable_behavior,
            enable_padding=enable_behavior,
            enable_pacing=enable_behavior,
            enable_jitter=enable_behavior,
        )
        # baseline 模式仅使用单路径
        if config.mode.startswith("baseline") or not config.enable_multipath:
            self.active_middle_ports = [config.middle_ports[0]]
        else:
            self.active_middle_ports = list(config.middle_ports)
        # 行为整形器
        self.behavior = BehaviorShaper(
            base_params,
            path_ids=list(range(len(self.active_middle_ports))),
        )
        # 多路径调度器
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(self.active_middle_ports))),
            batch_size=config.batch_size,
        )
        # 策略引擎
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
        # 分片缓冲与路径 writer
        self.fragment_buffer = FragmentBuffer()
        self.path_writers: Dict[int, asyncio.StreamWriter] = {}
        self.server_reader: asyncio.StreamReader | None = None
        self.server_writer: asyncio.StreamWriter | None = None
        self._server_lock = asyncio.Lock()
        self._server_conns: Dict[int, tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
        self._server_targets: Dict[int, tuple[str, int]] = {}
        self._server_tunnels: Dict[int, bool] = {}
        self._down_seq_counter: Dict[int, int] = {}
        self._window_task: asyncio.Task | None = None
        self.window_id = 0
        # 协议族/变体映射
        self.family_by_path: Dict[int, int] = {
            path_id: 1 for path_id in range(len(self.active_middle_ports))
        }
        self.variant_by_path: Dict[int, int] = {
            path_id: 0 for path_id in range(len(self.active_middle_ports))
        }

    async def connect_server(self, host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        # 连接目标服务
        reader, writer = await asyncio.open_connection(host, port)
        LOGGER.info("已连接到目标服务 %s:%s", host, port)
        return reader, writer

    async def handle_middle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # 处理中继连接
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
                if self.config.enable_obfuscation:
                    frame = self.proto.decode_payload(frame)
                # 处理分片或完整 payload
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
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
            LOGGER.debug("中继连接已关闭 %s: %s", addr, exc)

    async def send_ack(self, frame: Frame) -> None:
        # 回发 ACK
        writer = self.path_writers.get(frame.path_id)
        if not writer or writer.is_closing():
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
        try:
            writer.write(ack_frame.encode())
            await writer.drain()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
            LOGGER.debug("ACK 发送失败 path %s: %s", frame.path_id, exc)
            self.path_writers.pop(frame.path_id, None)

    async def forward_to_server(self, frame: Frame, payload: bytes) -> None:
        # 与上游服务串行交互，避免 readexactly 冲突
        async with self._server_lock:
            session_id = frame.session_id
            target = self._server_targets.get(session_id, (self.config.server_host, self.config.server_port))
            tunnel_mode = self._server_tunnels.get(session_id, False)
            if session_id not in self._server_conns:
                target, payload, tunnel_mode = self.extract_target(payload, target)
                self._server_targets[session_id] = target
                self._server_tunnels[session_id] = tunnel_mode
                self._server_conns[session_id] = await self.connect_server(*target)
            else:
                payload = self.strip_target_prefix(payload)
            reader, writer = self._server_conns[session_id]
            # 连接上游 server 的读写需串行，避免并发 readexactly 冲突
            if tunnel_mode and not payload:
                return
            try:
                writer.write(payload)
                await writer.drain()
            except (ConnectionResetError, ConnectionAbortedError):
                self._server_conns.pop(session_id, None)
                reader, writer = await self.connect_server(*target)
                self._server_conns[session_id] = (reader, writer)
                writer.write(payload)
                await writer.drain()
            if self.config.server_mode == "echo":
                response = await reader.readexactly(len(payload))
            else:
                response = await self.read_response_stream(reader)
        LOGGER.info(
            "上游请求 %s:%s 发送 %s 字节，收到 %s 字节",
            target[0],
            target[1],
            len(payload),
            len(response),
        )
        if self.config.proxy_mode and self.config.server_mode == "forward" and not tunnel_mode:
            response = f"RESP_LEN {len(response)}\n\n".encode("utf-8") + response
        await self.send_downlink(frame, response)
        if self.config.proxy_mode and self.config.server_mode == "forward" and not tunnel_mode:
            self.close_proxy_session(frame.session_id)

    def close_proxy_session(self, session_id: int) -> None:
        # 代理模式下结束会话：关闭与上游和中继的连接
        conn = self._server_conns.pop(session_id, None)
        self._down_seq_counter.pop(session_id, None)
        if conn:
            reader, writer = conn
            writer.close()
        for writer in self.path_writers.values():
            writer.close()

    def strip_target_prefix(self, payload: bytes) -> bytes:
        if payload.startswith(b"TARGET "):
            marker = payload.find(b"\n\n")
            if marker != -1:
                return payload[marker + 2 :]
        return payload

    def extract_target(
        self, payload: bytes, default_target: tuple[str, int]
    ) -> tuple[tuple[str, int], bytes, bool]:
        if not payload.startswith(b"TARGET "):
            return default_target, payload, False
        marker = payload.find(b"\n\n")
        if marker == -1:
            return default_target, payload, False
        line = payload[:marker].decode("utf-8", errors="ignore")
        rest = payload[marker + 2 :]
        parts = line.split(" ", 2)
        if len(parts) < 2:
            return default_target, rest, False
        host_port = parts[1].strip()
        tunnel_mode = len(parts) >= 3 and parts[2].strip().upper() == "TUNNEL"
        if ":" in host_port:
            host, port_text = host_port.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                port = default_target[1]
        else:
            host = host_port
            port = default_target[1]
        return (host, port), rest, tunnel_mode

    async def read_response_stream(self, reader: asyncio.StreamReader) -> bytes:
        # 读取上游响应流，直到短时间无数据（适配真实 HTTP 响应）
        chunks = bytearray()
        while True:
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            except asyncio.TimeoutError:
                break
            if not data:
                break
            chunks.extend(data)
        return bytes(chunks)

    async def send_downlink(self, frame: Frame, data: bytes) -> None:
        # 下行分片并流式发送
        remaining = memoryview(data)
        max_chunk = max(self.config.size_bins) * max(1, self.config.batch_size)
        seq = self._down_seq_counter.get(frame.session_id, 0)
        while remaining:
            chunk = remaining[:max_chunk]
            remaining = remaining[len(chunk) :]
            fragments: List[tuple[int, bytes]] = []
            pending = bytes(chunk)
            while pending:
                available_paths = list(self.path_writers.keys())
                if not available_paths:
                    self._down_seq_counter[frame.session_id] = seq
                    return
                # 回程仅在可用路径内调度，避免分片丢失
                path_id = self.scheduler.choose_path_from(available_paths)
                target_len = self.behavior.sample_target_len(path_id)
                params = self.behavior.params_by_path[path_id]
                if not params.enable_shaping:
                    target_len = len(pending)
                piece = pending[:target_len]
                pending = pending[target_len:]
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
                    seq=seq,
                    direction=DIR_DOWN,
                    path_id=path_id,
                    window_id=frame.window_id,
                    proto_id=family_id,
                    flags=FLAG_FRAGMENT,
                    frag_id=frag_id,
                    frag_total=total,
                    payload=payload,
                )
                if self.config.enable_obfuscation:
                    out_frame = self.proto.apply(out_frame, family_id, variant_id)
                    out_frame = self.proto.encode_payload(out_frame, family_id, variant_id)
                await self.behavior.pace(path_id, len(payload))
                jitter_ms = self.behavior.params_by_path[path_id].jitter_ms
                if self.behavior.params_by_path[path_id].enable_jitter:
                    await asyncio.sleep(jitter_ms / 1000 * random.random())
                writer.write(out_frame.encode())
                try:
                    await writer.drain()
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
                    LOGGER.debug("下行发送失败 path %s: %s", path_id, exc)
                    self.path_writers.pop(path_id, None)
                    continue
                if self.behavior.update_burst(path_id):
                    template = out_frame
                    for padding in self.behavior.make_padding_frames(template):
                        writer.write(padding.encode())
                        try:
                            await writer.drain()
                        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as exc:
                            LOGGER.debug("填充发送失败 path %s: %s", path_id, exc)
                            self.path_writers.pop(path_id, None)
                            break
            seq += 1
        self._down_seq_counter[frame.session_id] = seq

    async def start_window_loop(self) -> None:
        # 周期性窗口循环：策略更新与日志输出
        while True:
            await asyncio.sleep(self.config.window_size_sec)
            self.window_id += 1
            metrics = self.scheduler.snapshot()
            output = self.strategy.evaluate(metrics, 0, self.window_id)
            # 更新调度与行为参数
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
    # 启动出口节点服务
    args = parse_args()
    node = ExitNode(DEFAULT_CONFIG)
    server = await asyncio.start_server(node.handle_middle, DEFAULT_CONFIG.exit_host, args.listen)
    LOGGER.info("出口节点监听 %s:%s", DEFAULT_CONFIG.exit_host, args.listen)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
