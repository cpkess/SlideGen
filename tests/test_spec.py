"""Validation is the contract between the model and the renderer.

Every message here is written to be handed back to a model verbatim, so the
tests check that they name both the offending value and the alternatives.
"""

from __future__ import annotations

import pytest

from slidegen.spec import (
    MAX_BULLETS,
    SlideSpec,
    SpecValidationError,
    ensure_valid,
    validate_spec,
    validate_specs,
)
from tests.conftest import BLANK, TITLE_AND_CONTENT, TWO_CONTENT


def test_a_well_formed_spec_validates(catalog):
    spec = SlideSpec(
        layout_index=TITLE_AND_CONTENT,
        rationale="A single message with supporting points.",
        placeholders={0: "Adoption is up", 1: ["Growth", "Churn flat"]},
    )

    assert validate_spec(spec, catalog) == []


def test_unknown_layout_index_lists_the_valid_ones(catalog):
    errors = validate_spec(SlideSpec(layout_index=99, placeholders={0: "x"}), catalog)

    assert len(errors) == 1
    assert "99" in errors[0]
    assert "Title and Content" in errors[0]


def test_layout_without_text_placeholders_is_rejected(catalog):
    errors = validate_spec(SlideSpec(layout_index=BLANK, placeholders={}), catalog)

    assert len(errors) == 1
    assert "no text placeholder" in errors[0]


def test_unknown_placeholder_idx_lists_the_valid_ones(catalog):
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 7: "Nowhere"})

    errors = validate_spec(spec, catalog)

    assert len(errors) == 1
    assert "idx 7" in errors[0]
    assert "Content Placeholder 2" in errors[0]


def test_placeholder_from_another_layout_is_rejected(catalog):
    """idx 2 is real on Two Content and absent on Title and Content."""
    assert catalog.layout(TWO_CONTENT).placeholder(2) is not None

    errors = validate_spec(
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 2: "Second column"}),
        catalog,
    )

    assert any("idx 2 does not exist on layout 1" in error for error in errors)


def test_furniture_placeholder_is_rejected_with_a_reason(catalog):
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 11: "Footer text"})

    errors = validate_spec(spec, catalog)

    assert len(errors) == 1
    assert "slide furniture" in errors[0]


def test_missing_title_is_rejected(catalog):
    errors = validate_spec(
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={1: ["A point"]}), catalog
    )

    assert len(errors) == 1
    assert "requires a title" in errors[0]
    assert "idx 0" in errors[0]


@pytest.mark.parametrize("empty", ["", "   ", []], ids=["blank", "whitespace", "no-bullets"])
def test_empty_title_is_rejected(catalog, empty):
    errors = validate_spec(
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: empty, 1: ["A point"]}),
        catalog,
    )

    assert any("is empty" in error for error in errors)


def test_empty_body_is_rejected(catalog):
    errors = validate_spec(
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: "  "}), catalog
    )

    assert len(errors) == 1
    assert "omit the key entirely" in errors[0]


def test_too_many_bullets_is_rejected(catalog):
    spec = SlideSpec(
        layout_index=TITLE_AND_CONTENT,
        placeholders={0: "Title", 1: [f"Point {i}" for i in range(MAX_BULLETS + 1)]},
    )

    errors = validate_spec(spec, catalog)

    assert len(errors) == 1
    assert str(MAX_BULLETS + 1) in errors[0]


def test_a_layout_with_no_title_needs_no_title(catalog):
    """Not every template has a title on every layout; validation must not assume one."""
    layout = next(
        (layout for layout in catalog.usable_layouts if layout.title_placeholder is None), None
    )
    if layout is None:
        pytest.skip("built-in template puts a title on every usable layout")

    idx = layout.content_placeholders[0].idx
    assert validate_spec(SlideSpec(layout_index=layout.index, placeholders={idx: "Text"}), catalog) == []


def test_validate_specs_reports_by_position(catalog):
    good = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title"})
    bad = SlideSpec(layout_index=99, placeholders={0: "Title"})

    failures = validate_specs([good, bad, good], catalog)

    assert set(failures) == {1}


def test_ensure_valid_raises_with_numbered_candidates(catalog):
    bad = SlideSpec(layout_index=99, placeholders={0: "Title"})

    with pytest.raises(SpecValidationError) as excinfo:
        ensure_valid([bad], catalog)

    assert excinfo.value.errors[0].startswith("candidate 1:")
    assert excinfo.value.by_spec == {0: [excinfo.value.errors[0].removeprefix("candidate 1: ")]}


def test_placeholder_keys_coerce_from_json_strings():
    """Tool-call arguments arrive as JSON, where object keys are always strings."""
    spec = SlideSpec.model_validate(
        {"layout_index": 1, "rationale": "r", "placeholders": {"0": "Title", "1": ["a", "b"]}}
    )

    assert spec.placeholders == {0: "Title", 1: ["a", "b"]}


def test_text_for_flattens_bullets():
    spec = SlideSpec(layout_index=1, placeholders={1: ["a", "b"]})

    assert spec.text_for(1) == "a\nb"
    assert spec.text_for(99) == ""
