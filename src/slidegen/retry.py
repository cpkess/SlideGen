"""Exponential backoff for transient upstream failures.

Retries 429 and 5xx and connection errors; never retries a 4xx we caused, which
would just be a slower way to fail.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 0.5
DEFAULT_MAX_DELAY = 20.0


def status_of(exc: BaseException) -> int | None:
    """HTTP status carried by an exception, if it has one."""
    for attribute in ("status_code", "status", "http_status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def is_retryable(exc: BaseException) -> bool:
    status = status_of(exc)
    if status is not None:
        return status in RETRYABLE_STATUS
    # No status: a connection reset or timeout on the way out. Worth one more go.
    return type(exc).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectionError",
        "Timeout",
        "TimeoutError",
    }


def _delay(attempt: int, base_delay: float, max_delay: float, jitter: float) -> float:
    """Exponential backoff with full jitter, so parallel clients do not resonate."""
    ceiling = min(max_delay, base_delay * (2**attempt))
    return ceiling * (1 - jitter * random.random())


def retry_call(
    operation: Callable[[], T],
    *,
    max_attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    retryable: Callable[[BaseException], bool] = is_retryable,
    on_retry: Callable[[int, BaseException], None] | None = None,
) -> tuple[T, int]:
    """Run `operation`, retrying transient failures. Returns `(result, retries)`."""
    attempts = max(1, max_attempts)
    last: BaseException

    for attempt in range(attempts):
        try:
            return operation(), attempt
        except BaseException as exc:  # noqa: BLE001 — re-raised below
            if not retryable(exc) or attempt == attempts - 1:
                raise
            last = exc
            pause = _delay(attempt, base_delay, max_delay, jitter)
            if on_retry:
                on_retry(attempt + 1, exc)
            logger.warning(
                "retrying after transient failure",
                extra={
                    "attempt": attempt + 1,
                    "status": status_of(exc),
                    "delay": round(pause, 3),
                    "error": type(exc).__name__,
                },
            )
            sleep(pause)

    raise last  # pragma: no cover — the loop either returns or raises
