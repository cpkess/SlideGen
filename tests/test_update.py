"""The GitHub-powered update check.

Never a live call to api.github.com, and never a real `git checkout`.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
from io import BytesIO

import pytest

from slidegen.update import (
    UpdateError,
    apply_update,
    cached_check,
    check_for_update,
    is_newer,
    parse_version,
    repo_root,
    reset_cache,
)

REPO = "cpkess/SlideGen"


def release(tag="v0.2.0", **overrides):
    payload = {
        "tag_name": tag,
        "html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
        "body": "Adds a thing.",
        "published_at": "2026-08-02T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def fetcher(payload=None, error=None):
    calls = []

    def fetch(url, token, timeout):
        calls.append({"url": url, "token": token, "timeout": timeout})
        if error:
            raise error
        return payload

    fetch.calls = calls
    return fetch


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_cache()
    yield
    reset_cache()


# --- version comparison ----------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2.3", (1, 2, 3)),
        ("v2.0", (2, 0, 0)),
        ("v3", (3, 0, 0)),
        ("v1.2.3-rc1", (1, 2, 3)),
        ("nightly", None),
        ("", None),
    ],
)
def test_version_parsing(tag, expected):
    assert parse_version(tag) == expected


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("v0.2.0", "0.1.0", True),
        ("v0.1.1", "0.1.0", True),
        ("v1.0.0", "0.9.9", True),
        ("v0.1.0", "0.1.0", False),
        ("v0.1.0", "0.2.0", False),
        ("v0.10.0", "0.9.0", True),  # not string ordering
    ],
)
def test_newer_comparison(candidate, current, expected):
    assert is_newer(candidate, current) is expected


def test_a_prerelease_does_not_present_itself_as_an_upgrade():
    assert is_newer("v0.1.0-rc1", "0.1.0") is False


def test_an_unparseable_tag_is_never_an_upgrade():
    assert is_newer("nightly", "0.1.0") is False


# --- checking --------------------------------------------------------------


def test_a_newer_release_is_reported():
    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(release("v0.2.0")))

    assert status.available
    assert status.latest == "v0.2.0"
    assert status.checked
    assert "v0.2.0 is available" in status.message
    assert status.url.endswith("/v0.2.0")


def test_the_same_version_is_up_to_date():
    status = check_for_update(REPO, version="0.2.0", fetch=fetcher(release("v0.2.0")))

    assert not status.available
    assert "up to date" in status.message


def test_an_older_release_is_not_an_upgrade():
    status = check_for_update(REPO, version="0.3.0", fetch=fetcher(release("v0.2.0")))

    assert not status.available


def test_the_right_endpoint_is_called_with_the_token():
    fetch = fetcher(release())

    check_for_update(REPO, version="0.1.0", token="ghp_x", timeout=5.0, fetch=fetch)

    assert fetch.calls[0]["url"] == f"https://api.github.com/repos/{REPO}/releases/latest"
    assert fetch.calls[0]["token"] == "ghp_x"
    assert fetch.calls[0]["timeout"] == 5.0


def test_a_repository_with_no_releases_is_not_an_error_state():
    error = urllib.error.HTTPError("u", 404, "Not Found", {}, None)

    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=error))

    assert not status.checked
    assert not status.available
    assert "no releases published yet" in status.message


def test_an_unreachable_github_is_reported_not_raised():
    """The common case behind a corporate proxy."""
    error = urllib.error.URLError("proxy refused")

    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=error))

    assert not status.checked
    assert "could not reach GitHub" in status.message
    assert status.current == "0.1.0"


def http_error(code, body=None, headers=None):
    payload = BytesIO(json.dumps(body).encode()) if body is not None else None
    return urllib.error.HTTPError("u", code, "err", headers or {}, payload)


def test_an_authorisation_failure_names_the_token():
    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=http_error(403)))

    assert "GITHUB_TOKEN" in status.message


def test_githubs_own_explanation_is_surfaced():
    """A 403 can be several different things; only the body says which."""
    error = http_error(403, {"message": "GitHub access is not enabled for this session."})

    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=error))

    assert "not enabled for this session" in status.message


def test_rate_limiting_is_named_as_such():
    error = http_error(403, {"message": "API rate limit exceeded"}, {"x-ratelimit-remaining": "0"})

    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=error))

    assert "rate limit" in status.message
    assert "GITHUB_TOKEN" in status.message


def test_an_unexpected_status_includes_the_detail():
    error = http_error(500, {"message": "Server Error"})

    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=error))

    assert "HTTP 500" in status.message
    assert "Server Error" in status.message


def test_a_release_without_a_tag_is_handled():
    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(release(tag="")))

    assert not status.checked
    assert not status.available


def test_a_check_never_raises():
    status = check_for_update(REPO, version="0.1.0", fetch=fetcher(error=RuntimeError("boom")))

    assert not status.checked
    assert "boom" in status.message


# --- caching ---------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_check_is_cached_within_its_ttl():
    fetch = fetcher(release())
    clock = Clock()

    cached_check(REPO, ttl=3600, clock=clock, version="0.1.0", fetch=fetch)
    clock.now = 1800
    cached_check(REPO, ttl=3600, clock=clock, version="0.1.0", fetch=fetch)

    assert len(fetch.calls) == 1


def test_the_cache_expires():
    fetch = fetcher(release())
    clock = Clock()

    cached_check(REPO, ttl=3600, clock=clock, version="0.1.0", fetch=fetch)
    clock.now = 3601
    cached_check(REPO, ttl=3600, clock=clock, version="0.1.0", fetch=fetch)

    assert len(fetch.calls) == 2


def test_a_forced_check_bypasses_the_cache():
    fetch = fetcher(release())
    clock = Clock()

    cached_check(REPO, clock=clock, version="0.1.0", fetch=fetch)
    cached_check(REPO, clock=clock, force=True, version="0.1.0", fetch=fetch)

    assert len(fetch.calls) == 2


# --- applying --------------------------------------------------------------


def run(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def upstream(tmp_path):
    """A repo standing in for origin, carrying a v0.2.0 tag."""
    root = tmp_path / "origin"
    root.mkdir()
    run(root, "git", "init", "-q")
    run(root, "git", "config", "user.email", "t@example.com")
    run(root, "git", "config", "user.name", "Test")
    (root / "file.txt").write_text("v1")
    run(root, "git", "add", ".")
    run(root, "git", "commit", "-qm", "one")
    (root / "file.txt").write_text("v2")
    run(root, "git", "commit", "-aqm", "two")
    run(root, "git", "tag", "v0.2.0")
    return root


def checkout(tmp_path, dirty=False):
    """A working checkout of that repo, one commit behind the tag."""
    origin = upstream(tmp_path)
    work = tmp_path / "work"
    run(tmp_path, "git", "clone", "-q", str(origin), str(work))
    run(work, "git", "config", "user.email", "t@example.com")
    run(work, "git", "config", "user.name", "Test")
    run(work, "git", "checkout", "-q", "HEAD~1")
    if dirty:
        (work / "file.txt").write_text("uncommitted change")
    return work


def test_an_update_moves_the_checkout_to_the_tag(tmp_path):
    work = checkout(tmp_path)
    assert (work / "file.txt").read_text() == "v1"

    message = apply_update("v0.2.0", root=work)

    assert (work / "file.txt").read_text() == "v2"
    assert "Restart SlideGen" in message


def test_a_dirty_working_tree_refuses_the_update(tmp_path):
    work = checkout(tmp_path, dirty=True)

    with pytest.raises(UpdateError, match="uncommitted changes"):
        apply_update("v0.2.0", root=work)

    assert (work / "file.txt").read_text() == "uncommitted change"


def test_a_non_git_install_says_how_to_upgrade_instead(monkeypatch):
    monkeypatch.setattr("slidegen.update.repo_root", lambda *a, **k: None)

    with pytest.raises(UpdateError, match="pip install --upgrade"):
        apply_update("v0.2.0")


def test_a_missing_tag_fails_cleanly(tmp_path):
    work = checkout(tmp_path)

    with pytest.raises(UpdateError):
        apply_update("v9.9.9", root=work)


def test_repo_root_finds_this_checkout():
    root = repo_root()

    assert root is not None
    assert (root / ".git").exists()
    assert (root / "pyproject.toml").is_file()
