# SlideGen

Turn a brief into structured, editable, on-brand PowerPoint slides.

Paste a brief. Get three candidate slide structures. Pick one. Download a `.pptx` that is a real template slide — fully editable, correctly branded, no image-of-a-slide.

## How it works

The corporate PowerPoint template already *is* a component system. Its slide layouts define position, size, typography, color, and autofit behavior, and anything placed into a placeholder inherits all of it. SlideGen does not reimplement layout — it selects a layout and fills placeholders.

```
brief ──▶ LLM ──▶ SlideSpec (JSON) ──▶ python-pptx ──▶ .pptx
                       ▲
                       │
              LayoutCatalog, read at runtime
              from whatever template is loaded
```

The LLM's tool schema is **generated from the loaded template's layouts**. Add a layout to the template in PowerPoint's slide master view and it becomes available to the model automatically — no code change. That is the modularity story: new designs are a design task, not an engineering one.

## Quick start

```bash
uv sync                  # or: pip install -e ".[dev]"
cp .env.example .env
streamlit run src/slidegen/app.py
```

With no configuration, SlideGen uses the `python-pptx` built-in template and the `mock` LLM provider. Everything runs offline.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SLIDEGEN_TEMPLATE` | *(built-in)* | Path to a `.potx` / `.pptx` template |
| `LLM_PROVIDER` | `mock` | `mock` \| `lmstudio` \| `ford` |
| `LLM_MODEL` | provider default | Model id or alias |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | |
| `FORD_BASE_URL` | — | Standard FordLLM gateway |
| `FORD_BASE_URL_SECRET` | — | Secret-data gateway (**default for real use**) |
| `FORD_TIER` | `secret` | `secret` \| `standard` |
| `FORDLLM_CLIENT_ID` / `FORDLLM_CLIENT_SECRET` | — | AAD client credentials |
| `FORDLLM_TENANT_ID` | — | AAD tenant id |
| `FORDLLM_SCOPE` | `{client_id}/.default` | Token scope, if the gateway wants a specific one |
| `CANDIDATE_COUNT` | `3` | Candidates per generation (1–6) |
| `REQUEST_TIMEOUT` | `60` | Seconds per upstream request |
| `MAX_RETRIES` | `3` | Attempts per request, including the first |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | — | Required on the Ford network |

A provider appears in the UI only when its configuration is complete, so a
half-configured FordLLM is invisible rather than broken. `docs/handoff.md` has
the full contract, including deployment and the security posture.

## Command line

For scripted use, the same pipeline without the UI:

```bash
slidegen --brief "..." --out deck.pptx      # render the first candidate
slidegen --brief-file brief.txt --list      # show candidates, write nothing
slidegen --brief "..." --out all.pptx --all # every candidate, one slide each
```

`--candidate N` picks a different one, `--template` and `--provider` override
configuration for a single run, and `--json` emits the specs rather than a table.

## Repository rules

**No Ford assets in this repository.** No `.potx`, no real briefs, no product data, no credentials. Development runs against the `python-pptx` built-in template, which has eleven standard layouts and is sufficient to exercise every code path. The real template is supplied at runtime via `SLIDEGEN_TEMPLATE` on a machine inside the network.

This keeps the codebase template-agnostic — an architectural benefit, not just a compliance one.

## Layout

```
src/slidegen/
├── template.py    # inspect a template → LayoutCatalog
├── spec.py        # SlideSpec model + validation against the catalog
├── render.py      # spec + template → .pptx bytes
├── guards.py      # conservative overflow estimate, advisory only
├── retry.py       # exponential backoff on 429 / 5xx
├── llm/
│   ├── base.py    # Provider protocol
│   ├── mock.py    # deterministic, offline
│   ├── openai_compatible.py   # LM Studio + FordLLM
│   ├── factory.py # config → provider
│   ├── repair.py  # validate-and-repair loop
│   ├── prompts.py # system / repair prompts, built from the catalog
│   └── schema.py  # catalog → tool schema
├── logging_config.py
├── config.py
├── cli.py         # slidegen --brief ...
└── app.py         # Streamlit
scripts/
└── inspect_template.py
docs/
└── handoff.md     # deployment, env var contract, security posture
tests/
```

## Development

```bash
pytest                              # all tests, no network required
python scripts/inspect_template.py  # dump the loaded template's layout catalog
```

Tests must pass with `LLM_PROVIDER=mock` and no network access. Provider tests
mock the OpenAI SDK client entirely — there is never a live call.
