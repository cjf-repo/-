from __future__ import annotations

import argparse
import asyncio
import os
import time

from config import DEFAULT_CONFIG
from logger import setup_logger
from run_context import get_run_context

# 客户端应用：周期性发送 payload，并记录回显延迟。


LOGGER = setup_logger("client_app")


def parse_args() -> argparse.Namespace:
    # 命令行参数定义
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-host", default=DEFAULT_CONFIG.entry_host)
    parser.add_argument("--entry-port", type=int, default=DEFAULT_CONFIG.entry_port)
    parser.add_argument("--size", type=int, default=4096)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--duration", type=float, default=20.0)
    return parser.parse_args()


async def main() -> None:
    # 解析参数与环境变量覆盖
    args = parse_args()
    env_count = os.environ.get("SESSION_COUNT")
    if env_count is not None:
        args.count = int(env_count)
    env_duration = os.environ.get("SESSION_DURATION")
    if env_duration is not None:
        args.duration = float(env_duration)
    # 初始化运行上下文（用于写日志）
    run_context = get_run_context(DEFAULT_CONFIG)
    # 连接入口节点
    reader, writer = await asyncio.open_connection(args.entry_host, args.entry_port)
    start_ts = time.monotonic()
    sent = 0
    while True:
        # 满足 count 或 duration 结束条件则退出
        if args.count > 0 and sent >= args.count:
            break
        if args.count == 0 and args.duration > 0 and time.monotonic() - start_ts >= args.duration:
            break
        # 生成随机 payload
        payload = os.urandom(args.size)
        sent += 1
        LOGGER.info("发送第 %s 条（%s 字节）", sent, len(payload))
        # 发送并记录延迟
        send_ts = time.monotonic()
        writer.write(payload)
        await writer.drain()
        response = await reader.readexactly(len(payload))
        recv_ts = time.monotonic()
        latency_ms = (recv_ts - send_ts) * 1000
        if response == payload:
            LOGGER.info("回显校验通过（第 %s 条）", sent)
            # 成功日志
            run_context.write_latency_log(
                {
                    "seq": sent,
                    "ok": True,
                    "latency_ms": latency_ms,
                    "payload_len": len(payload),
                }
            )
        else:
            LOGGER.error("回显校验失败（第 %s 条）", sent)
            # 失败日志
            run_context.write_latency_log(
                {
                    "seq": sent,
                    "ok": False,
                    "latency_ms": latency_ms,
                    "payload_len": len(payload),
                }
            )
            break
        # 控制发送间隔
        await asyncio.sleep(args.interval)
    # 关闭连接
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
