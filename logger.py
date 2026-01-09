from __future__ import annotations

import logging

# 日志工具：统一设置日志格式与级别。


_DEF_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logger(name: str) -> logging.Logger:
    # 初始化全局日志配置，并返回指定名称的 logger
    logging.basicConfig(level=logging.INFO, format=_DEF_FORMAT)
    return logging.getLogger(name)
