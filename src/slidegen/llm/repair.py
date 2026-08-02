"""The validate-and-repair loop, shared by every provider.

A model that names a placeholder idx belonging to a different layout has made a
recoverable mistake, and the validation errors say exactly what is wrong. One
retry with that feedback fixes it far more often than it does not; a second
retry rarely adds anything, so failure is surfaced instead.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..spec import SlideSpec, SpecValidationError, validate_specs
from ..template import LayoutCatalog
from .base import ProviderError

logger = logging.getLogger(__name__)

Attempt = Callable[[list[str] | None], list[SlideSpec]]


def generate_with_repair(
    attempt: Attempt,
    catalog: LayoutCatalog,
    *,
    provider: str = "",
    max_repairs: int = 1,
) -> list[SlideSpec]:
    """Call `attempt`, validate, and retry once with the validation errors.

    `attempt` receives `None` on the first call and the list of validation error
    messages on a repair call.
    """
    feedback: list[str] | None = None

    for round_number in range(max_repairs + 1):
        specs = attempt(feedback)
        failures = validate_specs(specs, catalog)
        if not failures:
            if round_number:
                logger.info("repair succeeded", extra={"round": round_number})
            return specs

        feedback = [
            f"candidate {position + 1}: {message}"
            for position, messages in sorted(failures.items())
            for message in messages
        ]
        logger.warning(
            "candidates failed validation",
            extra={"round": round_number, "errors": len(feedback)},
        )

    detail = "\n".join(f"- {message}" for message in feedback or [])
    raise ProviderError(
        f"The model returned slide candidates that do not fit the template, and the "
        f"correction attempt did not fix them:\n{detail}",
        provider=provider,
        cause=SpecValidationError(feedback or []),
    )
