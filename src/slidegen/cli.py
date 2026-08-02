"""Command line entry point, for scripted use.

    slidegen --brief "..." --out deck.pptx
    slidegen --brief-file brief.txt --list
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import get_settings
from .guards import check_specs
from .llm.base import ProviderError
from .llm.factory import get_provider
from .logging_config import configure_logging
from .render import render
from .spec import SlideSpec
from .template import load_catalog


def _read_brief(args: argparse.Namespace) -> str:
    if args.brief_file:
        return Path(args.brief_file).read_text(encoding="utf-8")
    if args.brief == "-":
        return sys.stdin.read()
    return args.brief or ""


def _describe(spec: SlideSpec, catalog, position: int) -> str:
    layout = catalog.layout(spec.layout_index)
    name = layout.name if layout else f"layout {spec.layout_index}"
    lines = [f"[{position}] {name} (layout_index {spec.layout_index})"]
    if spec.rationale:
        lines.append(f"    {spec.rationale}")
    for idx, value in spec.placeholders.items():
        placeholder = layout.placeholder(idx) if layout else None
        label = placeholder.label if placeholder else f"idx {idx}"
        text = value if isinstance(value, str) else " • ".join(value)
        lines.append(f"    {label}: {text[:160]}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slidegen",
        description="Turn a brief into editable PowerPoint slides.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--brief", help="The brief text, or '-' to read stdin.")
    source.add_argument("--brief-file", help="Read the brief from a file.")

    parser.add_argument("--out", "-o", help="Write the .pptx here.")
    parser.add_argument(
        "--candidate",
        "-c",
        type=int,
        default=1,
        help="Which candidate to render, 1-based (default: 1).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Render every candidate into one deck, one slide each.",
    )
    parser.add_argument("--template", help="Template path (defaults to SLIDEGEN_TEMPLATE).")
    parser.add_argument("--provider", help="Override LLM_PROVIDER for this run.")
    parser.add_argument("--model", help="Override LLM_MODEL for this run.")
    parser.add_argument(
        "-n", "--candidates", type=int, default=None, help="How many candidates to request."
    )
    parser.add_argument("--list", action="store_true", help="Print candidates and exit.")
    parser.add_argument("--json", action="store_true", help="Print candidates as JSON.")
    parser.add_argument("--quiet", "-q", action="store_true", help="Log warnings only.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(logging.WARNING if args.quiet else logging.INFO)

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})
    count = args.candidates or settings.candidate_count

    brief = _read_brief(args)
    if not brief.strip():
        print("error: the brief is empty", file=sys.stderr)
        return 2

    try:
        presentation, catalog = load_catalog(args.template)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        specs = get_provider(settings).generate(brief, catalog, count)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([spec.model_dump() for spec in specs], indent=2))
    else:
        for position, spec in enumerate(specs, start=1):
            print(_describe(spec, catalog, position))
            print()

    if args.list:
        return 0

    if args.all:
        chosen = specs
    else:
        if not 1 <= args.candidate <= len(specs):
            print(
                f"error: --candidate {args.candidate} is out of range; "
                f"{len(specs)} candidates were returned",
                file=sys.stderr,
            )
            return 2
        chosen = [specs[args.candidate - 1]]

    for warning in check_specs(chosen, catalog):
        print(f"warning: {warning.message}", file=sys.stderr)

    data = render(chosen, catalog, presentation)

    if not args.out:
        print("error: --out is required unless --list is given", file=sys.stderr)
        return 2

    destination = Path(args.out)
    destination.write_bytes(data)
    print(f"Wrote {destination} ({len(data):,} bytes, {len(chosen)} slide(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
