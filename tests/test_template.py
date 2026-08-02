"""The catalog is the vocabulary everything else speaks. It has to be right."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slidegen.config import get_settings
from slidegen.template import BUILT_IN, get_catalog, load_template, resolve_template_path
from tests.conftest import BLANK, BUILT_IN_LAYOUT_COUNT, TITLE_AND_CONTENT, TWO_CONTENT

from scripts.inspect_template import main as inspect_main


def test_built_in_template_layout_count(catalog):
    assert len(catalog.layouts) == BUILT_IN_LAYOUT_COUNT
    assert catalog.source == BUILT_IN
    assert catalog.indices == list(range(BUILT_IN_LAYOUT_COUNT))


def test_title_and_content_exposes_title_and_body(catalog):
    layout = catalog.layout(TITLE_AND_CONTENT)

    assert layout is not None
    assert layout.name == "Title and Content"

    title = layout.title_placeholder
    assert title is not None
    assert title.is_title and title.accepts_text

    body = next(p for p in layout.content_placeholders if not p.is_title)
    assert body.accepts_text
    assert body.idx != title.idx


def test_content_placeholder_accepts_more_than_text(catalog):
    body = catalog.layout(TITLE_AND_CONTENT).placeholder(1)

    assert (body.accepts_text, body.accepts_picture) == (True, True)
    assert (body.accepts_table, body.accepts_chart) == (True, True)


def test_picture_placeholder_does_not_accept_text(catalog):
    picture = next(
        placeholder
        for layout in catalog.layouts
        for placeholder in layout.placeholders
        if placeholder.type == "PICTURE"
    )

    assert picture.accepts_picture
    assert not picture.accepts_text


def test_slide_furniture_is_marked_and_excluded_from_content(catalog):
    layout = catalog.layout(TITLE_AND_CONTENT)
    furniture = [p for p in layout.placeholders if p.is_furniture]

    assert {p.type for p in furniture} == {"DATE", "FOOTER", "SLIDE_NUMBER"}
    assert not any(p.accepts_text for p in furniture)
    assert not any(p.is_furniture for p in layout.content_placeholders)


def test_blank_layout_is_not_usable(catalog):
    assert catalog.layout(BLANK).content_placeholders == []
    assert BLANK not in [layout.index for layout in catalog.usable_layouts]
    assert len(catalog.usable_layouts) == BUILT_IN_LAYOUT_COUNT - 1


def test_two_content_has_two_body_placeholders(catalog):
    slots = [p for p in catalog.layout(TWO_CONTENT).content_placeholders if not p.is_title]

    assert len(slots) == 2


def test_placeholders_carry_layout_geometry(catalog):
    body = catalog.layout(TITLE_AND_CONTENT).placeholder(1)

    assert body.width and body.width > 0
    assert body.height and body.height > 0


def test_missing_template_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_template(tmp_path / "nope.potx")


def test_resolve_template_path_prefers_argument_over_environment(monkeypatch):
    monkeypatch.setenv("SLIDEGEN_TEMPLATE", "/from/env.potx")
    get_settings.cache_clear()

    assert resolve_template_path("/explicit.potx") == Path("/explicit.potx")
    assert resolve_template_path(None) == Path("/from/env.potx")


def test_empty_template_setting_means_built_in():
    assert resolve_template_path("") is None
    assert resolve_template_path("   ") is None


def test_catalog_round_trips_through_json(catalog):
    restored = type(catalog).model_validate(json.loads(catalog.model_dump_json()))

    assert restored == catalog


def test_inspector_prints_a_table(capsys):
    assert inspect_main([]) == 0

    out = capsys.readouterr().out
    assert "Title and Content" in out
    assert "furniture" in out


def test_inspector_json_matches_the_catalog(capsys):
    assert inspect_main(["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == get_catalog(None).model_dump()


def test_inspector_reports_a_missing_template(capsys, tmp_path):
    assert inspect_main(["--template", str(tmp_path / "nope.potx")]) == 2
    assert "error" in capsys.readouterr().err
