from __future__ import annotations

import argparse
import asyncio
import json
import random
import struct
import time
from urllib.parse import urlsplit
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
from run_context import get_run_context
from strategy import StrategyEngine


LOGGER = setup_logger("entry")
ACK_STRUCT = struct.Struct("!Q")

# 入口节点：接收客户端流量，分片并在多路径上发送。

def parse_args() -> argparse.Namespace:
    # 命令行参数解析
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, default=DEFAULT_CONFIG.entry_port)
    parser.add_argument("--middle-ports", default="", help="覆盖中继端口列表，例如 9103,9102")
    return parser.parse_args()


class EntryNode:
    def __init__(self, config=DEFAULT_CONFIG) -> None:
        # 保存配置与上下文
        self.config = config
        self.run_context = get_run_context(config)
        # 会话/窗口状态
        self.session_id = random.randint(1, 2**32 - 1)
        self.window_id = 0
        self.seq_counter = 0
        # 协议混淆器
        self.proto = ProtoObfuscator()
        # 基线行为参数
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
        # baseline 模式仅使用单路径
        self.active_middle_ports = (
            [config.middle_ports[0]]
            if config.mode.startswith("baseline")
            else list(config.middle_ports)
        )
        # 行为整形器
        self.behavior = BehaviorShaper(
            base_params,
            path_ids=list(range(len(self.active_middle_ports))),
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
        # 多路径调度器
        self.scheduler = MultiPathScheduler(
            path_ids=list(range(len(self.active_middle_ports))),
            batch_size=config.batch_size,
        )
        # 超时事件计数
        self.timeout_events = 0
        self._window_task: asyncio.Task | None = None
        self._next_down_seq = 0
        self._pending_down: Dict[int, bytes] = {}
        self._pending_down_ts: Dict[int, float] = {}
        self._next_down_wait_ts: float | None = None
        self._downlink_state: dict | None = None
        # 协议族/变体映射
        self.family_by_path: Dict[int, int] = {
            path_id: 1 for path_id in range(len(self.active_middle_ports))
        }
        self.variant_by_path: Dict[int, int] = {
            path_id: 0 for path_id in range(len(self.active_middle_ports))
        }

    async def connect_paths(self) -> List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
        # 连接所有中继路径
        conns = []
        for port in self.active_middle_ports:
            reader, writer = await asyncio.open_connection(self.config.middle_host, port)
            conns.append((reader, writer))
            LOGGER.info("已连接到中继 %s", port)
        return conns

    async def start_window_loop(self) -> None:
        # 周期性窗口循环：评估策略并写日志
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
            output = self.strategy.evaluate(metrics, self.timeout_events, self.window_id)
            # 更新调度权重与行为参数
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
            # 新窗口开始
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id, output.family_by_path, output.variant_by_path)
            for path_id, stats in metrics.items():
                # 记录窗口日志（用于离线分析）
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
            downlink_state = self._downlink_state
            if downlink_state is not None:
                self._handle_missing_downlink(
                    downlink_state["client_writer"],
                    downlink_state["close_after_response"],
                )
                await self._deliver_pending_downlink(
                    downlink_state["client_writer"],
                    downlink_state["bytes_to_client"],
                    downlink_state["close_after_response"],
                    downlink_state["expected_response_len"],
                    downlink_state["delivered_response_len"],
                )
            self.timeout_events = 0

    async def send_handshake(self, conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]) -> None:
        # 发送握手帧，建立协议上下文
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
        # 处理客户端连接
        addr = writer.get_extra_info("peername")
        LOGGER.info("客户端已连接 %s", addr)
        self.session_id = random.randint(1, 2**32 - 1)
        self.seq_counter = 0
        self.scheduler.reset_stats()
        path_conns = await self.connect_paths()
        await self.send_handshake(path_conns)
        proxy_buffer = bytearray()
        proxy_target_sent = False
        tunnel_mode = False
        bytes_from_client = 0
        bytes_to_client = [0]
        close_after_response = [False]
        expected_response_len = [None]
        delivered_response_len = [0]
        if self._window_task is None:
            self.behavior.start_window(self.window_id)
            self.proto.start_window(self.window_id, self.family_by_path, self.variant_by_path)
            self._window_task = asyncio.create_task(self.start_window_loop())
        self._next_down_seq = 0
        self._pending_down = {}
        self._pending_down_ts = {}
        self._next_down_wait_ts = None
        self._downlink_state = {
            "client_writer": writer,
            "bytes_to_client": bytes_to_client,
            "close_after_response": close_after_response,
            "expected_response_len": expected_response_len,
            "delivered_response_len": delivered_response_len,
        }
        fragment_buffer = FragmentBuffer()
        downlink_task = asyncio.create_task(
            self.read_from_paths(
                path_conns,
                writer,
                fragment_buffer,
                bytes_to_client,
                close_after_response,
                expected_response_len,
                delivered_response_len,
            )
        )
        try:
            while True:
                if close_after_response[0]:
                    break
                data = await reader.read(2048)
                if not data:
                    break
                bytes_from_client += len(data)
                if self.config.proxy_mode and not proxy_target_sent:
                    proxy_buffer.extend(data)
                    header_end = proxy_buffer.find(b"\r\n\r\n")
                    if header_end == -1:
                        continue
                    header_end += 4
                    header = bytes(proxy_buffer[:header_end])
                    body = bytes(proxy_buffer[header_end:])
                    proxy_buffer.clear()
                    target, rewritten, error_response, is_connect = self.parse_proxy_request(header)
                    if error_response is not None:
                        writer.write(error_response)
                        await writer.drain()
                        LOGGER.error("代理请求解析失败，关闭连接")
                        break
                    if target is None or rewritten is None:
                        LOGGER.error("代理请求解析失败，关闭连接")
                        break
                    host, port = target
                    prefix_suffix = " TUNNEL" if is_connect else ""
                    prefix = f"TARGET {host}:{port}{prefix_suffix}\n\n".encode("utf-8")
                    if is_connect:
                        await self.send_chunk(prefix, path_conns)
                        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                        await writer.drain()
                        tunnel_mode = True
                    else:
                        await self.send_chunk(prefix + rewritten + body, path_conns)
                    proxy_target_sent = True
                else:
                    if tunnel_mode:
                        await self.send_chunk(data, path_conns)
                    else:
                        await self.send_chunk(data, path_conns)
        except asyncio.IncompleteReadError:
            LOGGER.info("客户端已断开 %s", addr)
        finally:
            downlink_task.cancel()
            writer.close()
            await writer.wait_closed()
            self._downlink_state = None
            for _, path_writer in path_conns:
                path_writer.close()
                await path_writer.wait_closed()
            LOGGER.info(
                "代理连接关闭 %s，收到 %s 字节，返回 %s 字节",
                addr,
                bytes_from_client,
                bytes_to_client[0],
            )

    def parse_proxy_request(
        self, header: bytes
    ) -> tuple[tuple[str, int] | None, bytes | None, bytes | None, bool]:
        try:
            text = header.decode("iso-8859-1")
        except UnicodeDecodeError:
            return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
        lines = text.split("\r\n")
        if not lines:
            return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) < 2:
            return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
        method, target = parts[0], parts[1]
        host = None
        port = 80
        is_connect = method.upper() == "CONNECT"
        if is_connect:
            if ":" not in target:
                return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
            host, port_text = target.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
            return (host, port), b"", None, True
        if target.startswith("http://"):
            parsed = urlsplit(target)
            if not parsed.hostname:
                return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
            host = parsed.hostname
            port = parsed.port or 80
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            lines[0] = f"{method} {path} HTTP/1.1"
        else:
            for line in lines[1:]:
                if line.lower().startswith("host:"):
                    value = line.split(":", 1)[1].strip()
                    if ":" in value:
                        host_part, port_part = value.rsplit(":", 1)
                        host = host_part
                        try:
                            port = int(port_part)
                        except ValueError:
                            port = 80
                    else:
                        host = value
                    break
        if host is None:
            return None, None, b"HTTP/1.1 400 Bad Request\r\n\r\n", False
        rewritten = "\r\n".join(lines).encode("iso-8859-1")
        return (host, port), rewritten, None, False

    async def send_chunk(self, data: bytes, path_conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]]) -> None:
        # 将上行 payload 分片并发送
        seq = self.seq_counter
        self.seq_counter += 1
        remaining = data
        fragments: List[tuple[int, bytes]] = []
        while remaining:
            # 路径调度 + 按路径目标分布选择分片长度
            path_id = self.scheduler.choose_path()
            params = self.behavior.params_by_path[path_id]
            target_len = len(remaining) if not params.enable_shaping else self.behavior.sample_target_len(path_id)
            piece = remaining[:target_len]
            remaining = remaining[target_len:]
            fragments.append((path_id, piece))
            self.behavior.note_real_bytes(path_id, len(piece))
        total = len(fragments)
        for frag_id, (path_id, payload) in enumerate(fragments):
            # 为每个分片构建帧并发送
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
            if self.behavior.params_by_path[path_id].enable_jitter:
                await asyncio.sleep(jitter_ms / 1000 * random.random())
            _, writer = path_conns[path_id]
            writer.write(frame.encode())
            await writer.drain()
            if self.behavior.update_burst(path_id):
                template = frame
                for padding in self.behavior.make_padding_frames(template):
                    writer.write(padding.encode())
                    await writer.drain()

    def _reset_missing_timer(self) -> None:
        candidates = [
            ts for seq, ts in self._pending_down_ts.items() if seq > self._next_down_seq
        ]
        self._next_down_wait_ts = min(candidates) if candidates else None

    def _close_client_once(
        self,
        client_writer: asyncio.StreamWriter,
        close_after_response: List[bool],
    ) -> None:
        if close_after_response[0]:
            return
        close_after_response[0] = True
        if not client_writer.is_closing():
            client_writer.close()

    def _handle_missing_downlink(
        self,
        client_writer: asyncio.StreamWriter,
        close_after_response: List[bool],
    ) -> None:
        now = time.time()
        while (
            self._next_down_seq not in self._pending_down
            and self._next_down_wait_ts is not None
            and now - self._next_down_wait_ts > self.config.ack_timeout_sec
        ):
            missing_seq = self._next_down_seq
            LOGGER.warning("下行 seq %s 超时未到达，跳过", missing_seq)
            if self.config.proxy_mode:
                LOGGER.warning("代理模式下关闭客户端连接并清空下行缓存")
                self._close_client_once(client_writer, close_after_response)
                self._pending_down.clear()
                self._pending_down_ts.clear()
                self._next_down_wait_ts = None
                return
            self._next_down_seq += 1
            self._reset_missing_timer()

    async def _deliver_pending_downlink(
        self,
        client_writer: asyncio.StreamWriter,
        bytes_to_client: List[int],
        close_after_response: List[bool],
        expected_response_len: List[int | None],
        delivered_response_len: List[int],
    ) -> None:
        while self._next_down_seq in self._pending_down:
            data = self._pending_down.pop(self._next_down_seq)
            self._pending_down_ts.pop(self._next_down_seq, None)
            if self.config.proxy_mode and expected_response_len[0] is None:
                marker = data.find(b"\n\n")
                if data.startswith(b"RESP_LEN ") and marker != -1:
                    header = data[:marker].decode("utf-8", errors="ignore")
                    _, value = header.split(" ", 1)
                    try:
                        expected_response_len[0] = int(value.strip())
                    except ValueError:
                        expected_response_len[0] = None
                    data = data[marker + 2 :]
            client_writer.write(data)
            await client_writer.drain()
            if self.config.proxy_mode:
                bytes_to_client[0] += len(data)
                delivered_response_len[0] += len(data)
                if (
                    expected_response_len[0] is not None
                    and delivered_response_len[0] >= expected_response_len[0]
                    and not close_after_response[0]
                ):
                    self._close_client_once(client_writer, close_after_response)
            self._next_down_seq += 1
        self._reset_missing_timer()

    async def read_from_paths(
        self,
        path_conns: List[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
        client_writer: asyncio.StreamWriter,
        fragment_buffer: FragmentBuffer,
        bytes_to_client: List[int],
        close_after_response: List[bool],
        expected_response_len: List[int | None],
        delivered_response_len: List[int],
    ) -> None:
        # 并发读取各路径下行数据
        readers = [reader for reader, _ in path_conns]
        tasks = [
            asyncio.create_task(
                self.read_path(
                    reader,
                    path_id,
                    client_writer,
                    fragment_buffer,
                    bytes_to_client,
                    close_after_response,
                    expected_response_len,
                    delivered_response_len,
                )
            )
            for path_id, reader in enumerate(readers)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for path_id, result in enumerate(results):
            if isinstance(result, Exception):
                LOGGER.warning("路径读取任务异常退出 path=%s error=%s", path_id, result)
        if not close_after_response[0]:
            close_after_response[0] = True
            client_writer.close()

    async def read_path(
        self,
        reader: asyncio.StreamReader,
        path_id: int,
        client_writer: asyncio.StreamWriter,
        fragment_buffer: FragmentBuffer,
        bytes_to_client: List[int],
        close_after_response: List[bool],
        expected_response_len: List[int | None],
        delivered_response_len: List[int],
    ) -> None:
        # 单路径读取并处理下行帧
        while True:
            try:
                frame = await Frame.read_from(reader)
            except (
                asyncio.IncompleteReadError,
                ConnectionResetError,
                ConnectionAbortedError,
            ) as exc:
                peer = None
                if reader._transport is not None:
                    peer = reader._transport.get_extra_info("peername")
                LOGGER.info("路径已断开 path=%s peer=%s error=%s", path_id, peer, exc)
                break
            if frame.flags & FLAG_ACK:
                seq = ACK_STRUCT.unpack(frame.payload)[0]
                self.scheduler.mark_ack(frame.path_id, seq)
                continue
            if frame.flags & (FLAG_PADDING | FLAG_HANDSHAKE):
                continue
            if frame.direction != DIR_DOWN:
                continue
            if frame.flags & FLAG_FRAGMENT:
                # 分片重组
                if not (frame.flags & (FLAG_ACK | FLAG_HANDSHAKE | FLAG_PADDING)):
                    frame = self.proto.decode_payload(frame)
                complete, payload = fragment_buffer.add(frame)
                if not complete:
                    continue
                await self.enqueue_downlink(
                    frame.seq,
                    payload,
                    client_writer,
                    bytes_to_client,
                    close_after_response,
                    expected_response_len,
                    delivered_response_len,
                )
            else:
                # 完整 payload 直接入队
                if not (frame.flags & (FLAG_ACK | FLAG_HANDSHAKE | FLAG_PADDING)):
                    frame = self.proto.decode_payload(frame)
                await self.enqueue_downlink(
                    frame.seq,
                    frame.payload,
                    client_writer,
                    bytes_to_client,
                    close_after_response,
                    expected_response_len,
                    delivered_response_len,
                )

    async def enqueue_downlink(
        self,
        seq: int,
        payload: bytes,
        client_writer: asyncio.StreamWriter,
        bytes_to_client: List[int],
        close_after_response: List[bool],
        expected_response_len: List[int | None],
        delivered_response_len: List[int],
    ) -> None:
        # 按 seq 重排，确保回程数据按顺序交付给 client
        if seq not in self._pending_down_ts:
            self._pending_down_ts[seq] = time.time()
        self._pending_down[seq] = payload
        if seq > self._next_down_seq and self._next_down_wait_ts is None:
            self._next_down_wait_ts = self._pending_down_ts[seq]
        self._handle_missing_downlink(client_writer, close_after_response)
        await self._deliver_pending_downlink(
            client_writer,
            bytes_to_client,
            close_after_response,
            expected_response_len,
            delivered_response_len,
        )


async def main() -> None:
    # 启动入口节点服务
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
