from __future__ import annotations

import argparse
import asyncio
import csv
import time
import random

from config import DEFAULT_CONFIG, PathConfig
from logger import setup_logger
from run_context import get_run_context
from frames import HEADER_STRUCT

# 中继节点：模拟路径时延/丢包并生成 trace。


LOGGER = setup_logger("middle")


def parse_args() -> argparse.Namespace:
    # 命令行参数解析
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", type=int, required=True)
    parser.add_argument("--exit-host", default=DEFAULT_CONFIG.exit_host)
    parser.add_argument("--exit-port", type=int, default=DEFAULT_CONFIG.exit_port)
    parser.add_argument("--base-delay", type=int, default=20)
    parser.add_argument("--jitter", type=int, default=10)
    parser.add_argument("--loss", type=float, default=0.0)
    parser.add_argument("--path-id", type=int, default=-1)
    return parser.parse_args()


class TraceWriter:
    def __init__(self, run_context, path_id: int) -> None:
        # 按路径/会话维护 trace 文件句柄
        self.run_context = run_context
        self.path_id = path_id
        self.buffers: dict[int, bytearray] = {}
        self.session_start: dict[int, float] = {}
        self.writers: dict[tuple[int, str], csv.writer] = {}
        self.handles: dict[tuple[int, str], object] = {}

    def _writer_for(self, session_id: int, trace_type: str) -> csv.writer:
        # 获取指定会话/类型的 trace writer
        key = (session_id, trace_type)
        if key in self.writers:
            return self.writers[key]
        filename = f"trace_session_{session_id}_path_{self.path_id}_{trace_type}.csv"
        path = self.run_context.traces_dir / filename
        handle = path.open("a", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        if path.stat().st_size == 0:
            writer.writerow(["t", "dir", "len"])
        self.handles[key] = handle
        self.writers[key] = writer
        return writer

    def _attacker_writer(self, session_id: int, trace_type: str) -> csv.writer:
        # 获取攻击者视角 trace writer
        key = (session_id, f"attacker_{trace_type}")
        if key in self.writers:
            return self.writers[key]
        filename = f"trace_session_{session_id}_attacker_{trace_type}.csv"
        path = self.run_context.traces_dir / filename
        handle = path.open("a", newline="", encoding="utf-8")
        writer = csv.writer(handle)
        if path.stat().st_size == 0:
            writer.writerow(["t", "dir", "len"])
        self.handles[key] = handle
        self.writers[key] = writer
        return writer

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()

    def feed(self, data: bytes, direction: str) -> None:
        # 攻击者单点观测：仅从 Entry->Middle 方向采样，输出 TM1/TM2
        buffer = self.buffers.setdefault(self.path_id, bytearray())
        buffer.extend(data)
        while True:
            if len(buffer) < HEADER_STRUCT.size:
                return
            header = buffer[: HEADER_STRUCT.size]
            (
                session_id,
                seq,
                dir_flag,
                path_id,
                window_id,
                proto_id,
                extra_len,
                frag_id,
                frag_total,
                payload_len,
            ) = HEADER_STRUCT.unpack(header)
            total_len = HEADER_STRUCT.size + extra_len + 1 + payload_len
            if len(buffer) < total_len:
                return
            del buffer[:total_len]
            now = time.time()
            start = self.session_start.setdefault(session_id, now)
            t = now - start
            tm1 = self._writer_for(session_id, "TM1")
            tm2 = self._writer_for(session_id, "TM2")
            # TM1: TCP/IP 可见长度；TM2: 隧道帧总长度（不含 payload 内容）
            tm1.writerow([f"{t:.6f}", direction, total_len])
            tm2.writerow([f"{t:.6f}", direction, total_len])
            if self.path_id == self.run_context.attacker_path_id:
                attacker_tm1 = self._attacker_writer(session_id, "TM1")
                attacker_tm2 = self._attacker_writer(session_id, "TM2")
                attacker_tm1.writerow([f"{t:.6f}", direction, total_len])
                attacker_tm2.writerow([f"{t:.6f}", direction, total_len])


async def bridge(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    dest_reader: asyncio.StreamReader,
    dest_writer: asyncio.StreamWriter,
    config: PathConfig,
    trace: TraceWriter | None,
    direction: str,
) -> None:
    # 双向转发：模拟丢包/延迟并记录 trace
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            if trace is not None:
                trace.feed(data, direction)
            if random.random() < config.loss_rate:
                continue
            delay = config.base_delay_ms + random.randint(0, config.jitter_ms)
            await asyncio.sleep(delay / 1000)
            dest_writer.write(data)
            await dest_writer.drain()
    except ConnectionResetError:
        LOGGER.warning("转发链路发生连接重置")
    finally:
        # 关闭双向连接
        writer.close()
        dest_writer.close()
        await writer.wait_closed()
        await dest_writer.wait_closed()


async def handle_entry(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: PathConfig,
    exit_host: str,
    exit_port: int,
    path_id: int,
) -> None:
    # 处理入口连接并建立到出口的转发
    addr = writer.get_extra_info("peername")
    LOGGER.info("入口节点已连接 %s", addr)
    run_context = get_run_context(DEFAULT_CONFIG)
    trace = TraceWriter(run_context, path_id)
    exit_reader, exit_writer = await asyncio.open_connection(exit_host, exit_port)
    await asyncio.gather(
        bridge(reader, writer, exit_reader, exit_writer, config, trace, "up"),
        bridge(exit_reader, exit_writer, reader, writer, config, None, "down"),
    )


async def main() -> None:
    # 启动中继节点服务
    args = parse_args()
    config = PathConfig(
        host="127.0.0.1",
        port=args.listen,
        base_delay_ms=args.base_delay,
        jitter_ms=args.jitter,
        loss_rate=args.loss,
    )
    path_id = args.path_id if args.path_id >= 0 else DEFAULT_CONFIG.middle_ports.index(args.listen)
    server = await asyncio.start_server(
        lambda r, w: handle_entry(r, w, config, args.exit_host, args.exit_port, path_id),
        "127.0.0.1",
        args.listen,
    )
    LOGGER.info("中继节点监听 %s -> 出口 %s:%s", args.listen, args.exit_host, args.exit_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
