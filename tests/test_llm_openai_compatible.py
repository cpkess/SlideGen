"""The OpenAI-compatible provider, with the SDK client entirely mocked.

Never a live call. The two things worth guarding are the ones that fail an hour
into a session or return an opaque 500: the token-keyed client cache, and payload
hygiene.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from slidegen.config import Settings
from slidegen.llm import factory
from slidegen.llm.base import ProviderError
from slidegen.llm.factory import get_provider
from slidegen.llm.openai_compatible import (
    OpenAICompatibleProvider,
    TokenFetcher,
    clean_payload,
)
from slidegen.llm.schema import TOOL_NAME
from tests.conftest import TITLE_AND_CONTENT

@pytest.fixture(autouse=True)
def _clear_provider_cache():
    """Providers are cached per configuration; tests must not inherit each other's."""
    factory.reset_cache()
    yield
    factory.reset_cache()


GOOD_CANDIDATE = {
    "layout_index": TITLE_AND_CONTENT,
    "rationale": "Single message with supporting points.",
    "placeholders": {"0": "Adoption is up", "1": ["Growth 24%", "Churn flat"]},
}
BAD_CANDIDATE = {
    "layout_index": TITLE_AND_CONTENT,
    "rationale": "Uses an idx from the wrong layout.",
    "placeholders": {"0": "Adoption is up", "2": "Second column"},
}


def completion(candidates: list[dict], call_id: str = "call_1") -> SimpleNamespace:
    """A chat completion carrying one `emit_candidates` tool call."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id=call_id,
                            function=SimpleNamespace(
                                name=TOOL_NAME,
                                arguments=json.dumps({"candidates": candidates}),
                            ),
                        )
                    ],
                )
            )
        ]
    )


class FakeClient:
    """Stands in for `openai.OpenAI`, recording every payload it is handed."""

    def __init__(self, responses=None, **kwargs):
        self.kwargs = kwargs
        self.payloads: list[dict] = []
        self._responses = list(responses or [])
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **payload):
        self.payloads.append(payload)
        result = self._responses.pop(0) if self._responses else completion([GOOD_CANDIDATE])
        if isinstance(result, BaseException):
            raise result
        return result


def factory_for(*responses):
    """A client factory returning one FakeClient, with the given script."""
    made: list[FakeClient] = []

    def make(**kwargs):
        client = FakeClient(responses=list(responses), **kwargs)
        made.append(client)
        return client

    make.made = made
    return make


def provider(**overrides):
    defaults = dict(
        name="lmstudio",
        model="test-model",
        base_url="http://localhost:1234/v1",
        api_key="static-key",
        sleep=lambda _seconds: None,
    )
    defaults.update(overrides)
    return OpenAICompatibleProvider(**defaults)


# --- payload hygiene -------------------------------------------------------


def test_nulls_are_stripped_at_every_level():
    cleaned = clean_payload(
        {"model": "m", "temperature": None, "extra": {"a": None, "b": 1}, "list": [None, {"c": None}]}
    )

    assert "temperature" not in cleaned
    assert cleaned["extra"] == {"b": 1}
    assert cleaned["list"] == [{}]


def test_numeric_parameters_are_coerced_to_numbers():
    cleaned = clean_payload({"max_tokens": "1000", "temperature": "0.4", "top_p": 1, "seed": "7"})

    assert cleaned["max_tokens"] == 1000 and isinstance(cleaned["max_tokens"], int)
    assert cleaned["temperature"] == 0.4 and isinstance(cleaned["temperature"], float)
    assert isinstance(cleaned["top_p"], float)
    assert cleaned["seed"] == 7


def test_no_null_or_string_number_reaches_the_gateway(catalog):
    make = factory_for()
    provider(client_factory=make).generate("A brief.", catalog, 3)

    payload = make.made[0].payloads[0]
    assert isinstance(payload["temperature"], float)
    assert isinstance(payload["max_tokens"], int)
    for message in payload["messages"]:
        assert None not in message.values()


def test_streaming_is_never_requested(catalog):
    make = factory_for()
    provider(client_factory=make).generate("A brief.", catalog, 3)

    assert make.made[0].payloads[0].get("stream") in (None, False)


def test_structured_output_is_forced_through_tool_calling(catalog):
    make = factory_for()
    provider(client_factory=make).generate("A brief.", catalog, 3)

    payload = make.made[0].payloads[0]
    assert payload["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}
    assert payload["tools"][0]["function"]["name"] == TOOL_NAME
    assert "response_format" not in payload


def test_the_catalog_reaches_the_system_prompt(catalog):
    make = factory_for()
    provider(client_factory=make).generate("A brief.", catalog, 3)

    system = make.made[0].payloads[0]["messages"][0]["content"]
    assert "Title and Content" in system
    assert catalog.source in system


# --- client caching --------------------------------------------------------


def test_the_client_is_built_once_and_reused_for_a_static_key(catalog):
    make = factory_for()
    subject = provider(client_factory=make)

    subject.generate("A brief.", catalog, 3)
    subject.generate("Another brief.", catalog, 3)

    assert len(make.made) == 1


def test_the_client_is_rebuilt_when_the_token_changes(catalog):
    tokens = iter(["token-one", "token-one", "token-two"])
    fetcher = SimpleNamespace(token=lambda: next(tokens))
    make = factory_for()
    subject = provider(api_key=None, token_fetcher=fetcher, client_factory=make)

    first = subject.client()
    second = subject.client()
    third = subject.client()

    assert first is second
    assert third is not first
    assert [client.kwargs["api_key"] for client in make.made] == ["token-one", "token-two"]


def test_the_client_gets_the_base_url_and_no_sdk_level_retries(catalog):
    make = factory_for()
    subject = provider(base_url="https://gateway.example/v1", client_factory=make)

    subject.client()

    assert make.made[0].kwargs["base_url"] == "https://gateway.example/v1"
    assert make.made[0].kwargs["max_retries"] == 0


def test_a_provider_needs_a_key_or_a_fetcher():
    with pytest.raises(ValueError, match="api_key or token_fetcher"):
        OpenAICompatibleProvider(name="x", model="m", base_url="http://x/v1")


# --- token fetching --------------------------------------------------------


class Clock:
    """A hand-wound clock, so tests can say what time it is rather than count calls."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def fetcher_with(posts: list[dict]):
    clock = Clock()
    calls: list[tuple[str, dict]] = []

    def post(url, form, timeout):
        calls.append((url, form))
        return posts.pop(0)

    fetcher = TokenFetcher(
        tenant_id="tenant",
        client_id="client",
        client_secret="secret",
        scope="api://slidegen/.default",
        post=post,
        clock=clock,
    )
    return fetcher, calls, clock


