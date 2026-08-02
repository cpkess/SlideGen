"""LM Studio compatibility.

A local model is a weaker instruction-follower than a hosted one, and the server
in front of it is thinner. These are the two failure modes that actually bite,
reproduced against a mocked client — LM Studio is not reachable from CI.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from slidegen.config import Settings
from slidegen.llm import factory
from slidegen.llm.base import ProviderError
from slidegen.llm.factory import get_provider
from slidegen.llm.openai_compatible import arguments_from_content
from slidegen.llm.schema import TOOL_NAME
from tests.conftest import TITLE_AND_CONTENT
from tests.test_llm_openai_compatible import (
    GOOD_CANDIDATE,
    completion,
    factory_for,
    provider,
)

ARGUMENTS = {"candidates": [GOOD_CANDIDATE]}


def prose(content: str) -> SimpleNamespace:
    """A completion with text and no tool call — the common local-model reply."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=[]))]
    )


class Rejects(Exception):
    """A server that accepts `tools` but not a forced `tool_choice`."""

    def __init__(self, status: int = 400) -> None:
        self.status_code = status
        super().__init__("Invalid 'tool_choice': named tool choice is not supported")


# --- recovering arguments from prose ---------------------------------------


def test_a_bare_json_object_is_recovered():
    assert arguments_from_content(json.dumps(ARGUMENTS)) == ARGUMENTS


def test_a_fenced_json_block_is_recovered():
    content = f"Here are the slides:\n\n```json\n{json.dumps(ARGUMENTS)}\n```\n\nHope that helps!"

    assert arguments_from_content(content) == ARGUMENTS


def test_a_tool_call_tag_is_recovered():
    body = json.dumps({"name": TOOL_NAME, "arguments": ARGUMENTS})
    content = f"<tool_call>\n{body}\n</tool_call>"

    assert arguments_from_content(content) == ARGUMENTS


def test_a_whole_tool_call_object_is_unwrapped():
    content = json.dumps({"name": TOOL_NAME, "arguments": ARGUMENTS})

    assert arguments_from_content(content) == ARGUMENTS


def test_prose_around_the_object_does_not_defeat_recovery():
    content = f"Sure! I'll use the tool.\n{json.dumps(ARGUMENTS)}\nLet me know if you want changes."

    assert arguments_from_content(content) == ARGUMENTS


def test_text_with_no_json_recovers_nothing():
    assert arguments_from_content("I'm sorry, I can't help with that.") is None


def test_json_without_candidates_recovers_nothing():
    assert arguments_from_content(json.dumps({"slides": [{"title": "x"}]})) is None


def test_malformed_json_recovers_nothing():
    assert arguments_from_content('{"candidates": [') is None


def test_a_prose_reply_still_produces_slides(catalog):
    """The generation succeeded; only the transport was wrong."""
    make = factory_for(prose(f"```json\n{json.dumps(ARGUMENTS)}\n```"))

    specs = provider(client_factory=make).generate("A brief.", catalog, 1)

    assert len(specs) == 1
    assert specs[0].layout_index == TITLE_AND_CONTENT


def test_recovered_candidates_are_still_validated(catalog):
    """The fallback is a transport workaround, not a hole in the schema."""
    bad = {"candidates": [{"layout_index": 99, "rationale": "r", "placeholders": {"0": "T"}}]}
    make = factory_for(prose(json.dumps(bad)), prose(json.dumps(bad)))

    with pytest.raises(ProviderError, match="do not fit the template"):
        provider(client_factory=make).generate("A brief.", catalog, 1)


def test_a_repair_after_a_prose_reply_sends_no_orphan_tool_message(catalog):
    """A `tool` message with no matching tool_call id is itself a 400."""
    bad = {"candidates": [{"layout_index": 99, "rationale": "r", "placeholders": {"0": "T"}}]}
    make = factory_for(prose(json.dumps(bad)), completion([GOOD_CANDIDATE]))

    provider(client_factory=make).generate("A brief.", catalog, 1)

    roles = [message["role"] for message in make.made[0].payloads[1]["messages"]]
    assert "tool" not in roles
    assert roles == ["system", "user", "assistant", "user"]


