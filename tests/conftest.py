"""Shared fixtures.

Everything here runs against the `python-pptx` built-in template, offline. No
Ford assets, no network, no live model — see CLAUDE.md.
"""

from __future__ import annotations

import pytest

from slidegen.config import Settings, get_settings
from slidegen.template import LayoutCatalog, describe_template, load_catalog

# Built-in template facts the suite relies on. These are properties of
# python-pptx, not of any corporate template.
BUILT_IN_LAYOUT_COUNT = 11
TITLE_AND_CONTENT = 1
TWO_CONTENT = 3
BLANK = 6


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a developer's real `.env` or exported vars reach a test."""
    for name in (
        "SLIDEGEN_TEMPLATE",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "FORD_BASE_URL",
        "FORD_BASE_URL_SECRET",
        "FORD_TIER",
        "FORDLLM_CLIENT_ID",
        "FORDLLM_CLIENT_SECRET",
        "FORDLLM_TENANT_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def presentation():
    return load_catalog(None)[0]


@pytest.fixture
def catalog() -> LayoutCatalog:
    return load_catalog(None)[1]


@pytest.fixture
def settings() -> Settings:
    return Settings(llm_provider="mock")


def catalog_of(presentation) -> LayoutCatalog:
    return describe_template(presentation)
