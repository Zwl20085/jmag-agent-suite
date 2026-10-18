"""One-time star prompt: state handling, gh path, browser fallback. Fake run/which/open_url; no network, no real gh."""
import asyncio
import json
import subprocess

import pytest

from jmag_bridge import server, star
from jmag_bridge.config import load_state

GH = r"C:\fake\gh.exe"
ENDPOINT = "user/starred/Zwl20085/jmag-agent-suite"


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JMAG_BRIDGE_HOME", str(tmp_path))
    return tmp_path


class FakeRun:
    """Records argv; returncode per (args[1:]) prefix, or raises `exc` for every call."""

    def __init__(self, codes=None, exc=None):
        self.codes, self.exc, self.calls = codes or {}, exc, []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if self.exc:
            raise self.exc
        key = " ".join(argv[1:])
        code = next((c for k, c in self.codes.items() if key.startswith(k)), 0)
        return subprocess.CompletedProcess(argv, code, "", "")


class FakeOpen:
    def __init__(self, result=True):
        self.urls, self.result = [], result

    def __call__(self, url):
        self.urls.append(url)
        return self.result


def which_gh(name):
    return GH if name == "gh" else None


def which_none(name):
    return None


def recorded():
    return load_state()[star.STATE_KEY]


def test_prompt_shown_until_answered():
    msg = star.first_run_prompt()
    assert msg and star.REPO_URL in msg and "jmag_star_repo" in msg and "explicit answer" in msg
    star.star_repo("dismiss", run=FakeRun(), which=which_gh, open_url=FakeOpen())
    assert star.first_run_prompt() is None


def test_star_via_gh_puts_without_leading_slash():
    run, opener = FakeRun({"api -X GET": 1}), FakeOpen()
    r = star.star_repo("star", run=run, which=which_gh, open_url=opener)
    assert r == {"ok": True, "method": "gh", "message": r["message"], "url": star.REPO_URL}
    argvs = [c[0] for c in run.calls]
    assert argvs == [[GH, "auth", "status"], [GH, "api", "-X", "GET", ENDPOINT], [GH, "api", "-X", "PUT", ENDPOINT]]
    assert all(not kw.get("shell") and kw["timeout"] == 15 for _, kw in run.calls)
    assert not opener.urls
    assert recorded()["action"] == "star" and recorded()["method"] == "gh" and recorded()["when"].endswith("+00:00")


def test_already_starred_skips_put():
    run = FakeRun()  # every call exits 0, so GET says starred
    r = star.star_repo("star", run=run, which=which_gh, open_url=FakeOpen())
    assert r["ok"] and r["method"] == "already_starred"
    assert [c[0] for c in run.calls] == [[GH, "auth", "status"], [GH, "api", "-X", "GET", ENDPOINT]]
    assert recorded()["method"] == "already_starred"


@pytest.mark.parametrize("which, run, why", [
    (which_none, FakeRun(), "not found"),
    (which_gh, FakeRun({"auth status": 1}), "auth status` failed"),
    (which_gh, FakeRun(exc=subprocess.TimeoutExpired("gh", 15)), "auth status` failed"),
    (which_gh, FakeRun(exc=OSError("boom")), "auth status` failed"),
    (which_gh, FakeRun({"api -X GET": 1, "api -X PUT": 1}), "could not star"),
])
def test_star_falls_back_to_browser(which, run, why):
    opener = FakeOpen()
    r = star.star_repo("star", run=run, which=which, open_url=opener)
    assert r["ok"] and r["method"] == "browser" and why in r["message"]
    assert opener.urls == [star.REPO_URL]
    assert recorded() | {"when": None} == {"action": "star", "method": "browser", "when": None}
    assert star.first_run_prompt() is None


def test_browser_failure_still_records_and_gives_url():
    r = star.star_repo("star", run=FakeRun(), which=which_none, open_url=FakeOpen(result=False))
    assert not r["ok"] and r["method"] == "browser" and star.REPO_URL in r["message"]
    assert star.first_run_prompt() is None


def test_open_only_opens_page():
    run, opener = FakeRun(), FakeOpen()
    r = star.star_repo("open", run=run, which=which_gh, open_url=opener)
    assert r["ok"] and r["method"] == "browser" and opener.urls == [star.REPO_URL] and not run.calls
    assert recorded()["action"] == "open"


def test_dismiss_runs_nothing():
    run, opener = FakeRun(), FakeOpen()
    r = star.star_repo("dismiss", run=run, which=which_gh, open_url=opener)
    assert r["ok"] and r["method"] == "dismissed" and not run.calls and not opener.urls
    assert recorded()["action"] == "dismiss"


@pytest.mark.parametrize("action", ["yes", "", "STAR", None])
def test_invalid_action(action):
    with pytest.raises(ValueError):
        star.star_repo(action, run=FakeRun(), which=which_gh, open_url=FakeOpen())
    assert star.first_run_prompt() is not None


def test_corrupt_state_file_prompts_and_is_repaired(home):
    (home / "state.json").write_text("{not json")
    assert star.first_run_prompt() is not None
    star.star_repo("dismiss", run=FakeRun(), which=which_gh, open_url=FakeOpen())
    assert json.loads((home / "state.json").read_text())[star.STATE_KEY]["action"] == "dismiss"
    assert star.first_run_prompt() is None


def test_other_state_keys_survive(home):
    (home / "state.json").write_text(json.dumps({"jmag_pid": 1234}))
    star.star_repo("dismiss", run=FakeRun(), which=which_gh, open_url=FakeOpen())
    assert load_state()["jmag_pid"] == 1234


class CapsBridge:
    async def call(self, op, timeout=600, **a):
        return {"ok": True, "result": {"version": "23.0"}}


def test_capabilities_carries_note_only_on_first_run(monkeypatch):
    monkeypatch.setattr(server, "B", CapsBridge())
    first = asyncio.run(server.jmag_capabilities())
    assert first["ok"] and first["result"] == {"version": "23.0"} and "jmag_star_repo" in first["first_run_note"]
    star.star_repo("dismiss", run=FakeRun(), which=which_gh, open_url=FakeOpen())
    assert "first_run_note" not in asyncio.run(server.jmag_capabilities())


def test_star_tool_rejects_bad_action_without_recording():
    r = asyncio.run(server.jmag_star_repo("maybe"))
    assert r["ok"] is False and "dismiss" in r["error"]["hint"]
    assert star.first_run_prompt() is not None
