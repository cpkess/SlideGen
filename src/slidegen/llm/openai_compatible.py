"""One provider implementation for both LM Studio and FordLLM.

They are the same thing from here: an OpenAI-compatible chat completions endpoint
reached with the OpenAI SDK. They differ in `base_url` and in how the API key is
obtained — a static string for LM Studio, a 60-minute AAD token for FordLLM.

Two FordLLM-specific hazards are handled here and nowhere else:

* The token expires hourly. `TokenFetcher` refreshes itself and is never
  re-instantiated, but the `OpenAI` client embeds the key at construction, so the
  client is cached against the token string and rebuilt when that string changes.
* The gateway validates request payloads against an Avro schema and answers a
  bare 500 for a null or a string where it wants a number. Payloads are stripped
  and coerced before every call.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from typing import Any

from ..spec import SlideSpec
from ..template import LayoutCatalog
from .base import DEFAULT_CANDIDATES, ProviderError
from ..retry import retry_call
from .prompts import repair_prompt, system_prompt, user_prompt
from .schema import TOOL_NAME, build_tool_schema

logger = logging.getLogger(__name__)

# Request parameters the gateway wants as real numbers, never strings.
INT_PARAMS = frozenset({"max_tokens", "max_completion_tokens", "n", "seed", "top_logprobs"})
FLOAT_PARAMS = frozenset({"temperature", "top_p", "presence_penalty", "frequency_penalty"})

TOKEN_REFRESH_LEEWAY = 300.0  # refresh five minutes early rather than mid-request


# --- payload hygiene -------------------------------------------------------


def _strip_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_strip_nulls(item) for item in value if item is not None]
    return value


def clean_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop nulls everywhere and coerce numeric parameters to real numbers.

    `temperature=None` or `max_tokens="1000"` earns a 500 Avro schema validation
    error from the FordLLM gateway, which is opaque enough to be worth preventing
    unconditionally rather than debugging twice.
    """
    cleaned = _strip_nulls(payload)
    for key in list(cleaned):
        if key in INT_PARAMS:
            cleaned[key] = int(float(cleaned[key]))
        elif key in FLOAT_PARAMS:
            cleaned[key] = float(cleaned[key])
    return cleaned


# --- AAD token -------------------------------------------------------------


