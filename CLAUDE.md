# CLAUDE.md

Context and rules for agent sessions on this repository. Read before making changes.

## What this project is

SlideGen turns a text brief into editable PowerPoint slides. The corporate `.pptx` template supplies all visual design; this codebase selects a slide layout and fills its placeholders. **We do not implement layout, typography, spacing, or color.** If you find yourself computing positions in inches or setting font sizes, stop — the answer is almost always a different slide layout in the template.

## Hard rules

1. **No Ford assets, ever.** No `.potx`/`.pptx` template files, no real briefs, no product names, timing, or pricing in code, tests, fixtures, or commit messages. Development uses the `python-pptx` built-in template.
2. **No credentials in the repo.** `.env` is gitignored. `.env.example` holds names with empty values only.
3. **Tests run offline.** `pytest` must pass with no network and `LLM_PROVIDER=mock`. Never write a test that requires a live model.
4. **The layout catalog is derived, never hardcoded.** No literal layout indices or placeholder `idx` values outside of `template.py` and test fixtures. Everything reads from `LayoutCatalog` at runtime.
5. **The LLM emits semantics only.** It picks a layout and supplies text. It never emits coordinates, sizes, colors, or fonts. The tool schema must make those unrepresentable.
6. **Providers are interchangeable.** FordLLM is OpenAI-SDK-compatible. LM Studio is too. One `openai_compatible` implementation serves both; they differ only in `base_url` and auth.

## Environment

- Python 3.11+, `uv` preferred
- Core deps: `python-pptx`, `openai`, `pydantic`, `streamlit`, `python-dotenv`
- Dev deps: `pytest`

You cannot reach the Ford network from this sandbox. The `ford` provider is written against the OpenAI SDK interface and verified by unit tests with a mocked client — never by a live call.

You cannot open PowerPoint. Verify generated files by re-reading them with `python-pptx` and asserting structure: correct layout applied, expected placeholders populated, no empty required placeholder, text present and unmangled. Visual quality is checked by a human on their own machine.

## FordLLM specifics

- OpenAI-compatible: `OpenAI(api_key=token, base_url=...)`
- AAD client-credentials token, **expires every 60 minutes**
- `TokenFetcher` self-refreshes and is never re-instantiated, but the **OpenAI client must be rebuilt whenever the token value changes** — cache the client keyed on the token string, guarded by a lock
- **Never send `None` for numeric params.** `temperature=None` or `max_tokens="1000"` returns a 500 Avro schema validation error. Strip nulls and coerce types before every call.
- Two gateways: standard and secret-data. **Secret is the default.** Briefs contain unannounced product information.
- Do not use streaming (different base URL, and we generate a small JSON object)
- Force structured output with **tool calling**, not `response_format` — function calling is documented and supported; `json_schema` response format is not

## Conventions

- Type hints everywhere; `pydantic` models for all data crossing a boundary
- Structured logging to stdout via `logging`, JSON formatter — request id, provider, model, latency, retries, outcome
- Small, single-purpose commits; one PR per task
- Prefer standard library and existing deps over new packages

## Definition of done for any task

- `pytest` passes offline
- New behavior has a test
- No new hardcoded layout or placeholder values
- `README.md` updated if configuration or usage changed
- PR description states what a human should verify by opening the generated file
