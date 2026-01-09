from __future__ import annotations

import logging


_DEF_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logger(name: str) -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format=_DEF_FORMAT)
    return logging.getLogger(name)
