"""Backoff and structured logging."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from slidegen.logging_config import JSONFormatter, configure_logging
from slidegen.retry import is_retryable, retry_call, status_of


class Status(Exception):
    def __init__(self, status: int) -> None:
        self.status_code = status
        super().__init__(f"HTTP {status}")


class APIConnectionError(Exception):
    """Same name as the SDK's, which is how `is_retryable` recognises it."""


def failing(*errors, then=None):
    """A callable that raises each error in turn, then returns `then`."""
    queue = list(errors)

    def call():
        if queue:
            raise queue.pop(0)
        return then

    return call


def test_a_successful_call_is_not_retried():
    result, retries = retry_call(lambda: "ok", sleep=lambda _s: None)

    assert (result, retries) == ("ok", 0)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(status):
    result, retries = retry_call(
        failing(Status(status), then="ok"), sleep=lambda _s: None
    )

    assert (result, retries) == ("ok", 1)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(status):
    with pytest.raises(Status):
        retry_call(failing(Status(status), then="ok"), sleep=lambda _s: None)


def test_connection_errors_are_retried():
    result, _ = retry_call(
        failing(APIConnectionError("reset"), then="ok"), sleep=lambda _s: None
    )

    assert result == "ok"


def test_attempts_are_bounded():
    calls = 0

    def always_fail():
        nonlocal calls
        calls += 1
        raise Status(503)

    with pytest.raises(Status):
        retry_call(always_fail, max_attempts=4, sleep=lambda _s: None)

    assert calls == 4


def test_the_delay_grows_and_stays_inside_the_ceiling():
    delays: list[float] = []

    with pytest.raises(Status):
        retry_call(
            failing(*[Status(503)] * 5),
            max_attempts=5,
            base_delay=1.0,
            max_delay=8.0,
            sleep=delays.append,
        )

    assert len(delays) == 4
    assert all(0 <= delay <= 8.0 for delay in delays)
    assert delays[-1] >= delays[0] / 2  # jittered, but trending up


def test_status_is_read_from_a_nested_response():
    class WithResponse(Exception):
        response = type("R", (), {"status_code": 429})()

    assert status_of(WithResponse()) == 429
    assert is_retryable(WithResponse())


def test_an_unrecognised_error_is_not_retried():
    assert not is_retryable(ValueError("nope"))


def test_the_formatter_emits_one_json_object_per_record():
    record = logging.LogRecord("slidegen.test", logging.INFO, __file__, 1, "hello", (), None)

    payload = json.loads(JSONFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "slidegen.test"
    assert payload["message"] == "hello"
    assert "ts" in payload


def test_extra_fields_are_merged_into_the_object():
    logger = logging.getLogger("slidegen.test.extra")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        logger.info(
            "generation finished",
            extra={
                "request_id": "abc123",
                "provider": "ford",
                "model": "gpt-4o",
                "latency_ms": 1234,
                "retries": 1,
                "outcome": "ok",
            },
        )
    finally:
        logger.removeHandler(handler)

    payload = json.loads(stream.getvalue())
    assert payload["request_id"] == "abc123"
    assert payload["provider"] == "ford"
    assert payload["latency_ms"] == 1234
    assert payload["retries"] == 1
    assert payload["outcome"] == "ok"


def test_configure_logging_is_idempotent():
    logger = logging.getLogger("slidegen")
    before = list(logger.handlers)
    try:
        configure_logging()
        count = len(logger.handlers)
        configure_logging()

        assert len(logger.handlers) == count
        assert any(isinstance(h.formatter, JSONFormatter) for h in logger.handlers)
    finally:
        logger.handlers = before
