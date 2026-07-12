from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from mtyh.paths import logs_dir


def configure_logging() -> None:
    log_dir = logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "mtyh.log"

    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)