def test_a_genuinely_empty_reply_is_still_an_error(catalog):
    make = factory_for(prose(""))

    with pytest.raises(ProviderError, match="without calling the tool"):
        provider(client_factory=make).generate("A brief.", catalog, 1)


# --- forced tool_choice fallback -------------------------------------------


def test_a_forced_tool_choice_is_tried_first(catalog):
    make = factory_for()
    subject = provider(client_factory=make)

    subject.generate("A brief.", catalog, 1)

    assert make.made[0].payloads[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": TOOL_NAME},
    }
    assert subject.forces_tool_choice


def test_a_rejected_forced_choice_falls_back_to_auto(catalog):
    make = factory_for(Rejects(), completion([GOOD_CANDIDATE]))
    subject = provider(client_factory=make)

    specs = subject.generate("A brief.", catalog, 1)

    payloads = make.made[0].payloads
    assert payloads[0]["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}
    assert payloads[1]["tool_choice"] == "auto"
    assert len(specs) == 1


def test_the_fallback_is_remembered_for_later_calls(catalog):
    """One probe per process, not one per generation."""
    make = factory_for(
        Rejects(), completion([GOOD_CANDIDATE]), completion([GOOD_CANDIDATE])
    )
    subject = provider(client_factory=make)

    subject.generate("A brief.", catalog, 1)
    assert not subject.forces_tool_choice

    subject.generate("Another brief.", catalog, 1)

    assert [payload["tool_choice"] for payload in make.made[0].payloads[1:]] == ["auto", "auto"]


def test_an_unrelated_400_is_not_treated_as_a_tool_choice_problem(catalog):
    class BadRequest(Exception):
        status_code = 400

        def __str__(self) -> str:
            return "context length exceeded"

    make = factory_for(BadRequest())
    subject = provider(client_factory=make)

    with pytest.raises(ProviderError):
        subject.generate("A brief.", catalog, 1)

    assert subject.forces_tool_choice
    assert len(make.made[0].payloads) == 1


def test_a_server_error_still_retries_rather_than_downgrading(catalog):
    """503 is transient; it must not be mistaken for a capability problem."""

    class Unavailable(Exception):
        status_code = 503

        def __str__(self) -> str:
            return "tool service unavailable"

    make = factory_for(Unavailable(), completion([GOOD_CANDIDATE]))
    subject = provider(client_factory=make)

    subject.generate("A brief.", catalog, 1)

    assert subject.forces_tool_choice
    assert [payload["tool_choice"] for payload in make.made[0].payloads] == [
        {"type": "function", "function": {"name": TOOL_NAME}}
    ] * 2


# --- reachability ----------------------------------------------------------


class ModelListing:
    def __init__(self, *ids, error=None):
        self._ids = ids
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **_: None))
        self.models = SimpleNamespace(list=self._list)

    def _list(self):
        if self._error:
            raise self._error
        return SimpleNamespace(data=[SimpleNamespace(id=i) for i in self._ids])


def test_ping_lists_the_loaded_models():
    subject = provider(client_factory=lambda **_: ModelListing("qwen3-8b", "llama-3.1-8b"))

    assert subject.ping() == ["qwen3-8b", "llama-3.1-8b"]


def test_ping_reports_an_unreachable_server_with_its_url():
    subject = provider(
        base_url="http://localhost:1234/v1",
        client_factory=lambda **_: ModelListing(error=ConnectionRefusedError("refused")),
    )

    with pytest.raises(ProviderError, match="http://localhost:1234/v1"):
        subject.ping()


def test_ping_does_not_run_a_generation():
    """A trial generation on a local model can take a minute and prove nothing."""
    calls = []

    class Watched(ModelListing):
        def __init__(self, **_):
            super().__init__("qwen3-8b")
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw: calls.append(kw))
            )

    provider(client_factory=Watched).ping()

    assert calls == []


def test_the_mock_provider_answers_ping_too():
    factory.reset_cache()
    assert get_provider(Settings()).ping() == ["mock-1"]
