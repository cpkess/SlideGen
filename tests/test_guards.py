"""The overflow guard warns; it never blocks."""

from __future__ import annotations

from slidegen.guards import check_spec, check_specs
from slidegen.spec import SlideSpec
from tests.conftest import TITLE_AND_CONTENT, TWO_CONTENT

COMPARISON = 4  # title + two small caption boxes above two content boxes


def spec(**placeholders) -> SlideSpec:
    return SlideSpec(
        layout_index=TITLE_AND_CONTENT,
        placeholders={int(k.lstrip("p")): v for k, v in placeholders.items()},
    )


def test_reasonable_text_produces_no_warning(catalog):
    assert check_spec(spec(p0="Adoption is up", p1=["Growth 24%", "Churn flat"]), catalog) == []


def test_a_long_title_is_flagged(catalog):
    warnings = check_spec(spec(p0="Adoption " * 40, p1="Body"), catalog)

    assert [warning.idx for warning in warnings] == [0]
    assert warnings[0].label == "Title 1"
    assert warnings[0].estimated_lines > warnings[0].capacity_lines


def test_too_much_body_text_is_flagged(catalog):
    warnings = check_spec(spec(p0="Title", p1=["A long supporting point. " * 12] * 8), catalog)

    assert [warning.idx for warning in warnings] == [1]


def test_the_warning_says_what_to_do(catalog):
    warning = check_spec(spec(p0="Title", p1=["Padding " * 30] * 10), catalog)[0]

    assert "Content Placeholder 2" in warning.message
    assert "trim it" in warning.message


def test_each_bullet_costs_a_line_even_when_short(catalog):
    """Five one-word bullets do not fit a two-line caption box."""
    caption = SlideSpec(
        layout_index=COMPARISON,
        placeholders={0: "Title", 1: [f"Point {i}" for i in range(5)], 2: ["Detail"]},
    )

    warnings = check_spec(caption, catalog)

    assert [warning.idx for warning in warnings] == [1]


def test_bullets_that_fit_are_left_alone(catalog):
    """The same layout, with a bullet count the caption box can hold."""
    caption = SlideSpec(
        layout_index=COMPARISON,
        placeholders={0: "Title", 1: ["Point one"], 2: ["Detail"]},
    )

    assert check_spec(caption, catalog) == []


def test_the_guard_is_per_placeholder_not_per_slide(catalog):
    long_text = "Padding " * 40
    subject = SlideSpec(
        layout_index=TWO_CONTENT,
        placeholders={0: "Title", 1: ["Short"], 2: [long_text] * 8},
    )

    warnings = check_spec(subject, catalog)

    assert [warning.idx for warning in warnings] == [2]


def test_a_narrow_column_holds_less_than_a_full_width_one(catalog):
    """The estimate reads the layout's own geometry, so a column warns sooner."""
    text = ["Padding " * 12] * 7

    wide = check_spec(SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "T", 1: text}), catalog)
    narrow = check_spec(SlideSpec(layout_index=TWO_CONTENT, placeholders={0: "T", 1: text}), catalog)

    assert not wide
    assert narrow


def test_warnings_are_numbered_by_slide(catalog):
    fine = spec(p0="Title", p1=["Short"])
    over = spec(p0="Title", p1=["Padding " * 30] * 10)

    warnings = check_specs([fine, over, over], catalog)

    assert [warning.slide for warning in warnings] == [2, 3]


def test_an_unknown_layout_is_not_the_guards_problem(catalog):
    """Validation reports that; the guard just stays quiet."""
    assert check_spec(SlideSpec(layout_index=99, placeholders={0: "Title"}), catalog) == []


def test_an_empty_deck_produces_no_warnings(catalog):
    assert check_specs([], catalog) == []