def _post_form(url: str, form: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST a form and parse the JSON reply, honouring the environment's proxy."""
    data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


class TokenFetcher:
    """AAD client-credentials token, refreshed in place.

    Instantiate once and keep it: it holds the cached token and its expiry. The
    OpenAI client is what needs rebuilding when the token rotates, not this.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        authority: str = "https://login.microsoftonline.com",
        timeout: float = 30.0,
        leeway: float = TOKEN_REFRESH_LEEWAY,
        post: Callable[[str, dict[str, str], float], dict[str, Any]] = _post_form,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope or f"{client_id}/.default"
        self._authority = authority.rstrip("/")
        self._timeout = timeout
        self._leeway = leeway
        self._post = post
        self._clock = clock

        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at: float = 0.0

    @property
    def url(self) -> str:
        return f"{self._authority}/{self._tenant_id}/oauth2/v2.0/token"

    def token(self) -> str:
        """A valid access token, fetching or refreshing as needed."""
        with self._lock:
            if self._token and self._clock() < self._expires_at:
                return self._token

            payload = self._post(
                self.url,
                {
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": self._scope,
                },
                self._timeout,
            )
            token = payload.get("access_token")
            if not token:
                raise ProviderError(
                    "The identity provider did not return an access token. Check "
                    "FORDLLM_CLIENT_ID, FORDLLM_CLIENT_SECRET and FORDLLM_TENANT_ID.",
                    provider="ford",
                )

            expires_in = float(payload.get("expires_in", 3600))
            self._token = str(token)
            self._expires_at = self._clock() + max(0.0, expires_in - self._leeway)
            logger.info(
                "aad token acquired", extra={"expires_in": expires_in, "provider": "ford"}
            )
            return self._token


# --- provider --------------------------------------------------------------


def _openai_client_factory(**kwargs: Any) -> Any:
    from openai import OpenAI

    return OpenAI(**kwargs)


def _tool_call_of(response: Any) -> Any:
    """The `emit_candidates` call from a completion, whatever shape it arrives in."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ProviderError("The model returned no choices.")
    message = getattr(choices[0], "message", None)
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        if getattr(getattr(call, "function", None), "name", None) == TOOL_NAME:
            return call
    content = (getattr(message, "content", None) or "").strip()
    raise ProviderError(
        "The model replied without calling the tool"
        + (f": {content[:300]}" if content else ".")
    )


class OpenAICompatibleProvider:
    """Chat-completions provider driving structured output through tool calling.

    Tool calling rather than `response_format`: function calling is documented and
    supported on the FordLLM gateway, `json_schema` response format is not.
    """

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        api_key: str | None = None,
        token_fetcher: TokenFetcher | None = None,
        client_factory: Callable[..., Any] = _openai_client_factory,
        timeout: float = 60.0,
        max_attempts: int = 3,
        temperature: float = 0.4,
        max_tokens: int = 2048,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if api_key is None and token_fetcher is None:
            raise ValueError("one of api_key or token_fetcher is required")

        self.name = name
        self.model = model
        self._base_url = base_url
        self._api_key = api_key
        self._token_fetcher = token_fetcher
        self._client_factory = client_factory
        self._timeout = timeout
        self._max_attempts = max_attempts
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._sleep = sleep

        self._lock = threading.Lock()
        self._cached_client: Any | None = None
        self._cached_token: str | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _credential(self) -> str:
        if self._token_fetcher is not None:
            return self._token_fetcher.token()
        return self._api_key or ""

    def client(self) -> Any:
        """The OpenAI client for the current credential.

        Rebuilt only when the token string changes: the SDK captures the key at
        construction, so a refreshed token in a stale client is a 401 an hour in.
        """
        token = self._credential()
        with self._lock:
            if self._cached_client is None or self._cached_token != token:
                logger.debug("building client", extra={"provider": self.name})
                self._cached_client = self._client_factory(
                    api_key=token,
                    base_url=self._base_url,
                    timeout=self._timeout,
                    max_retries=0,  # backoff is ours, in slidegen.retry
                )
                self._cached_token = token
            return self._cached_client

    def _complete(self, payload: dict[str, Any]) -> Any:
        client = self.client()
        return client.chat.completions.create(**clean_payload(payload))

    def generate(
        self, brief: str, catalog: LayoutCatalog, n: int = DEFAULT_CANDIDATES
    ) -> list[SlideSpec]:
        from .repair import generate_with_repair

        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        retries = 0

        tool = build_tool_schema(catalog, n)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(catalog, n)},
            {"role": "user", "content": user_prompt(brief, n)},
        ]

        def attempt(feedback: list[str] | None) -> list[SlideSpec]:
            nonlocal retries
            if feedback:
                messages.append(
                    {
                        "role": "user",
                        "content": repair_prompt(feedback, n),
                    }
                )

            payload = {
                "model": self.model,
                "messages": messages,
                "tools": [tool],
                "tool_choice": {"type": "function", "function": {"name": TOOL_NAME}},
                "temperature": self._temperature,
                "max_tokens": self._max_tokens,
            }

            response, used = retry_call(
                lambda: self._complete(payload),
                max_attempts=self._max_attempts,
                sleep=self._sleep,
            )
            retries += used

            call = _tool_call_of(response)
            raw = call.function.arguments
            try:
                arguments = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    "The model's tool call was not valid JSON.",
                    provider=self.name,
                    cause=exc,
                ) from exc

            # Keep the rejected call in the transcript so the repair turn has
            # something to correct rather than something to guess at.
            messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": TOOL_NAME, "arguments": raw},
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": "Candidates received; validating against the template.",
                }
            )

            candidates = arguments.get("candidates") if isinstance(arguments, dict) else None
            if not isinstance(candidates, list) or not candidates:
                raise ProviderError(
                    "The model's tool call contained no candidates.", provider=self.name
                )
            return [SlideSpec.model_validate(candidate) for candidate in candidates]

        outcome = "ok"
        try:
            return generate_with_repair(attempt, catalog, provider=self.name)
        except ProviderError:
            outcome = "error"
            raise
        except Exception as exc:
            outcome = "error"
            raise ProviderError(
                f"{self.name} request failed: {exc}", provider=self.name, cause=exc
            ) from exc
        finally:
            logger.info(
                "generation finished",
                extra={
                    "request_id": request_id,
                    "provider": self.name,
                    "model": self.model,
                    "latency_ms": round((time.monotonic() - started) * 1000),
                    "retries": retries,
                    "outcome": outcome,
                    "candidates": n,
                },
            )
