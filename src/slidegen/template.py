"""Inspect a PowerPoint template and describe what it offers.

This is the only module that knows about `python-pptx` placeholder internals or
about specific placeholder types. Everything downstream — validation, the tool
schema, the renderer, the UI — reads a `LayoutCatalog` produced here.

Nothing in this module decides how a slide *looks*. The template already did.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.presentation import Presentation as PresentationType
from pydantic import BaseModel, Field

from .config import get_settings

logger = logging.getLogger(__name__)

# --- placeholder type groupings -------------------------------------------
#
# A placeholder's capabilities follow from its type. `has_text_frame` is not a
# useful signal: layout picture placeholders report it as True.

_TITLE_TYPES = frozenset(
    {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE, PP_PLACEHOLDER.VERTICAL_TITLE}
)

_TEXT_TYPES = _TITLE_TYPES | frozenset(
    {
        PP_PLACEHOLDER.BODY,
        PP_PLACEHOLDER.VERTICAL_BODY,
        PP_PLACEHOLDER.SUBTITLE,
        PP_PLACEHOLDER.OBJECT,
        PP_PLACEHOLDER.VERTICAL_OBJECT,
    }
)

_PICTURE_TYPES = frozenset(
    {
        PP_PLACEHOLDER.PICTURE,
        PP_PLACEHOLDER.BITMAP,
        PP_PLACEHOLDER.MEDIA_CLIP,
        PP_PLACEHOLDER.OBJECT,
    }
)

_TABLE_TYPES = frozenset({PP_PLACEHOLDER.TABLE, PP_PLACEHOLDER.OBJECT})

_CHART_TYPES = frozenset(
    {PP_PLACEHOLDER.CHART, PP_PLACEHOLDER.ORG_CHART, PP_PLACEHOLDER.OBJECT}
)

# Slide furniture: driven by the deck, not by the brief. The template fills
# these itself, so they are catalogued but never offered to the model.
_FURNITURE_TYPES = frozenset(
    {
        PP_PLACEHOLDER.DATE,
        PP_PLACEHOLDER.FOOTER,
        PP_PLACEHOLDER.HEADER,
        PP_PLACEHOLDER.SLIDE_NUMBER,
    }
)


class PlaceholderInfo(BaseModel):
    """One placeholder on one slide layout."""

    idx: int = Field(description="Placeholder index, unique within its layout.")
    type: str = Field(description="Placeholder type name, e.g. 'BODY'.")
    name: str = Field(description="Placeholder name as authored in the template.")

    accepts_text: bool
    accepts_picture: bool
    accepts_table: bool
    accepts_chart: bool

    is_title: bool = Field(default=False, description="Title-like placeholder.")
    is_furniture: bool = Field(
        default=False,
        description="Date, footer, header or slide number — deck-level, not content.",
    )

    width: int | None = Field(default=None, description="Width in EMU, if the layout sets one.")
    height: int | None = Field(default=None, description="Height in EMU, if the layout sets one.")

    @property
    def label(self) -> str:
        """Human-facing label: the authored name, falling back to the type."""
        return self.name or self.type


class LayoutInfo(BaseModel):
    """One slide layout in the template."""

    index: int
    name: str
    placeholders: list[PlaceholderInfo] = Field(default_factory=list)

    def placeholder(self, idx: int) -> PlaceholderInfo | None:
        return next((p for p in self.placeholders if p.idx == idx), None)

    @property
    def content_placeholders(self) -> list[PlaceholderInfo]:
        """Text placeholders the model may fill — everything but slide furniture."""
        return [p for p in self.placeholders if p.accepts_text and not p.is_furniture]

    @property
    def title_placeholder(self) -> PlaceholderInfo | None:
        return next((p for p in self.placeholders if p.is_title), None)

    @property
    def fillable_idxs(self) -> list[int]:
        return [p.idx for p in self.content_placeholders]


class LayoutCatalog(BaseModel):
    """Everything SlideGen knows about the loaded template."""

    source: str = Field(description="Template path, or 'python-pptx built-in'.")
    slide_width: int | None = None
    slide_height: int | None = None
    layouts: list[LayoutInfo] = Field(default_factory=list)

    def layout(self, index: int) -> LayoutInfo | None:
        return next((layout for layout in self.layouts if layout.index == index), None)

    @property
    def indices(self) -> list[int]:
        return [layout.index for layout in self.layouts]

    @property
    def usable_layouts(self) -> list[LayoutInfo]:
        """Layouts with at least one fillable text placeholder.

        A layout with nothing to fill (a 'Blank' layout, a pure image layout)
        cannot carry a brief, so it is not offered to the model.
        """
        return [layout for layout in self.layouts if layout.content_placeholders]


BUILT_IN = "python-pptx built-in"


def _placeholder_info(placeholder) -> PlaceholderInfo:
    fmt = placeholder.placeholder_format
    ph_type = fmt.type
    return PlaceholderInfo(
        idx=fmt.idx,
        type=getattr(ph_type, "name", str(ph_type)),
        name=placeholder.name or "",
        accepts_text=ph_type in _TEXT_TYPES,
        accepts_picture=ph_type in _PICTURE_TYPES,
        accepts_table=ph_type in _TABLE_TYPES,
        accepts_chart=ph_type in _CHART_TYPES,
        is_title=ph_type in _TITLE_TYPES,
        is_furniture=ph_type in _FURNITURE_TYPES,
        width=placeholder.width,
        height=placeholder.height,
    )


def resolve_template_path(path: str | Path | None = None) -> Path | None:
    """The template to load: explicit argument, then `SLIDEGEN_TEMPLATE`, then None.

    `None` means the `python-pptx` built-in template.
    """
    candidate = path if path is not None else get_settings().slidegen_template
    if candidate is None or str(candidate).strip() == "":
        return None
    return Path(str(candidate)).expanduser()


def load_template(path: str | Path | None = None) -> PresentationType:
    """Open a template, falling back to the `python-pptx` built-in.

    A `.potx` opens exactly like a `.pptx` as far as python-pptx is concerned.
    """
    resolved = resolve_template_path(path)
    if resolved is None:
        logger.debug("loading built-in template")
        return Presentation()
    if not resolved.is_file():
        raise FileNotFoundError(f"Template not found: {resolved}")
    logger.debug("loading template", extra={"template": str(resolved)})
    return Presentation(str(resolved))


def describe_template(
    presentation: PresentationType, source: str = BUILT_IN
) -> LayoutCatalog:
    """Build a `LayoutCatalog` from an already-open presentation."""
    layouts = [
        LayoutInfo(
            index=index,
            name=layout.name or f"Layout {index}",
            placeholders=sorted(
                (_placeholder_info(ph) for ph in layout.placeholders),
                key=lambda p: p.idx,
            ),
        )
        for index, layout in enumerate(presentation.slide_layouts)
    ]
    return LayoutCatalog(
        source=source,
        slide_width=presentation.slide_width,
        slide_height=presentation.slide_height,
        layouts=layouts,
    )


def load_catalog(path: str | Path | None = None) -> tuple[PresentationType, LayoutCatalog]:
    """Load a template and describe it in one step.

    Returns the presentation too, so callers that go on to render do not pay to
    open the file twice.
    """
    resolved = resolve_template_path(path)
    presentation = load_template(path)
    catalog = describe_template(presentation, source=str(resolved) if resolved else BUILT_IN)
    logger.debug(
        "template catalogued",
        extra={"source": catalog.source, "layouts": len(catalog.layouts)},
    )
    return presentation, catalog


def get_catalog(path: str | Path | None = None) -> LayoutCatalog:
    """The catalog alone, for callers that do not need the presentation."""
    return load_catalog(path)[1]