def test_the_token_is_fetched_once_and_cached():
    fetcher, calls, clock = fetcher_with([{"access_token": "abc", "expires_in": 3600}])

    assert fetcher.token() == "abc"
    clock.now = 60.0
    assert fetcher.token() == "abc"
    assert len(calls) == 1


def test_the_token_is_refreshed_before_it_expires():
    """The gateway token lasts an hour; we refresh five minutes early."""
    fetcher, calls, clock = fetcher_with(
        [
            {"access_token": "first", "expires_in": 3600},
            {"access_token": "second", "expires_in": 3600},
        ]
    )

    assert fetcher.token() == "first"
    clock.now = 3290.0  # still inside the leeway window
    assert fetcher.token() == "first"
    clock.now = 3310.0  # past expiry minus leeway
    assert fetcher.token() == "second"
    assert len(calls) == 2


def test_the_token_request_uses_client_credentials():
    fetcher, calls, _ = fetcher_with([{"access_token": "abc", "expires_in": 3600}])

    fetcher.token()

    url, form = calls[0]
    assert url == "https://login.microsoftonline.com/tenant/oauth2/v2.0/token"
    assert form["grant_type"] == "client_credentials"
    assert form["scope"] == "api://slidegen/.default"


def test_a_missing_access_token_is_reported_clearly():
    fetcher, _, _ = fetcher_with([{"error": "invalid_client"}])

    with pytest.raises(ProviderError, match="did not return an access token"):
        fetcher.token()


# --- generation ------------------------------------------------------------


def test_candidates_come_back_as_specs(catalog):
    make = factory_for(completion([GOOD_CANDIDATE, GOOD_CANDIDATE, GOOD_CANDIDATE]))

    specs = provider(client_factory=make).generate("A brief.", catalog, 3)

    assert len(specs) == 3
    assert specs[0].layout_index == TITLE_AND_CONTENT
    assert specs[0].placeholders == {0: "Adoption is up", 1: ["Growth 24%", "Churn flat"]}


def test_an_invalid_candidate_triggers_one_repair_call(catalog):
    make = factory_for(completion([BAD_CANDIDATE]), completion([GOOD_CANDIDATE]))

    specs = provider(client_factory=make).generate("A brief.", catalog, 1)

    client = make.made[0]
    assert len(client.payloads) == 2
    assert specs[0].placeholders == {0: "Adoption is up", 1: ["Growth 24%", "Churn flat"]}


def test_the_repair_call_carries_the_rejected_tool_call_and_the_errors(catalog):
    make = factory_for(completion([BAD_CANDIDATE]), completion([GOOD_CANDIDATE]))

    provider(client_factory=make).generate("A brief.", catalog, 1)

    messages = make.made[0].payloads[1]["messages"]
    roles = [message["role"] for message in messages]
    assert roles == ["system", "user", "assistant", "tool", "user"]
    assert messages[2]["tool_calls"][0]["function"]["name"] == TOOL_NAME
    assert "idx 2 does not exist on layout 1" in messages[-1]["content"]


