"""A deterministic, offline provider.

It is not a language model and does not pretend to be one. It exists so the
whole pipeline — schema, validation, repair, render, download — can be exercised
with no network and no credentials, and so tests have something stable to assert
against.

It still produces genuinely *different* candidates: different layouts, different
shapes of content. A picker with three near-identical cards would hide bugs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..spec import MAX_BULLETS, SlideSpec
from ..template import LayoutCatalog, LayoutInfo, PlaceholderInfo
from .base import DEFAULT_CANDIDATES, ProviderError
from .repair import generate_with_repair

# A placeholder shorter than this is a heading or caption, not a bullet list.
SHORT_PLACEHOLDER_EMU = 900_000

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_TITLE_MAX = 70
_POINT_MAX = 110


@dataclass(frozen=True)
class _Approach:
    """One way of framing the brief, and the layout shape it wants."""

    key: str
    rationale: str
    min_slots: int
    max_slots: int | None
    prefer_bullets: bool


_APPROACHES = (
    _Approach(
        key="headline",
        rationale="Single message with the supporting points as bullets — the default read.",
        min_slots=1,
        max_slots=1,
        prefer_bullets=True,
    ),
    _Approach(
        key="statement",
        rationale="Leads with the conclusion as a short statement, detail left to the speaker.",
        min_slots=1,
        max_slots=1,
        prefer_bullets=False,
    ),
    _Approach(
        key="split",
        rationale="Splits the brief across columns so two strands can be compared side by side.",
        min_slots=2,
        max_slots=None,
        prefer_bullets=True,
    ),
)


def _sentences(brief: str) -> list[str]:
    parts = (part.strip(" \t-•*") for part in _SENTENCE_SPLIT.split(brief or ""))
    return [part for part in parts if part]


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip(" ,;:.") + "…"


def _slots(layout: LayoutInfo) -> list[PlaceholderInfo]:
    """Content placeholders other than the title."""
    return [p for p in layout.content_placeholders if not p.is_title]


def _score(layout: LayoutInfo, approach: _Approach) -> tuple[int, int, int]:
    """Rank layouts for an approach. Lower sorts first."""
    count = len(_slots(layout))
    if count < approach.min_slots:
        fit = 2
    elif approach.max_slots is not None and count > approach.max_slots:
        fit = 1
    else:
        fit = 0
    has_title = 0 if layout.title_placeholder else 1
    return (fit, has_title, layout.index)


def _pick_layout(
    catalog: LayoutCatalog, approach: _Approach, used: set[int]
) -> LayoutInfo | None:
    ranked = sorted(catalog.usable_layouts, key=lambda layout: _score(layout, approach))
    for layout in ranked:
        if layout.index not in used:
            return layout
    return ranked[0] if ranked else None


def _distribute(points: list[str], buckets: int) -> list[list[str]]:
    """Deal points round-robin so every bucket gets something."""
    result: list[list[str]] = [[] for _ in range(buckets)]
    for position, point in enumerate(points):
        result[position % buckets].append(point)
    return result


def _build_spec(
    layout: LayoutInfo, approach: _Approach, title: str, points: list[str]
) -> SlideSpec:
    placeholders: dict[int, str | list[str]] = {}

    title_placeholder = layout.title_placeholder
    if title_placeholder is not None:
        placeholders[title_placeholder.idx] = title

    slots = _slots(layout)
    if slots:
        shares = _distribute(points, len(slots))
        for placeholder, share in zip(slots, shares, strict=True):
            if not share:
                share = [title]
            short = (placeholder.height or 0) and placeholder.height < SHORT_PLACEHOLDER_EMU
            if short or not approach.prefer_bullets:
                placeholders[placeholder.idx] = _truncate(" ".join(share), _POINT_MAX * 2)
            else:
                placeholders[placeholder.idx] = share[:MAX_BULLETS]

    return SlideSpec(
        layout_index=layout.index,
        rationale=f"{layout.name}. {approach.rationale}",
        placeholders=placeholders,
        notes=f"Generated offline by the mock provider ({approach.key} approach).",
    )


class MockProvider:
    """Deterministic candidates derived from the brief and the catalog."""

    name = "mock"

    def __init__(self, model: str = "mock-1") -> None:
        self.model = model

    def ping(self) -> list[str]:
        """Always reachable — it is arithmetic, not a server."""
        return [self.model]

    def generate(
        self, brief: str, catalog: LayoutCatalog, n: int = DEFAULT_CANDIDATES
    ) -> list[SlideSpec]:
        if not catalog.usable_layouts:
            raise ProviderError(
                f"Template '{catalog.source}' has no layout with a fillable text "
                "placeholder, so no slide can be built from it.",
                provider=self.name,
            )

        return generate_with_repair(
            lambda _feedback: self._candidates(brief, catalog, n),
            catalog,
            provider=self.name,
            max_repairs=0,
        )

    def _candidates(self, brief: str, catalog: LayoutCatalog, n: int) -> list[SlideSpec]:
        sentences = _sentences(brief)
        title = _truncate(sentences[0], _TITLE_MAX) if sentences else "Untitled brief"
        points = [_truncate(sentence, _POINT_MAX) for sentence in sentences[1:]] or [title]

        specs: list[SlideSpec] = []
        used: set[int] = set()
        for position in range(n):
            approach = _APPROACHES[position % len(_APPROACHES)]
            layout = _pick_layout(catalog, approach, used)
            if layout is None:  # pragma: no cover — guarded by usable_layouts above
                break
            used.add(layout.index)
            specs.append(_build_spec(layout, approach, title, points))
        return specs
