"""Runtime settings, loaded from the environment / `.env`.

Every value documented in README.md's configuration table lives here. Nothing
in this module knows about templates, layouts or providers beyond their names.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["mock", "lmstudio", "ford"]
FordTier = Literal["secret", "standard"]

DEFAULT_MODELS: dict[str, str] = {
    "mock": "mock-1",
    "lmstudio": "local-model",
    "ford": "gpt-4o",
}


class Settings(BaseSettings):
    """Configuration for a SlideGen process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Template
    slidegen_template: str | None = None

    # Provider selection
    llm_provider: Provider = "mock"
    llm_model: str | None = None

    # LM Studio
    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_api_key: str = "lm-studio"

    # FordLLM
    ford_base_url: str | None = None
    ford_base_url_secret: str | None = None
    ford_tier: FordTier = "secret"
    fordllm_client_id: str | None = None
    fordllm_client_secret: str | None = None
    fordllm_tenant_id: str | None = None
    fordllm_scope: str | None = None
    fordllm_authority: str = "https://login.microsoftonline.com"

    # Generation
    candidate_count: int = Field(default=3, ge=1, le=6)
    request_timeout: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0, le=8)

    # Updates, from GitHub Releases
    slidegen_repo: str = "cpkess/SlideGen"
    slidegen_update_check: bool = True
    slidegen_update_ttl: float = Field(default=86_400.0, ge=60.0)
    github_token: str | None = None

    @property
    def model(self) -> str:
        """Model id for the active provider, falling back to a per-provider default."""
        return self.llm_model or DEFAULT_MODELS.get(self.llm_provider, "unknown")

    @property
    def ford_active_base_url(self) -> str | None:
        """The gateway matching `FORD_TIER`. Secret is the default for real use."""
        if self.ford_tier == "standard":
            return self.ford_base_url
        return self.ford_base_url_secret

    @property
    def ford_configured(self) -> bool:
        return bool(
            self.fordllm_client_id
            and self.fordllm_client_secret
            and self.fordllm_tenant_id
            and self.ford_active_base_url
        )

    def enabled_providers(self) -> list[Provider]:
        """Providers the UI may offer, given what is actually configured.

        `mock` is always available; it is the offline default.
        """
        enabled: list[Provider] = ["mock"]
        if self.lmstudio_base_url:
            enabled.append("lmstudio")
        if self.ford_configured:
            enabled.append("ford")
        return enabled


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached; call `get_settings.cache_clear()` in tests."""
    return Settings()
