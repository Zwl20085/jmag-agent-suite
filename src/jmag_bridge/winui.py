"""Windows-side helpers: find/click JMAG modal dialogs, capture JMAG windows."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

PS_DIR = Path(__file__).parent / "ps"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _cmd(script: str, *args: str) -> list[str]:
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(PS_DIR / script), *args]


def _parse(stdout: str) -> list[dict]:
    rows = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r.get("buttons"), str):
                r["buttons"] = [r["buttons"]]
            rows.append(r)
    return rows


def _run_ps(script: str, *args: str, timeout: int = 60) -> list[dict]:
    if os.name != "nt":
        return []
    try:
        proc = subprocess.run(_cmd(script, *args), capture_output=True, text=True, timeout=timeout,
                              encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return [{"error": f"{script} timed out after {timeout}s"}]
    return _parse(proc.stdout)


def dialogs(title_filter: str = "") -> list[dict]:
    """Visible JMAG dialogs: kind (modal|progress|window), title, text, buttons."""
    return _run_ps("dialogs.ps1", *(["-TitleFilter", title_filter] if title_filter else []))


def click(button: str, title_filter: str = "", wait: float = 15.0) -> dict:
    """Press a dialog button without blocking on it.

    UI Automation's Invoke() is synchronous: if the button starts a long action (e.g. 'open results')
    the call only returns when JMAG is done. So the click runs in a detached PowerShell and we poll
    until the dialog is gone.
    """
    if os.name != "nt":
        return {"clicked": False}
    before = [d for d in dialogs(title_filter) if button in (d.get("buttons") or [])]
    if not before:
        return {"clicked": False, "reason": f"no dialog with a '{button}' button", "dialogs": dialogs()}
    args = ["-Click", button] + (["-TitleFilter", title_filter] if title_filter else [])
    proc = subprocess.Popen(_cmd("dialogs.ps1", *args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW)
    t_end = time.time() + wait
    gone = False
    while time.time() < t_end:
        time.sleep(0.7)
        still = [d for d in dialogs(title_filter) if d.get("title") == before[0].get("title")
                 and button in (d.get("buttons") or [])]
        if not still:
            gone = True
            break
    if proc.poll() is None and gone:
        proc.kill()  # the click was delivered; don't leave the helper hanging
    return {"clicked": True, "dialog": before[0].get("title"), "dialog_closed": gone}


def screenshots(out_dir: str) -> list[dict]:
    return _run_ps("winshot.ps1", "-OutDir", out_dir)


def parse_progress(text: str) -> dict:
    """Fields of JMAG's 'Run Analysis' progress window (label line followed by value line)."""
    lines = [x.strip() for x in text.splitlines()]
    out = {}
    for k, line in enumerate(lines[:-1]):
        if line.endswith(":") and not lines[k + 1].endswith(":"):
            out[line[:-1]] = lines[k + 1]
    return out
