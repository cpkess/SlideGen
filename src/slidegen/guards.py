"""A conservative overflow estimate for placeholder text.

The template controls autofit, so long text does not break the file — it shrinks,
or it spills. Either way a human wants to know before they present it. This is a
warning, never a hard failure: the estimate cannot see the real font metrics, and
being wrong should cost a dismissed notice, not a blocked download.

Nothing here sets a size. It reads the size the layout already declares.
"""

from __future__ import annotations

import math

from pydantic import BaseModel

from .spec import SlideSpec
from .template import LayoutCatalog

EMU_PER_POINT = 12700

# Assumed point sizes, used only to estimate capacity. Deliberately on the large
# side: a template's title style is rarely smaller than this, and over-estimating
# the font means under-estimating capacity, which errs toward warning.
ASSUMED_TITLE_POINTS = 28.0
ASSUMED_BODY_POINTS = 18.0

# Average glyph width as a fraction of point size, and line height as a multiple.
CHAR_WIDTH_RATIO = 0.5
LINE_HEIGHT_RATIO = 1.2

# Placeholders are inset from their box by roughly 0.1" left/right, 0.05" top/bottom.
HORIZONTAL_INSET_EMU = 182_880
VERTICAL_INSET_EMU = 91_440

# Only warn once the estimate is clearly over, not at the boundary.
WARN_RATIO = 1.15


class LengthWarning(BaseModel):
    """One placeholder whose text is likely not to fit."""

    slide: int
    layout_index: int
    layout_name: str
    idx: int
    label: str
    estimated_lines: int
    capacity_lines: int

    @property
    def message(self) -> str:
        return (
            f"Slide {self.slide}: “{self.label}” holds about {self.capacity_lines} "
            f"lines but the text needs about {self.estimated_lines}. It will shrink "
            "or overflow — trim it, or choose a layout with more room."
        )


def _capacity(width: int | None, height: int | None, points: float) -> tuple[int, int]:
    """`(characters per line, lines)` a box of this size can hold."""
    if not width or not height:
        return (0, 0)
    usable_width = max(0, width - HORIZONTAL_INSET_EMU) / EMU_PER_POINT
    usable_height = max(0, height - VERTICAL_INSET_EMU) / EMU_PER_POINT
    chars_per_line = int(usable_width // (points * CHAR_WIDTH_RATIO))
    lines = int(usable_height // (points * LINE_HEIGHT_RATIO))
    return (max(1, chars_per_line), max(1, lines))


def _lines_needed(value: str | list[str], chars_per_line: int) -> int:
    chunks = [value] if isinstance(value, str) else list(value)
    total = 0
    for chunk in chunks:
        for paragraph in str(chunk).split("\n"):
            total += max(1, math.ceil(len(paragraph) / chars_per_line))
    return total


def check_spec(spec: SlideSpec, catalog: LayoutCatalog, slide: int = 1) -> list[LengthWarning]:
    """Estimate whether any placeholder on this slide will overflow."""
    layout = catalog.layout(spec.layout_index)
    if layout is None:
        return []

    warnings: list[LengthWarning] = []
    for idx, value in spec.placeholders.items():
        placeholder = layout.placeholder(idx)
        if placeholder is None or not placeholder.accepts_text:
            continue

        points = ASSUMED_TITLE_POINTS if placeholder.is_title else ASSUMED_BODY_POINTS
        chars_per_line, capacity = _capacity(placeholder.width, placeholder.height, points)
        if not chars_per_line or not capacity:
            continue

        needed = _lines_needed(value, chars_per_line)
        if needed > capacity * WARN_RATIO:
            warnings.append(
                LengthWarning(
                    slide=slide,
                    layout_index=layout.index,
                    layout_name=layout.name,
                    idx=idx,
                    label=placeholder.label,
                    estimated_lines=needed,
                    capacity_lines=capacity,
                )
            )
    return warnings


def check_specs(specs: list[SlideSpec], catalog: LayoutCatalog) -> list[LengthWarning]:
    """Overflow warnings across a whole deck, numbered from slide 1."""
    return [
        warning
        for position, spec in enumerate(specs, start=1)
        for warning in check_spec(spec, catalog, slide=position)
    ]
