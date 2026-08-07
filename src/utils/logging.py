"""Structured-ish logging to both console and a rotating file. No secrets are
ever logged (the AI client redacts keys before any logging happens)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from ..config.settings import LOGS_DIR

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("ignis")
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    fileh = RotatingFileHandler(
        LOGS_DIR / "ignis_hunter.log", maxBytes=5_000_000, backupCount=5,
        encoding="utf-8",
    )
    fileh.setFormatter(fmt)
    root.addHandler(fileh)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"ignis.{name}")
