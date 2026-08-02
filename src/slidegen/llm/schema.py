"""Build the model's tool schema from the loaded template.

The schema is generated, never written by hand. Add a layout to the template in
PowerPoint and it appears here on the next run.

The schema is also the guardrail: a candidate can name a layout and supply text,
and nothing else. Positions, sizes, fonts and colours have no representation, so
the model cannot emit them even by accident.
"""

from __future__ import annotations

from typing import Any

from ..spec import MAX_BULLETS, MAX_NOTES, MAX_RATIONALE, MAX_TEXT
from ..template import LayoutCatalog, LayoutInfo

TOOL_NAME = "emit_candidates"


def layout_summary(layout: LayoutInfo) -> str:
    """`0 (Title Slide): idx 0 = Title 1 [title], idx 1 = Subtitle 2`."""
    parts = []
    for placeholder in layout.content_placeholders:
        role = " [title]" if placeholder.is_title else ""
        parts.append(f"idx {placeholder.idx} = {placeholder.label}{role}")
    return f"{layout.index} ({layout.name}): " + ("; ".join(parts) or "no text placeholders")


def describe_layouts(catalog: LayoutCatalog) -> str:
    """Multi-line description of every usable layout, for prompts and schemas."""
    return "\n".join(f"- {layout_summary(layout)}" for layout in catalog.usable_layouts)


def _placeholder_value_schema(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "anyOf": [
            {
                "type": "string",
                "maxLength": MAX_TEXT,
                "description": "Fills the placeholder as a single paragraph.",
            },
            {
                "type": "array",
                "maxItems": MAX_BULLETS,
                "items": {"type": "string", "maxLength": MAX_TEXT},
                "description": "Fills the placeholder as bullets, one per item.",
            },
        ],
    }


def _placeholders_schema(catalog: LayoutCatalog) -> dict[str, Any]:
    """One property per placeholder idx that exists somewhere in the template.

    Each property's description says which layouts it belongs to, so the model can
    tell that `idx 2` means one thing on a comparison layout and nothing at all on
    a title layout.
    """
    usage: dict[int, list[str]] = {}
    for layout in catalog.usable_layouts:
        for placeholder in layout.content_placeholders:
            usage.setdefault(placeholder.idx, []).append(
                f"layout {layout.index} ({layout.name}) = {placeholder.label}"
                + (" [title]" if placeholder.is_title else "")
            )

    properties = {
        str(idx): _placeholder_value_schema("; ".join(where))
        for idx, where in sorted(usage.items())
    }
    return {
        "type": "object",
        "description": (
            "Text keyed by placeholder idx, as a string. Only use idx values that "
            "exist on the layout you chose — see each key's description. Always "
            "supply the title placeholder."
        ),
        "properties": properties,
        "additionalProperties": False,
    }


def _candidate_schema(catalog: LayoutCatalog) -> dict[str, Any]:
    indices = [layout.index for layout in catalog.usable_layouts]
    return {
        "type": "object",
        "properties": {
            "layout_index": {
                "type": "integer",
                "enum": indices,
                "description": "Slide layout to use. Available layouts:\n" + describe_layouts(catalog),
            },
            "rationale": {
                "type": "string",
                "maxLength": MAX_RATIONALE,
                "description": (
                    "One sentence on why this layout and this framing suit the brief. "
                    "Shown to the user when they pick; never rendered on the slide."
                ),
            },
            "placeholders": _placeholders_schema(catalog),
            "notes": {
                "type": "string",
                "maxLength": MAX_NOTES,
                "description": "Optional speaker notes.",
            },
        },
        "required": ["layout_index", "rationale", "placeholders"],
        "additionalProperties": False,
    }


def build_tool_schema(catalog: LayoutCatalog, n: int = 3) -> dict[str, Any]:
    """The OpenAI tool definition for `emit_candidates`, exactly `n` candidates."""
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                f"Emit exactly {n} candidate slide structures for the brief. The "
                "candidates must differ in approach — a different layout and a "
                "different way of framing the content — not merely in wording."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "minItems": n,
                        "maxItems": n,
                        "items": _candidate_schema(catalog),
                        "description": f"Exactly {n} distinct candidates.",
                    }
                },
                "required": ["candidates"],
                "additionalProperties": False,
            },
        },
    }
