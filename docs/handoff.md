# Handoff

Deployment, the environment variable contract, and the security posture.

## What runs where

SlideGen is a single Python process. There is no database, no queue, no
persistent state. A brief goes in, a `.pptx` comes back, and nothing is written
to disk unless the CLI is asked to write a file.

Two entry points share the same core:

```bash
streamlit run src/slidegen/app.py            # interactive
slidegen --brief "..." --out deck.pptx       # scripted
```

Both need the template, the provider configuration, and — for FordLLM — network
access to the gateway and to Entra ID.

## Deployment

### On a workstation inside the network

```bash
uv sync
cp .env.example .env          # then fill it in
export SLIDEGEN_TEMPLATE=/path/to/corporate.potx
streamlit run src/slidegen/app.py
```

This is the expected deployment. The brief never leaves the machine except to go
to the configured gateway, and the template stays on the workstation.

### Shared host

Nothing prevents running it on a shared server, but note before you do:

- **There is no authentication.** Streamlit serves to whoever can reach the port.
  Anyone with the URL can submit a brief through your credentials and read the
  result. Put it behind the standard reverse proxy and SSO, or bind it to
  localhost.
- **`FORDLLM_CLIENT_SECRET` lives in the process environment.** One credential
  serves every user of that instance, so the gateway's logs attribute every
  request to the service principal, not to the person who typed the brief.
- **`st.cache_resource` is shared across sessions.** The template and its catalog
  are cached process-wide, which is intended; briefs and candidates are held in
  per-session state and are not.

### Container

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .
EXPOSE 8501
CMD ["streamlit", "run", "src/slidegen/app.py", "--server.address=0.0.0.0"]
```

Mount the template at runtime — do not bake it into the image:

```bash
docker run -p 8501:8501 \
  -v /path/to/corporate.potx:/templates/corporate.potx:ro \
  -e SLIDEGEN_TEMPLATE=/templates/corporate.potx \
  --env-file .env \
  slidegen
```

## Environment variable contract

| Variable | Default | Required | Purpose |
|---|---|---|---|
| `SLIDEGEN_TEMPLATE` | *(built-in)* | no | Path to a `.potx` / `.pptx`. Empty means the `python-pptx` built-in template. |
| `LLM_PROVIDER` | `mock` | no | `mock` \| `lmstudio` \| `ford` |
| `LLM_MODEL` | provider default | no | Model id or alias |
| `LMSTUDIO_BASE_URL` | `http://localhost:1234/v1` | no | LM Studio's OpenAI-compatible endpoint |
| `LMSTUDIO_API_KEY` | `lm-studio` | no | LM Studio ignores the value but the SDK requires one |
| `FORD_BASE_URL` | — | for `ford` + standard tier | Standard gateway |
| `FORD_BASE_URL_SECRET` | — | for `ford` + secret tier | Secret-data gateway |
| `FORD_TIER` | `secret` | no | `secret` \| `standard` |
| `FORDLLM_CLIENT_ID` | — | for `ford` | AAD application id |
| `FORDLLM_CLIENT_SECRET` | — | for `ford` | AAD client secret |
| `FORDLLM_TENANT_ID` | — | for `ford` | AAD tenant id |
| `FORDLLM_SCOPE` | `{client_id}/.default` | no | Token scope, if the gateway wants a specific one |
| `FORDLLM_AUTHORITY` | `https://login.microsoftonline.com` | no | Override for a non-public cloud |
| `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` | — | on the network | Both the OpenAI SDK and the token fetch honour these |
| `CANDIDATE_COUNT` | `3` | no | Candidates per generation (1–6) |
| `REQUEST_TIMEOUT` | `60` | no | Seconds per upstream request |
| `MAX_RETRIES` | `3` | no | Total attempts per request, including the first |
| `SLIDEGEN_REPO` | `cpkess/SlideGen` | no | Repository checked for releases |
| `SLIDEGEN_UPDATE_CHECK` | `true` | no | `false` stops the app contacting GitHub entirely |
| `SLIDEGEN_UPDATE_TTL` | `86400` | no | Seconds between update checks |
| `GITHUB_TOKEN` | — | no | Private repo, or to raise the unauthenticated rate limit |

A provider only appears in the UI when its configuration is complete —
`Settings.enabled_providers()` is the single source of that truth, so a
half-configured FordLLM is invisible rather than broken.

### Verifying configuration

```bash
python scripts/inspect_template.py       # the template loads, and its layouts
slidegen --ping                          # the provider is reachable, and its models
slidegen --brief "Test." --list          # a full generation round-trips
slidegen --check-update                  # GitHub is reachable, if updates are on
```

`--ping` is reachability only; it deliberately does not run a generation, so a
failure means the network or the URL, never the model. The sidebar's **Test
connection** button does the same thing.

### Cutting a release

The updater reads GitHub Releases, so it only sees versions that have one. To
publish: bump `version` in `pyproject.toml` and `__version__` in
`src/slidegen/__init__.py`, tag `vX.Y.Z`, push the tag, and create a release
against it. Tags that do not parse as `vX.Y.Z` are ignored by the checker, and a
pre-release suffix never presents itself as an upgrade over the plain version.

