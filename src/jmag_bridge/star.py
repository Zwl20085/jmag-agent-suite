"""One-time, opt-in offer to star the repository on GitHub.

The first time the suite is used on a machine, jmag_capabilities and jmag_self_test carry a `first_run_note` for the
agent to relay. Nothing is starred unless the user says yes; any answer is recorded in state.json (key
"star_prompt") and the note never appears again. Starring uses the GitHub CLI; if `gh` is missing, not logged in or
fails, the repo page is opened in the browser instead so the user can click Star themselves.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import subprocess
import webbrowser
from typing import Any, Callable

from .config import load_state, save_state

REPO_OWNER = "Zwl20085"
REPO_NAME = "jmag-agent-suite"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
STATE_KEY = "star_prompt"
ACTIONS = ("star", "open", "dismiss")
GH_TIMEOUT_S = 15
# No leading slash: Git Bash on Windows rewrites "/user/.." into a filesystem path.
_ENDPOINT = f"user/starred/{REPO_OWNER}/{REPO_NAME}"


def first_run_prompt() -> str | None:
    """The message for the agent to relay on first use on this machine, or None once any answer was recorded."""
    st = load_state()
    if isinstance(st, dict) and STATE_KEY in st:
        return None
    return (
        "AGENT: ask the user the question below in your own reply, wait for an explicit answer, and only then call "
        "jmag_star_repo with action='star' (yes), 'open' (open the page) or 'dismiss' (no). Never call it without an "
        "answer. It is asked once per machine.\n"
        "USER: This is the first time the JMAG Agent Suite is used on this machine. The suite is free and open "
        f"source; a GitHub star helps other engineers find it ({REPO_URL}). Would you like to star it? "
        "Answer yes (star it through the GitHub CLI), open (open the page so you can star it yourself) or no "
        "(you will not be asked again)."
    )


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _record(action: str, method: str) -> bool:
    try:
        save_state(**{STATE_KEY: {"action": action, "method": method, "when": _now()}})
        return True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _gh(run: Callable[..., Any], gh: str, *args: str) -> bool:
    """True if `gh <args>` exited 0. Never raises; shell=False always."""
    try:
        r = run([gh, *args], capture_output=True, text=True, timeout=GH_TIMEOUT_S, shell=False,
                stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return getattr(r, "returncode", 1) == 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def _star_with_gh(run: Callable[..., Any], which: Callable[[str], str | None]) -> tuple[str | None, str]:
    """('gh' | 'already_starred', msg) on success, (None, reason) if the caller must fall back to the browser."""
    gh = which("gh")
    if not gh:
        return None, "GitHub CLI (gh) not found"
    if not _gh(run, gh, "auth", "status"):
        return None, "`gh auth status` failed (not logged in? run `gh auth login`)"
    if _gh(run, gh, "api", "-X", "GET", _ENDPOINT):
        return "already_starred", f"{REPO_URL} was already starred - thank you."
    if _gh(run, gh, "api", "-X", "PUT", _ENDPOINT):
        return "gh", f"Starred {REPO_URL} through the GitHub CLI - thank you."
    return None, "gh could not star the repository"


def _open(open_url: Callable[[str], Any]) -> bool:
    try:
        return bool(open_url(REPO_URL))
    except Exception:  # noqa: BLE001 - any browser failure only changes the message
        return False


def star_repo(action: str, *, run: Callable[..., Any] = subprocess.run,
              which: Callable[[str], str | None] = shutil.which,
              open_url: Callable[[str], Any] = webbrowser.open) -> dict:
    """Act on the user's answer to the first-run prompt. action: 'star' | 'open' | 'dismiss'.

    Returns {ok, method ('gh' | 'browser' | 'dismissed' | 'already_starred'), message, url}. Every path records the
    answer so the prompt is never shown again. Raises ValueError for any other action.
    """
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}; got {action!r}")
    if action == "dismiss":
        ok, method, msg = True, "dismissed", "Understood - you will not be asked again on this machine."
    elif action == "star":
        method_gh, why = _star_with_gh(run, which)
        if method_gh:
            ok, method, msg = True, method_gh, why
        else:
            opened = _open(open_url)
            ok, method = opened, "browser"
            msg = (f"{why}; opened {REPO_URL} in the browser - click Star there." if opened
                   else f"{why}, and the browser could not be opened. Star it at {REPO_URL}.")
    else:
        opened = _open(open_url)
        ok, method = opened, "browser"
        msg = (f"Opened {REPO_URL} in the browser - click Star there." if opened
               else f"The browser could not be opened. Star it at {REPO_URL}.")
    if not _record(action, method):
        msg += " (Could not save the answer to state.json; the prompt may appear again.)"
    return {"ok": ok, "method": method, "message": msg, "url": REPO_URL}
