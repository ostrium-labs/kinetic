"""Structured JSON-lines logging to <cache>/logs/. Human-facing output goes through the CLI."""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import structlog

from ostrium_kinetic.workspace import Workspace

_configured: Path | None = None


def configure(ws: Workspace) -> Path:
    """Send structlog events as JSON lines to <cache>/logs/kinetic-YYYYMMDD.jsonl."""
    global _configured
    log_dir = ws.cache("logs")
    path = log_dir / f"kinetic-{dt.date.today():%Y%m%d}.jsonl"
    if _configured == path:
        return path
    handle = path.open("a", encoding="utf-8")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.WriteLoggerFactory(file=handle),
        cache_logger_on_first_use=False,
    )
    _configured = path
    return path


def get(**context: Any) -> Any:
    return structlog.get_logger("kinetic").bind(**context)
