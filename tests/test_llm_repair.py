"""The repair loop: one retry, carrying the specific validation errors."""

from __future__ import annotations

import pytest

from slidegen.llm.base import ProviderError
from slidegen.llm.repair import generate_with_repair
from slidegen.spec import SlideSpec
from tests.conftest import TITLE_AND_CONTENT

GOOD = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 1: ["A point"]})
BAD_LAYOUT = SlideSpec(layout_index=99, placeholders={0: "Title"})
BAD_IDX = SlideSpec(layout_index=TITLE_AND_CONTENT, placeholders={0: "Title", 4: "Nowhere"})


class StubProvider:
    """Returns the next scripted batch on each call, recording the feedback it got."""

    def __init__(self, *batches: list[SlideSpec]) -> None:
        self.batches = list(batches)
        self.feedback: list[list[str] | None] = []

    def __call__(self, feedback: list[str] | None) -> list[SlideSpec]:
        self.feedback.append(feedback)
        return self.batches.pop(0)


def test_a_valid_first_answer_is_returned_unchanged(catalog):
    stub = StubProvider([GOOD])

    assert generate_with_repair(stub, catalog) == [GOOD]
    assert stub.feedback == [None]


def test_one_bad_spec_then_a_good_one(catalog):
    stub = StubProvider([BAD_IDX], [GOOD])

    specs = generate_with_repair(stub, catalog)

    assert specs == [GOOD]
    assert len(stub.feedback) == 2


def test_the_repair_call_receives_the_specific_errors(catalog):
    stub = StubProvider([BAD_IDX], [GOOD])

    generate_with_repair(stub, catalog)

    feedback = stub.feedback[1]
    assert feedback is not None
    assert "candidate 1:" in feedback[0]
    assert "idx 4 does not exist on layout 1" in feedback[0]
    assert "Content Placeholder 2" in feedback[0]


def test_feedback_numbers_the_offending_candidate(catalog):
    stub = StubProvider([GOOD, BAD_LAYOUT], [GOOD, GOOD])

    generate_with_repair(stub, catalog)

    assert stub.feedback[1][0].startswith("candidate 2:")


def test_two_bad_answers_raise_with_the_errors_included(catalog):
    stub = StubProvider([BAD_LAYOUT], [BAD_LAYOUT])

    with pytest.raises(ProviderError) as excinfo:
        generate_with_repair(stub, catalog, provider="stub")

    assert "correction attempt did not fix them" in str(excinfo.value)
    assert "layout_index 99 does not exist" in str(excinfo.value)
    assert excinfo.value.provider == "stub"
    assert len(stub.feedback) == 2


def test_max_repairs_zero_never_retries(catalog):
    stub = StubProvider([BAD_LAYOUT])

    with pytest.raises(ProviderError):
        generate_with_repair(stub, catalog, max_repairs=0)

    assert stub.feedback == [None]


def test_more_repairs_are_allowed_when_asked_for(catalog):
    stub = StubProvider([BAD_LAYOUT], [BAD_LAYOUT], [GOOD])

    assert generate_with_repair(stub, catalog, max_repairs=2) == [GOOD]
