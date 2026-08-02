"""Structured JSON logging to stdout.

One JSON object per line, so whatever collects stdout can index it without a
parser. Anything passed via `extra=` is merged into the object.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


class JSONFormatter(logging.Formatter):
    """Render a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int | str = logging.INFO) -> None:
    """Install the JSON formatter on the `slidegen` logger. Idempotent."""
    logger = logging.getLogger("slidegen")
    logger.setLevel(level)
    logger.propagate = False

    for existing in logger.handlers:
        if isinstance(existing.formatter, JSONFormatter):
            return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
