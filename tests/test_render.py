"""Render, then re-open the bytes and assert what actually landed in the file.

We cannot open PowerPoint here, so structural verification with python-pptx is
the standard: right layout, right placeholders, text present and unmangled.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from pptx import Presentation

from slidegen.render import build, render
from slidegen.spec import SlideSpec, SpecValidationError
from tests.conftest import TITLE_AND_CONTENT, TWO_CONTENT

TITLE_SLIDE = 0
SECTION_HEADER = 2


def reopen(data: bytes) -> Presentation:
    return Presentation(BytesIO(data))


def text_by_idx(slide) -> dict[int, str]:
    return {
        placeholder.placeholder_format.idx: placeholder.text_frame.text
        for placeholder in slide.placeholders
    }


def test_one_slide_per_spec_with_the_chosen_layouts(catalog, presentation):
    specs = [
        SlideSpec(layout_index=TITLE_SLIDE, placeholders={0: "Connected services", 1: "Q3 review"}),
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Where we are", 1: ["Up", "Flat"]}),
        SlideSpec(layout_index=SECTION_HEADER, placeholders={0: "Risks", 1: "Onboarding capacity"}),
    ]

    deck = reopen(render(specs, catalog, presentation))

    assert len(deck.slides) == 3
    assert [slide.slide_layout.name for slide in deck.slides] == [
        "Title Slide",
        "Title and Content",
        "Section Header",
    ]


def test_a_string_lands_as_one_paragraph(catalog, presentation):
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: "A paragraph."})

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    body = slide.placeholders[1].text_frame
    assert len(body.paragraphs) == 1
    assert body.paragraphs[0].text == "A paragraph."


def test_a_list_lands_as_bullets_at_level_zero(catalog, presentation):
    spec = SlideSpec(
        layout_index=TITLE_AND_CONTENT,
        placeholders={0: "Title", 1: ["First", "Second", "Third"]},
    )

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    body = slide.placeholders[1].text_frame
    assert [p.text for p in body.paragraphs] == ["First", "Second", "Third"]
    assert {p.level for p in body.paragraphs} == {0}


def test_text_lands_in_the_right_placeholder_on_a_multi_slot_layout(catalog, presentation):
    spec = SlideSpec(
        layout_index=TWO_CONTENT,
        placeholders={0: "Comparison", 1: ["Left one", "Left two"], 2: ["Right one"]},
    )

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    text = text_by_idx(slide)
    assert text[0] == "Comparison"
    assert text[1] == "Left one\nLeft two"
    assert text[2] == "Right one"


def test_text_is_not_mangled(catalog, presentation):
    tricky = "Ampersand & angle < > “curly” — em-dash, 24% ünïcode"
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: tricky, 1: "Body"})

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    assert text_by_idx(slide)[0] == tricky


def test_speaker_notes_are_written(catalog, presentation):
    spec = SlideSpec(
        layout_index=TITLE_AND_CONTENT,
        placeholders={0: "Title", 1: "Body"},
        notes="Mention the dealer capacity risk.",
    )

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    assert slide.notes_slide.notes_text_frame.text == "Mention the dealer capacity risk."


def test_no_slide_carries_an_empty_required_placeholder(catalog, presentation):
    specs = [
        SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: ["A"]}),
        SlideSpec(layout_index=TWO_CONTENT, placeholders={0: "Title", 1: ["A"], 2: ["B"]}),
    ]

    for slide in reopen(render(specs, catalog, presentation)).slides:
        title = slide.shapes.title
        assert title is not None
        assert title.text_frame.text.strip()


def test_the_renderer_sets_no_geometry(catalog, presentation):
    """Position and size must come from the layout, never from us."""
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: "Body"})

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    for placeholder in slide.placeholders:
        element = placeholder._element  # noqa: SLF001
        assert element.spPr.find(f"{{{element.nsmap['a']}}}xfrm") is None


def test_the_renderer_sets_no_font_or_colour(catalog, presentation):
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: ["A", "B"]})

    slide = reopen(render([spec], catalog, presentation)).slides[0]

    for placeholder in slide.placeholders:
        for paragraph in placeholder.text_frame.paragraphs:
            for run in paragraph.runs:
                assert run.font.size is None
                assert run.font.name is None
                assert run.font.color.type is None


def test_rendering_twice_from_one_template_does_not_accumulate(catalog, presentation):
    """The Streamlit app caches one template across reruns."""
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: "Body"})

    first = reopen(render([spec], catalog, presentation))
    second = reopen(render([spec, spec], catalog, presentation))

    assert (len(first.slides), len(second.slides)) == (1, 2)


def test_existing_slides_in_a_pptx_template_are_dropped(catalog, presentation, tmp_path):
    seeded = build(
        [SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Pre-existing", 1: "x"})],
        catalog,
        presentation,
    )
    path = tmp_path / "seeded.pptx"
    seeded.save(path)

    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "New", 1: "y"})
    deck = reopen(render([spec], catalog, str(path)))

    assert len(deck.slides) == 1
    assert deck.slides[0].shapes.title.text_frame.text == "New"


def test_render_accepts_a_template_path(catalog, tmp_path):
    spec = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "From path", 1: "Body"})

    deck = reopen(render([spec], catalog, None))

    assert deck.slides[0].shapes.title.text_frame.text == "From path"


def test_render_validates_before_writing(catalog, presentation):
    with pytest.raises(SpecValidationError):
        render([SlideSpec(layout_index=99, placeholders={0: "Title"})], catalog, presentation)


def test_an_empty_deck_renders(catalog, presentation):
    assert len(reopen(render([], catalog, presentation)).slides) == 0
