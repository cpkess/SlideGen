# Delegated build tasks

One session per task. Each ends in a PR you review. Paste the prompt as-is — `CLAUDE.md` supplies the rest of the context.

Run them in order. Each has a **you verify** step; do it before starting the next one.

---

## Task 1 — Scaffold and template inspector

> Set up the project skeleton: `pyproject.toml` (Python 3.11, deps `python-pptx`, `openai`, `pydantic`, `streamlit`, `python-dotenv`; dev dep `pytest`), `.gitignore`, `.env.example`, and `src/slidegen/config.py` loading the settings documented in README.md via pydantic-settings.
>
> Then implement `src/slidegen/template.py`. It loads a template from `SLIDEGEN_TEMPLATE`, falling back to the `python-pptx` built-in, and produces a `LayoutCatalog`: for each slide layout, its index, name, and every placeholder with `idx`, placeholder type, name, and whether it accepts text, picture, table, or chart. Model it with pydantic.
>
> Add `scripts/inspect_template.py` printing the catalog as a readable table, plus `--json`.
>
> Tests: assert the built-in template yields the expected number of layouts and that a known layout exposes title and body placeholders.

**You verify:** run the inspector against the real Ford template on your machine. The output is the vocabulary the model will use — if the layouts are thin or badly named, that's the constraint to fix first, in PowerPoint, not in code.

---

## Task 2 — Spec model and renderer

> Implement `src/slidegen/spec.py` and `src/slidegen/render.py`.
>
> `SlideSpec` is a pydantic model: `layout_index: int`, `rationale: str`, `placeholders: dict[int, str | list[str]]` keyed by placeholder `idx`, and optional `notes: str` for speaker notes. A `str` fills a text frame as a paragraph; a `list[str]` fills it as bullets at level 0.
>
> Validation runs against a `LayoutCatalog`: the layout index must exist, every placeholder idx must exist on that layout and accept text, and a required title placeholder must not be empty. Validation errors must be specific enough to hand back to a model for repair — include the offending value and the valid alternatives.
>
> `render(specs, catalog, template) -> bytes` adds one slide per spec and returns the `.pptx` as bytes. Do not set any position, size, font, or color.
>
> Tests: render several hand-written specs against the built-in template, re-open the result with `python-pptx`, and assert the layout applied and the text landed in the right placeholders. Cover the validation failure cases.

**You verify:** open the generated file. This is the go/no-go gate — if a filled template slide doesn't look right, the LLM layer can't rescue it. Fix the template before continuing.

---

## Task 3 — LLM layer with a mock provider

> Implement `src/slidegen/llm/`.
>
> `base.py`: a `Provider` protocol with `generate(brief: str, catalog: LayoutCatalog, n: int = 3) -> list[SlideSpec]`.
>
> `schema.py`: build an OpenAI tool schema from a `LayoutCatalog`. The tool is `emit_candidates`, taking exactly `n` candidates, each a layout index constrained by enum to the catalog's real indices, plus placeholder text keyed by valid idx. Include each layout's name and placeholder names in the descriptions so the model can choose meaningfully. The schema must make coordinates, fonts, and colors unrepresentable. Set `additionalProperties: false` and a sensible `maxLength` on every string.
>
> `mock.py`: deterministic, offline. Returns three valid specs derived from the catalog and the brief text — different layouts, so the picker has something real to show.
>
> Add a validate-and-repair loop in a shared helper: validate each returned spec against the catalog, and on failure send the specific validation errors back for one retry, then raise with a clear message.
>
> Write the system prompt in `src/slidegen/llm/prompts.py`. It should describe the available layouts from the catalog, instruct that the three candidates differ in *approach* rather than wording, and forbid inventing layout indices or placeholder ids.
>
> Tests: schema generation matches the catalog; the mock provider round-trips through validation and render; the repair loop is exercised with a stubbed provider that returns one bad spec then a good one.

**You verify:** nothing to open yet — just read the generated tool schema and confirm it describes your real layouts sensibly.

---

## Task 4 — Streamlit app

> Implement `src/slidegen/app.py`.
>
> Flow: brief textarea → Generate → three candidate cards showing the layout name and rationale → select one → editable text fields for each placeholder → Download `.pptx`.
>
> Header shows the active provider, model, and — when the provider is `ford` — the data tier, prominently. Sidebar holds settings: provider, model, template path, and a connection test.
>
> Cache the loaded template and catalog in session state. Handle provider errors with a clear message rather than a traceback. Keep the UI in one file and plain.

**You verify:** run it, generate with the mock provider, download, open. Full loop working offline.

---

## Task 5 — Real providers

> Implement `src/slidegen/llm/openai_compatible.py`, serving both LM Studio and FordLLM through the OpenAI SDK. They differ only in `base_url` and auth.
>
> For `ford`: fetch an AAD client-credentials token, and cache the `OpenAI` client keyed on the token string behind a lock so it is rebuilt whenever the token changes. Select the base URL from `FORD_TIER`, defaulting to secret. Strip `None` values and coerce numeric types on every request payload. Do not stream.
>
> Add `src/slidegen/llm/factory.py` selecting a provider from config, and expose the enabled set so the UI only offers what is configured.
>
> Add structured JSON logging to stdout: request id, provider, model, latency, retry count, outcome.
>
> Tests: mock the OpenAI client entirely. Assert the client is rebuilt on token change and reused otherwise; assert null and string-typed numeric params never reach the payload; assert base URL selection follows the tier. No live calls.

**You verify:** on the VPN, with credentials set, run one real generation. This is the first moment Ford's network is involved, so anything that breaks here is auth or proxy — nothing else.

---

## Task 6 — Hardening

> Add: retry with exponential backoff on 429 and 5xx; a length guard that flags when supplied text is likely to overflow its placeholder based on the placeholder's size and a conservative character estimate, surfaced as a warning in the UI rather than a hard failure; a `--brief` CLI entry point for scripted use; and a `docs/handoff.md` covering deployment, the env var contract, and the security posture.

**You verify:** feed it your five worst real briefs. Overly long input is where auto-generated decks fall apart.

---

## Notes on running these

- Allow PyPI in the cloud environment's network config so packages install and tests run.
- Review each PR before starting the next task — later tasks build on earlier structure, and a wrong abstraction in Task 2 gets expensive by Task 5.
- If a session drifts into computing positions or setting fonts, stop it and point at rule 1 in `CLAUDE.md`. That's the failure mode to watch for.
