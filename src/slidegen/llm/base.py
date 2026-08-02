"""The provider contract.

Every provider takes a brief and a catalog and returns candidate specs. What
happens in between — a real model, a local model, or arithmetic — is the
provider's business.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..spec import SlideSpec
from ..template import LayoutCatalog

DEFAULT_CANDIDATES = 3


@runtime_checkable
class Provider(Protocol):
    """Produces candidate slide specs for a brief."""

    name: str
    model: str

    def generate(
        self, brief: str, catalog: LayoutCatalog, n: int = DEFAULT_CANDIDATES
    ) -> list[SlideSpec]:
        """Return `n` candidate specs, each already validated against `catalog`."""
        ...


class ProviderError(RuntimeError):
    """A provider could not produce usable candidates.

    Carries a message fit to show a user — no tracebacks in the UI.
    """

    def __init__(self, message: str, *, provider: str = "", cause: Exception | None = None) -> None:
        self.provider = provider
        self.cause = cause
        super().__init__(message)
