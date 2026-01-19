from __future__ import annotations

import asyncio

from logger import setup_logger
from config import DEFAULT_CONFIG

# 目标服务：简单回显服务器，用于端到端测试。


LOGGER = setup_logger("server")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    # 处理客户端连接并回显数据
    addr = writer.get_extra_info("peername")
    LOGGER.info("客户端已连接 %s", addr)
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except asyncio.IncompleteReadError:
        LOGGER.info("客户端已断开 %s", addr)
    finally:
        # 关闭连接
        writer.close()
        await writer.wait_closed()


async def main(host: str | None = None, port: int | None = None) -> None:
    # 启动目标服务
    target_host = host or DEFAULT_CONFIG.server_host
    target_port = port or DEFAULT_CONFIG.server_port
    server = await asyncio.start_server(handle_client, target_host, target_port)
    LOGGER.info("目标服务监听 %s:%s", target_host, target_port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