def test_a_reply_without_a_tool_call_is_a_clear_error(catalog):
    prose = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="Here are some slides!", tool_calls=[]))]
    )
    make = factory_for(prose)

    with pytest.raises(ProviderError, match="without calling the tool"):
        provider(client_factory=make).generate("A brief.", catalog, 3)


def test_malformed_tool_arguments_are_a_clear_error(catalog):
    broken = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="c1", function=SimpleNamespace(name=TOOL_NAME, arguments="{not json")
                        )
                    ],
                )
            )
        ]
    )
    make = factory_for(broken)

    with pytest.raises(ProviderError, match="not valid JSON"):
        provider(client_factory=make).generate("A brief.", catalog, 3)


def test_an_empty_candidate_list_is_a_clear_error(catalog):
    make = factory_for(completion([]))

    with pytest.raises(ProviderError, match="no candidates"):
        provider(client_factory=make).generate("A brief.", catalog, 3)


# --- retries ---------------------------------------------------------------


class Transient(Exception):
    def __init__(self, status: int) -> None:
        self.status_code = status
        super().__init__(f"HTTP {status}")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_transient_failures_are_retried(catalog, status):
    make = factory_for(Transient(status), completion([GOOD_CANDIDATE]))

    specs = provider(client_factory=make).generate("A brief.", catalog, 1)

    assert len(specs) == 1
    assert len(make.made[0].payloads) == 2


def test_a_client_error_is_not_retried(catalog):
    make = factory_for(Transient(400), Transient(400), Transient(400))

    with pytest.raises(ProviderError):
        provider(client_factory=make).generate("A brief.", catalog, 1)

    assert len(make.made[0].payloads) == 1


def test_retries_are_bounded(catalog):
    make = factory_for(*[Transient(503)] * 5)

    with pytest.raises(ProviderError):
        provider(client_factory=make, max_attempts=3).generate("A brief.", catalog, 1)

    assert len(make.made[0].payloads) == 3


# --- factory ---------------------------------------------------------------


def test_the_secret_gateway_is_the_default_tier():
    settings = Settings(
        llm_provider="ford",
        ford_base_url="https://standard.example/v1",
        ford_base_url_secret="https://secret.example/v1",
        fordllm_client_id="id",
        fordllm_client_secret="secret",
        fordllm_tenant_id="tenant",
    )

    assert settings.ford_tier == "secret"
    assert get_provider(settings).base_url == "https://secret.example/v1"


def test_the_standard_tier_selects_the_standard_gateway():
    settings = Settings(
        llm_provider="ford",
        ford_tier="standard",
        ford_base_url="https://standard.example/v1",
        ford_base_url_secret="https://secret.example/v1",
        fordllm_client_id="id",
        fordllm_client_secret="secret",
        fordllm_tenant_id="tenant",
    )

    assert get_provider(settings).base_url == "https://standard.example/v1"


def test_an_unconfigured_ford_provider_explains_what_is_missing():
    settings = Settings(llm_provider="ford", ford_base_url_secret="https://secret.example/v1")

    with pytest.raises(ProviderError, match="FORDLLM_CLIENT_ID"):
        get_provider(settings)


def test_a_missing_gateway_url_names_the_variable():
    settings = Settings(
        llm_provider="ford",
        ford_tier="standard",
        fordllm_client_id="id",
        fordllm_client_secret="secret",
        fordllm_tenant_id="tenant",
    )

    with pytest.raises(ProviderError, match="FORD_BASE_URL"):
        get_provider(settings)


def test_providers_are_cached_so_the_token_fetcher_survives():
    settings = Settings(
        llm_provider="ford",
        ford_base_url_secret="https://secret.example/v1",
        fordllm_client_id="id",
        fordllm_client_secret="secret",
        fordllm_tenant_id="tenant",
    )

    assert get_provider(settings) is get_provider(settings)


def test_only_configured_providers_are_offered():
    assert Settings().enabled_providers() == ["mock", "lmstudio"]

    configured = Settings(
        ford_base_url_secret="https://secret.example/v1",
        fordllm_client_id="id",
        fordllm_client_secret="secret",
        fordllm_tenant_id="tenant",
    )
    assert "ford" in configured.enabled_providers()


def test_lmstudio_uses_its_base_url_and_a_static_key():
    subject = get_provider(Settings(llm_provider="lmstudio"))

    assert subject.name == "lmstudio"
    assert subject.base_url == "http://localhost:1234/v1"


def test_the_mock_provider_is_the_default():
    assert get_provider(Settings()).name == "mock"
