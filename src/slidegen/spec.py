"""`SlideSpec` — the only thing that crosses the boundary from the model to the renderer.

A spec says *which* layout and *what* text. It cannot say where, how big, what
colour or what font: the template decides all of that. Keeping those
unrepresentable here is what makes the output on-brand by construction.

Validation errors are written to be handed straight back to a model for repair,
so every message names the offending value and the valid alternatives.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .template import LayoutCatalog, LayoutInfo

MAX_RATIONALE = 400
MAX_TEXT = 2000
MAX_BULLETS = 12
MAX_NOTES = 4000

PlaceholderValue = str | list[str]


class SlideSpec(BaseModel):
    """One slide: a layout choice plus the text to pour into its placeholders."""

    layout_index: int = Field(description="Index of a slide layout in the loaded template.")
    rationale: str = Field(
        default="",
        max_length=MAX_RATIONALE,
        description="Why this layout suits the brief. Shown to the user, never rendered.",
    )
    placeholders: dict[int, PlaceholderValue] = Field(
        default_factory=dict,
        description=(
            "Text keyed by placeholder idx. A string fills the text frame as a "
            "paragraph; a list of strings fills it as bullets at level 0."
        ),
    )
    notes: str | None = Field(
        default=None, max_length=MAX_NOTES, description="Speaker notes."
    )

    def text_for(self, idx: int) -> str:
        """Placeholder content flattened to a single string, for previews."""
        value = self.placeholders.get(idx)
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return "\n".join(value)


class SpecValidationError(ValueError):
    """One or more specs failed validation against the catalog.

    `errors` is a flat list of messages; `by_spec` maps a spec's position in the
    submitted batch to its own messages.
    """

    def __init__(self, errors: list[str], by_spec: dict[int, list[str]] | None = None) -> None:
        self.errors = errors
        self.by_spec = by_spec or {}
        super().__init__("; ".join(errors) if errors else "spec validation failed")


def _describe_layouts(catalog: LayoutCatalog) -> str:
    return ", ".join(f"{layout.index} ({layout.name})" for layout in catalog.usable_layouts)


def _describe_placeholders(layout: LayoutInfo) -> str:
    fillable = layout.content_placeholders
    if not fillable:
        return "none"
    return ", ".join(f"{p.idx} ({p.label})" for p in fillable)


def _is_empty(value: PlaceholderValue) -> bool:
    if isinstance(value, str):
        return not value.strip()
    return not [item for item in value if item.strip()]


def validate_spec(spec: SlideSpec, catalog: LayoutCatalog) -> list[str]:
    """Check one spec against the catalog. Returns messages; empty means valid."""
    errors: list[str] = []

    layout = catalog.layout(spec.layout_index)
    if layout is None:
        errors.append(
            f"layout_index {spec.layout_index} does not exist in template "
            f"'{catalog.source}'. Valid layout_index values: {_describe_layouts(catalog)}."
        )
        return errors

    if not layout.content_placeholders:
        errors.append(
            f"layout_index {spec.layout_index} ({layout.name}) has no text placeholder "
            f"to fill. Choose one of: {_describe_layouts(catalog)}."
        )
        return errors

    for idx, value in spec.placeholders.items():
        placeholder = layout.placeholder(idx)
        if placeholder is None:
            errors.append(
                f"placeholder idx {idx} does not exist on layout {layout.index} "
                f"({layout.name}). Valid idx values: {_describe_placeholders(layout)}."
            )
            continue
        if not placeholder.accepts_text:
            reason = (
                "is slide furniture filled by the template"
                if placeholder.is_furniture
                else f"is a {placeholder.type} placeholder and does not accept text"
            )
            errors.append(
                f"placeholder idx {idx} ({placeholder.label}) on layout {layout.index} "
                f"({layout.name}) {reason}. Valid idx values: {_describe_placeholders(layout)}."
            )
            continue
        if isinstance(value, list) and len(value) > MAX_BULLETS:
            errors.append(
                f"placeholder idx {idx} ({placeholder.label}) has {len(value)} bullets; "
                f"at most {MAX_BULLETS} are allowed. Merge or drop the weakest points."
            )

    title = layout.title_placeholder
    if title is not None:
        value = spec.placeholders.get(title.idx)
        if value is None:
            errors.append(
                f"layout {layout.index} ({layout.name}) requires a title: placeholder "
                f"idx {title.idx} ({title.label}) is missing."
            )
        elif _is_empty(value):
            errors.append(
                f"layout {layout.index} ({layout.name}) requires a title: placeholder "
                f"idx {title.idx} ({title.label}) is empty."
            )

    for idx, value in spec.placeholders.items():
        placeholder = layout.placeholder(idx)
        if placeholder is None or not placeholder.accepts_text or placeholder.is_title:
            continue
        if _is_empty(value):
            errors.append(
                f"placeholder idx {idx} ({placeholder.label}) on layout {layout.index} "
                f"({layout.name}) is empty. Supply text or omit the key entirely."
            )

    return errors


def validate_specs(specs: list[SlideSpec], catalog: LayoutCatalog) -> dict[int, list[str]]:
    """Validate a batch. Returns `{position: messages}` for the failures only."""
    failures: dict[int, list[str]] = {}
    for position, spec in enumerate(specs):
        errors = validate_spec(spec, catalog)
        if errors:
            failures[position] = errors
    return failures


def ensure_valid(specs: list[SlideSpec], catalog: LayoutCatalog) -> None:
    """Raise `SpecValidationError` if any spec in the batch is invalid."""
    failures = validate_specs(specs, catalog)
    if not failures:
        return
    flat = [
        f"candidate {position + 1}: {message}"
        for position, messages in sorted(failures.items())
        for message in messages
    ]
    raise SpecValidationError(flat, failures)
