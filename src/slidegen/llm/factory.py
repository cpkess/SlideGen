"""Select a provider from configuration.

Providers are cached per configuration. Streamlit reruns the whole script on
every interaction, and a fresh provider each time would mean a fresh
`TokenFetcher` and a fresh AAD round trip per keystroke.
"""

from __future__ import annotations

import threading
from typing import Any

from ..config import Settings, get_settings
from .base import Provider, ProviderError
from .mock import MockProvider
from .openai_compatible import OpenAICompatibleProvider, TokenFetcher

_lock = threading.Lock()
_providers: dict[tuple[Any, ...], Provider] = {}
_fetchers: dict[tuple[str, str, str], TokenFetcher] = {}


def enabled_providers(settings: Settings | None = None) -> list[str]:
    """Provider names the UI may offer, given what is actually configured."""
    return list((settings or get_settings()).enabled_providers())


def _token_fetcher(settings: Settings) -> TokenFetcher:
    """One `TokenFetcher` per credential set, reused for the process's lifetime."""
    key = (
        str(settings.fordllm_tenant_id),
        str(settings.fordllm_client_id),
        str(settings.fordllm_authority),
    )
    fetcher = _fetchers.get(key)
    if fetcher is None:
        fetcher = TokenFetcher(
            tenant_id=str(settings.fordllm_tenant_id),
            client_id=str(settings.fordllm_client_id),
            client_secret=str(settings.fordllm_client_secret),
            scope=settings.fordllm_scope,
            authority=settings.fordllm_authority,
            timeout=settings.request_timeout,
        )
        _fetchers[key] = fetcher
    return fetcher


def _build(settings: Settings) -> Provider:
    provider = settings.llm_provider

    if provider == "mock":
        return MockProvider(model=settings.model)

    if provider == "lmstudio":
        return OpenAICompatibleProvider(
            name="lmstudio",
            model=settings.model,
            base_url=settings.lmstudio_base_url,
            api_key=settings.lmstudio_api_key,
            timeout=settings.request_timeout,
            max_attempts=settings.max_retries,
        )

    if provider == "ford":
        base_url = settings.ford_active_base_url
        if not base_url:
            variable = "FORD_BASE_URL" if settings.ford_tier == "standard" else "FORD_BASE_URL_SECRET"
            raise ProviderError(
                f"FORD_TIER is '{settings.ford_tier}' but {variable} is not set.",
                provider="ford",
            )
        if not settings.ford_configured:
            raise ProviderError(
                "FordLLM is not fully configured. Set FORDLLM_CLIENT_ID, "
                "FORDLLM_CLIENT_SECRET, FORDLLM_TENANT_ID and the gateway URL for "
                f"FORD_TIER='{settings.ford_tier}'.",
                provider="ford",
            )
        return OpenAICompatibleProvider(
            name="ford",
            model=settings.model,
            base_url=base_url,
            token_fetcher=_token_fetcher(settings),
            timeout=settings.request_timeout,
            max_attempts=settings.max_retries,
        )

    raise ProviderError(f"Unknown provider '{provider}'.")


def get_provider(settings: Settings | None = None) -> Provider:
    """The provider for these settings, built once and reused."""
    settings = settings or get_settings()
    key = (
        settings.llm_provider,
        settings.model,
        settings.lmstudio_base_url,
        settings.ford_active_base_url,
        settings.fordllm_client_id,
        settings.request_timeout,
        settings.max_retries,
    )
    with _lock:
        provider = _providers.get(key)
        if provider is None:
            provider = _build(settings)
            _providers[key] = provider
        return provider


def reset_cache() -> None:
    """Drop cached providers and token fetchers. For tests and settings changes."""
    with _lock:
        _providers.clear()
        _fetchers.clear()
