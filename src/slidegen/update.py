"""Check GitHub Releases for a newer SlideGen, and optionally move to it.

Two deliberate limits, both because of where this runs:

* **Checking never blocks and never raises.** A machine behind a corporate proxy
  may not reach api.github.com at all. An unreachable check is reported as
  "could not check", not as an error, and never stops a deck being generated.
* **Updating is always an explicit act.** Nothing self-applies. Pulling and
  running new code on a workstation that handles secret-tier briefs is a
  decision for a person, so the check tells you and `apply_update` waits to be
  asked.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
USER_AGENT = "slidegen-update-check"

_VERSION = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$")


def current_version() -> str:
    """The running version, from installed metadata where available."""
    try:
        from importlib.metadata import version

        return version("slidegen")
    except Exception:  # noqa: BLE001 — running from a source tree, not installed
        from . import __version__

        return __version__


def parse_version(tag: str) -> tuple[int, int, int] | None:
    """`v1.2.3` → `(1, 2, 3)`. Returns None for anything unrecognisable.

    A pre-release suffix is ignored for ordering, so `1.2.0-rc1` and `1.2.0`
    compare equal and an rc never presents itself as an upgrade over the release.
    """
    match = _VERSION.match(tag.strip())
    if match is None:
        return None
    major, minor, patch, _suffix = match.groups()
    return (int(major), int(minor or 0), int(patch or 0))


def is_newer(candidate: str, than: str) -> bool:
    left, right = parse_version(candidate), parse_version(than)
    if left is None or right is None:
        return False
    return left > right


class UpdateStatus(BaseModel):
    """The outcome of a check. Never an exception."""

    current: str
    latest: str | None = None
    available: bool = False
    url: str | None = None
    notes: str = ""
    published_at: str | None = None
    error: str | None = Field(
        default=None, description="Why the check did not complete, if it did not."
    )

    @property
    def checked(self) -> bool:
        return self.error is None

    @property
    def message(self) -> str:
        if self.error:
            return f"Could not check for updates: {self.error}"
        if self.available:
            return f"SlideGen {self.latest} is available — you are on {self.current}."
        return f"SlideGen {self.current} is up to date."


def _get_json(url: str, token: str | None, timeout: float) -> dict:
    """GET a GitHub API endpoint, honouring the environment's proxy settings."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode())


def _api_message(exc: urllib.error.HTTPError) -> str:
    """GitHub's own explanation, which is usually the most useful thing available.

    A 403 can be rate limiting, a private repository, or a policy in front of the
    API, and only the body distinguishes them.
    """
    try:
        body = json.loads(exc.read().decode())
    except Exception:  # noqa: BLE001 — an unreadable body is not worth reporting
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return str(message).strip() if message else ""


def _describe(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "no releases published yet"

        detail = _api_message(exc)
        if exc.code == 403 and exc.headers.get("x-ratelimit-remaining") == "0":
            return "GitHub API rate limit reached — set GITHUB_TOKEN to raise it"
        if exc.code in (401, 403):
            hint = detail or "not authorised"
            return f"{hint} (set GITHUB_TOKEN if the repository is private)"
        return f"GitHub returned HTTP {exc.code}" + (f": {detail}" if detail else "")

    if isinstance(exc, urllib.error.URLError):
        return f"could not reach GitHub ({exc.reason})"
    return str(exc)


def check_for_update(
    repo: str,
    *,
    version: str | None = None,
    token: str | None = None,
    timeout: float = 10.0,
    fetch: Callable[[str, str | None, float], dict] = _get_json,
) -> UpdateStatus:
    """Ask GitHub for the latest release. Never raises."""
    running = version or current_version()
    try:
        payload = fetch(f"{GITHUB_API}/repos/{repo}/releases/latest", token, timeout)
    except Exception as exc:  # noqa: BLE001 — a failed check is not an app failure
        reason = _describe(exc)
        logger.info("update check failed", extra={"repo": repo, "reason": reason})
        return UpdateStatus(current=running, error=reason)

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        return UpdateStatus(current=running, error="the latest release has no tag")

    available = is_newer(tag, running)
    logger.info(
        "update check complete",
        extra={"repo": repo, "current": running, "latest": tag, "available": available},
    )
    return UpdateStatus(
        current=running,
        latest=tag,
        available=available,
        url=payload.get("html_url"),
        notes=str(payload.get("body") or "").strip(),
        published_at=payload.get("published_at"),
    )


# --- caching ---------------------------------------------------------------
#
# Streamlit reruns the script on every interaction. Without a cache the app would
# call GitHub on each keystroke.

_lock = threading.Lock()
_cached: tuple[float, UpdateStatus] | None = None


def cached_check(
    repo: str,
    *,
    ttl: float = 86_400.0,
    force: bool = False,
    clock: Callable[[], float] = time.monotonic,
    **kwargs,
) -> UpdateStatus:
    """`check_for_update`, at most once per `ttl` seconds."""
    global _cached
    with _lock:
        if not force and _cached is not None and clock() - _cached[0] < ttl:
            return _cached[1]
        status = check_for_update(repo, **kwargs)
        _cached = (clock(), status)
        return status


def reset_cache() -> None:
    """Drop the cached check. For tests and for a manual re-check."""
    global _cached
    with _lock:
        _cached = None


# --- applying --------------------------------------------------------------


class UpdateError(RuntimeError):
    """An update was attempted and could not be completed safely."""


def repo_root(start: Path | None = None) -> Path | None:
    """The git checkout containing this package, if it is one."""
    here = (start or Path(__file__).resolve()).parent
    for directory in [here, *here.parents]:
        if (directory / ".git").exists():
            return directory
    return None


def _git(root: Path, *args: str, timeout: float = 120.0) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise UpdateError((result.stderr or result.stdout).strip() or f"git {args[0]} failed")
    return result.stdout.strip()


def apply_update(tag: str, *, root: Path | None = None) -> str:
    """Move a git checkout onto a released tag.

    Refuses rather than guesses: an uncommitted change is far more likely to be
    work in progress than something to discard. Returns a message for the user.

    This runs code fetched from GitHub. It is only ever called from an explicit
    action, never from a check.
    """
    root = root or repo_root()
    if root is None:
        raise UpdateError(
            "This is not a git checkout, so it cannot update itself. Reinstall "
            "with: pip install --upgrade 'slidegen @ git+https://github.com/"
            f"<owner>/<repo>@{tag}'"
        )

    if _git(root, "status", "--porcelain"):
        raise UpdateError(
            "There are uncommitted changes in the working tree. Commit or stash "
            "them first — updating would overwrite them."
        )

    _git(root, "fetch", "--tags", "--quiet", "origin")
    _git(root, "-c", "advice.detachedHead=false", "checkout", "--quiet", tag)
    logger.info("updated checkout", extra={"tag": tag, "root": str(root)})

    return (
        f"Updated to {tag}. Restart SlideGen for it to take effect"
        " — and re-run `uv sync` if the dependencies changed."
    )
