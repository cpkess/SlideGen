#!/usr/bin/env python3
"""Dump the loaded template's layout catalog.

    python scripts/inspect_template.py [--template PATH] [--json]

The output is the vocabulary the model will use to choose a slide. If the
layouts read as thin or badly named here, fix the template in PowerPoint's
slide master view — not the code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from slidegen.template import LayoutCatalog, PlaceholderInfo, get_catalog  # noqa: E402

HEADERS = ("idx", "type", "name", "accepts", "role")


def _capabilities(placeholder: PlaceholderInfo) -> str:
    flags = [
        ("text", placeholder.accepts_text),
        ("picture", placeholder.accepts_picture),
        ("table", placeholder.accepts_table),
        ("chart", placeholder.accepts_chart),
    ]
    return ",".join(name for name, on in flags if on) or "-"


def _role(placeholder: PlaceholderInfo) -> str:
    if placeholder.is_furniture:
        return "furniture"
    return "title" if placeholder.is_title else ""


def _row(placeholder: PlaceholderInfo) -> tuple[str, ...]:
    return (
        str(placeholder.idx),
        placeholder.type,
        placeholder.name,
        _capabilities(placeholder),
        _role(placeholder),
    )


def print_table(catalog: LayoutCatalog) -> None:
    print(f"Template: {catalog.source}")
    print(f"Layouts:  {len(catalog.layouts)}  ({len(catalog.usable_layouts)} usable)")
    if catalog.slide_width and catalog.slide_height:
        width_in = catalog.slide_width / 914400
        height_in = catalog.slide_height / 914400
        print(f"Slide:    {width_in:.2f} x {height_in:.2f} in")
    print()

    rows = [_row(ph) for layout in catalog.layouts for ph in layout.placeholders]
    widths = [
        max([len(header)] + [len(row[column]) for row in rows])
        for column, header in enumerate(HEADERS)
    ]

    def line(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    for layout in catalog.layouts:
        suffix = "" if layout.content_placeholders else "   (no fillable text placeholder)"
        print(f"[{layout.index}] {layout.name}{suffix}")
        print("    " + line(HEADERS))
        print("    " + "  ".join("-" * width for width in widths))
        for placeholder in layout.placeholders:
            print("    " + line(_row(placeholder)))
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dump the loaded template's layout catalog.",
    )
    parser.add_argument("--template", help="Template path (defaults to SLIDEGEN_TEMPLATE)")
    parser.add_argument("--json", action="store_true", help="Emit the catalog as JSON")
    args = parser.parse_args(argv)

    try:
        catalog = get_catalog(args.template)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(catalog.model_dump(), indent=2))
    else:
        print_table(catalog)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