## Security posture

### What leaves the machine

The brief, and only the brief. It is sent to the configured gateway as the user
message of a chat completion, together with the system prompt and the tool
schema. The tool schema contains the loaded template's **layout names and
placeholder names** — worth knowing if those names are themselves sensitive.

Nothing else is transmitted to the model. The template file is never uploaded; it
is read locally and only its structure informs the prompt. The generated `.pptx`
is built locally from the model's text.

**One other outbound connection exists:** the update check, an unauthenticated
`GET https://api.github.com/repos/{SLIDEGEN_REPO}/releases/latest`, at most once
per `SLIDEGEN_UPDATE_TTL`. It sends no brief, no template data and no
credentials — only the request itself, which reveals to GitHub that this IP runs
SlideGen. Set `SLIDEGEN_UPDATE_CHECK=false` to remove it. If a security review
asks "what does this talk to", the answer is the configured gateway, Entra ID for
the token, and — unless disabled — api.github.com.

### Data tier

Briefs routinely contain unannounced product information, so **the secret-data
gateway is the default** and the UI states the active tier in the header. On the
standard tier the header shows a warning instead of a confirmation. `FORD_TIER`
is the only switch; there is no way to reach the standard gateway accidentally
while the variable says `secret`.

### Credentials

- `.env` is gitignored; `.env.example` carries names with empty values only.
- The AAD token is held in memory by a single long-lived `TokenFetcher` and never
  written to disk or logged. Logs record that a token was acquired and when it
  expires, never its value.
- The token expires every 60 minutes and is refreshed five minutes early. The
  `OpenAI` client embeds the key at construction, so it is cached against the
  token string and rebuilt whenever that string changes — a stale client is a 401
  an hour into a session.
- No credential is ever a function argument that could surface in a traceback.

### Repository hygiene

No Ford assets: no `.potx`, no real briefs, no product names, timing or pricing
in code, tests, fixtures or commit messages. `.gitignore` excludes `*.potx` and
`*.pptx` so a template cannot be committed by accident. Development and CI run
against the `python-pptx` built-in template, which exercises every code path.

### Updating

Updating executes code fetched from GitHub on a machine that handles secret-tier
briefs. That is a supply-chain surface, and it is treated as one:

- **Checking and applying are separate.** The check is read-only and automatic;
  applying is not. Nothing updates itself, on any schedule, ever.
- **Applying is a `git checkout` of a release tag**, after `git fetch --tags`. It
  refuses on a dirty working tree rather than discarding local changes, and
  refuses outright if this is not a git checkout, pointing at `pip install
  --upgrade` instead.
- **A restart is required.** Python will not reload a running process, so the new
  code is not live until someone restarts it — which is also the last chance to
  reconsider.
- **Whoever can push a tag to `SLIDEGEN_REPO` can supply code to every instance**
  that someone then clicks *Update* on. If that repository is public, so is that
  trust boundary. Restrict tag-push permissions, or set
  `SLIDEGEN_UPDATE_CHECK=false` and update through your normal deployment
  process.

### Logging

Structured JSON to stdout, one object per line: request id, provider, model,
latency, retry count, outcome. **Brief text and generated slide text are never
logged.** Validation errors are logged by count, not content, for the same
reason. If you add logging, keep it to metadata.

### Failure modes worth knowing

| Symptom | Cause |
|---|---|
| 401 an hour into a session | The client was not rebuilt on token change. Covered by tests; check `OpenAICompatibleProvider.client`. |
| Opaque 500 from the gateway | A null or a string-typed number in the payload. `clean_payload` strips and coerces; check anything added to the request since. |
| Proxy errors on the network | `HTTPS_PROXY` unset for the process. Both the SDK and the AAD token fetch read it from the environment. |
| "does not fit the template" | The model chose a placeholder idx from another layout twice running. The validation errors in the message say exactly which. |
| Text overflows the slide | Expected: the guard warns, it does not block. The template's autofit decides what actually happens. |
| LM Studio: "replied without calling the tool" | The loaded model has no tool-use template. SlideGen recovers a call written as text, but a model that emits neither cannot be used — load one with tool support. |
| LM Studio: named tool choice rejected | Handled: the call is retried with `tool_choice="auto"` and the downgrade is remembered. Visible in the logs as a warning. |
| LM Studio: model not found | `slidegen --ping` lists what the server actually has; `LLM_MODEL` must match one of those ids. |
| "Could not check for updates" | Informational. GitHub is unreachable, rate-limited, or the repo has no releases. It never blocks generation. |

## Operating notes

- **Retries.** 429 and 5xx get exponential backoff with full jitter, up to
  `MAX_RETRIES` attempts. 4xx is never retried. The SDK's own retry is disabled so
  there is one backoff policy, not two multiplying together.
- **Repair.** An invalid set of candidates is sent back once with the specific
  validation errors. A second failure raises with those errors included.
- **Overflow warnings** are estimates from the placeholder's declared size and an
  assumed point size. They are advisory; a human confirms by opening the file.
- **The layout catalog is derived at runtime.** Adding a layout in PowerPoint's
  slide master view puts it in the tool schema on the next run, with no code
  change and no redeploy beyond restarting the process.
