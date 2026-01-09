from __future__ import annotations

import asyncio

from logger import setup_logger


LOGGER = setup_logger("server")


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
        writer.close()
        await writer.wait_closed()


async def main(host: str = "127.0.0.1", port: int = 9301) -> None:
    server = await asyncio.start_server(handle_client, host, port)
    LOGGER.info("目标服务监听 %s:%s", host, port)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
