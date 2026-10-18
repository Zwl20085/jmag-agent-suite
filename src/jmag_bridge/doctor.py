"""Environment diagnostics: why the MCP server, the JMAG worker or the Claude Code integration does not work.

Pure stdlib (no ``mcp`` import, no JMAG session, no network) so it runs even when the server cannot start:
``python -m jmag_bridge --doctor [--json]`` and the ``jmag_doctor`` MCP tool share :func:`run_checks`.
Every check is isolated: an exception inside one becomes a FAIL row for that check.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import __version__, config

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MIN_HOST = (3, 10)
MIN_MCP = (1, 10)
PROBE_MARK = "@@DOCTOR@@"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Mirrors worker/jmag_worker.py + ops.Context.module(): JMAG home on sys.path, then `from jmag.designer import designer`.
# Importing loads PyJmag.dll but does not start designer.exe (that only happens in CreateApplication/GetApplication).
PYJMAG_PROBE = ("import sys; sys.path.insert(0, sys.argv[1]); from jmag.designer import designer; "
                "print('" + PROBE_MARK + "', hasattr(designer, 'CreateApplication'))")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _run(cmd: list[str], timeout: float, cwd: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    """subprocess.run with captured text output and no console window (tests monkeypatch this)."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=env,
                          creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")


def _worker_env() -> dict:
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "VIRTUAL_ENV"):  # same scrub as worker_client.Worker
        env.pop(k, None)
    return env


def _tail(text: str, n: int = 3) -> str:
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return " | ".join(lines[-n:])


def _vtuple(v: str) -> tuple:
    out = []
    for part in v.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


# =========================================================================== checks

def check_host_python() -> Check:
    v = sys.version_info
    detail = f"{sys.executable} (Python {v.major}.{v.minor}.{v.micro})"
    if (v.major, v.minor) < MIN_HOST:
        return Check("host python", FAIL, detail, "the server needs Python >= 3.10: install it from python.org and rerun "
                                                  "scripts/install.ps1 (delete .venv first)")
    if "WindowsApps" in sys.executable:
        return Check("host python", WARN, detail + " - Microsoft Store Python",
                     "Store Python sandboxes %LOCALAPPDATA%; install Python from python.org and recreate .venv")
    return Check("host python", PASS, detail)


def check_mcp() -> Check:
    try:
        ver = importlib.metadata.version("mcp")
    except importlib.metadata.PackageNotFoundError:
        return Check("mcp package", FAIL, "not installed in " + sys.executable,
                     "pip install -e . in the repo venv (or rerun scripts/install.ps1)")
    if importlib.util.find_spec("mcp.server") is None:
        return Check("mcp package", FAIL, f"mcp {ver} installed but mcp.server is not importable",
                     "reinstall: pip install --force-reinstall \"mcp>=1.10\"")
    if _vtuple(ver) < MIN_MCP:
        return Check("mcp package", WARN, f"mcp {ver} (needs >= 1.10)", "pip install -U \"mcp>=1.10\"")
    return Check("mcp package", PASS, f"mcp {ver}")


def check_jmag_install(install: config.JmagInstall | None) -> Check:
    if install is None:
        env = os.environ.get("JMAG_HOME")
        where = f"JMAG_HOME={env} is not a JMAG folder" if env else "JMAG_HOME not set and no default location matched"
        return Check("jmag install", FAIL, where + " (needs designer.exe and jmag/designer.py)",
                     "set JMAG_HOME=<folder containing designer.exe> or pass -JmagHome to install.ps1")
    return Check("jmag install", PASS, f"{install.home} (version {install.version})")


def check_designer_exe(install: config.JmagInstall) -> Check:
    exe = install.home / "designer.exe"
    if exe.is_file():
        return Check("designer.exe", PASS, str(exe))
    return Check("designer.exe", FAIL, f"{exe} missing", "repair the JMAG-Designer installation or point JMAG_HOME at another one")


def check_jmag_python(install: config.JmagInstall) -> Check:
    src = "JMAG_PYTHON override" if os.environ.get("JMAG_PYTHON") else "bundled"
    if install.python.is_file():
        return Check("jmag python", PASS, f"{install.python} ({src})")
    fix = ("JMAG_PYTHON points at a missing file: fix or unset it" if os.environ.get("JMAG_PYTHON")
           else "this JMAG install has no python3.8 folder: reinstall JMAG with its Python, or set JMAG_PYTHON")
    return Check("jmag python", FAIL, f"{install.python} missing ({src})", fix)


def check_jmag_python_runs(install: config.JmagInstall, timeout: float = 30) -> Check:
    try:
        r = _run([str(install.python), "-I", "-c", "import sys; print(sys.version)"], timeout,
                 cwd=str(config.state_dir()), env=_worker_env())
    except subprocess.TimeoutExpired:
        return Check("jmag python runs", FAIL, f"no answer within {timeout:.0f} s",
                     "antivirus or a broken install blocks the interpreter: run it by hand to see the error")
    except OSError as e:
        return Check("jmag python runs", FAIL, f"cannot execute: {e}", "check the file and its permissions (JMAG_PYTHON?)")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        return Check("jmag python runs", FAIL, f"exit {r.returncode}: {_tail(r.stderr) or 'no output'}",
                     "run the interpreter by hand to see the error; reinstall JMAG if it is damaged")
    ver = out.split()[0]
    if not ver.startswith("3.8."):
        return Check("jmag python runs", WARN, f"Python {ver} (worker expects 3.8)",
                     "PyJmag.dll crashes on void calls outside CPython 3.8: unset JMAG_PYTHON / use <JMAG>/python3.8")
    return Check("jmag python runs", PASS, f"Python {ver}")


def check_pyjmag_import(install: config.JmagInstall, timeout: float = 60) -> Check:
    name = "pyjmag import"
    module = install.home / "jmag" / "designer.py"
    if not module.is_file() or not (install.home / "jmag" / "private.py").is_file():
        return Check(name, WARN, f"{install.home / 'jmag'} has no designer.py/private.py: Designer is installed "
                                 "without its Python module", "reinstall JMAG-Designer including the Python API component")
    try:
        r = _run([str(install.python), "-I", "-c", PYJMAG_PROBE, str(install.home)], timeout,
                 cwd=str(config.state_dir()), env=_worker_env())
    except subprocess.TimeoutExpired:
        return Check(name, FAIL, f"import of jmag.designer hung for {timeout:.0f} s",
                     "close modal JMAG dialogs / licence prompts and retry; check worker-main.log in the state dir")
    except OSError as e:
        return Check(name, FAIL, f"cannot execute JMAG python: {e}", "see the 'jmag python runs' check")
    marks = [ln for ln in (r.stdout or "").splitlines() if ln.startswith(PROBE_MARK)]
    if r.returncode == 0 and marks:
        if marks[-1].split()[-1] != "True":
            return Check(name, WARN, "jmag.designer imported but has no CreateApplication",
                         "unexpected JMAG Python API version: the bridge targets JMAG-Designer 23.x")
        return Check(name, PASS, "from jmag.designer import designer OK (Designer not started)")
    err = _tail(r.stderr) or _tail(r.stdout) or f"exit {r.returncode}"
    if "No module named 'jmag" in err:
        return Check(name, WARN, f"{err} - Designer seems installed without its Python module",
                     "reinstall JMAG-Designer including the Python API component")
    return Check(name, FAIL, err, "PyJmag failed to load under JMAG's Python: repair JMAG; check that JMAG_PYTHON is "
                                  "JMAG's own python3.8")


def check_state_dir() -> Check:
    d = config.state_dir()
    probe = d / f".doctor-{os.getpid()}.tmp"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        return Check("state dir", FAIL, f"{d} not writable: {e}",
                     "set JMAG_BRIDGE_HOME to a writable folder (journal, worker logs and outputs live there)")
    return Check("state dir", PASS, f"{d} (journal {d / 'journal.jsonl'}, logs worker-*.log, server-startup.log)")


def check_matplotlib() -> Check:
    if importlib.util.find_spec("matplotlib") is None:
        return Check("matplotlib", WARN, "not installed (optional)", "pip install -e .[plots] for jmag_plot/jmag_make_report")
    try:
        ver = importlib.metadata.version("matplotlib")
    except importlib.metadata.PackageNotFoundError:
        ver = "?"
    return Check("matplotlib", PASS, f"matplotlib {ver}")


def check_claude_cli() -> Check:
    exe = shutil.which("claude")
    if exe:
        return Check("claude cli", PASS, exe)
    return Check("claude cli", WARN, "claude not on PATH (only needed for install.ps1 / Claude Code)",
                 "install Claude Code: https://docs.anthropic.com/en/docs/claude-code/setup")


def check_mcp_registration(run_cli: bool = True, timeout: float = 90) -> Check:
    name = "mcp registration"
    if not run_cli:
        return Check(name, PASS, "skipped: called from inside an MCP session, so the server is evidently registered")
    exe = shutil.which("claude")
    if not exe:
        return Check(name, WARN, "skipped: claude CLI not on PATH", "install Claude Code, then rerun scripts/install.ps1")
    try:
        # from the home folder: inside this checkout the repo's plugin .mcp.json (project scope) would shadow the
        # user-scope server that install.ps1 registers
        r = _run([exe, "mcp", "list"], timeout, cwd=str(Path.home()))
    except subprocess.TimeoutExpired:
        return Check(name, WARN, f"`claude mcp list` gave no answer within {timeout:.0f} s", "run `claude mcp list` by hand")
    text = (r.stdout or "") + "\n" + (r.stderr or "")
    rows = [ln.strip() for ln in text.splitlines() if ln.strip().lower().startswith("jmag:")]
    if not rows:
        return Check(name, WARN, "no MCP server named 'jmag' in `claude mcp list` (fine if installed as a plugin)",
                     ".\\scripts\\install.ps1 (or: claude mcp add jmag --scope user -- <venv python> -m jmag_bridge)")
    row = rows[0]
    low = row.lower()
    if "fail" in low or "✗" in row:
        return Check(name, WARN, row, "the server does not start under Claude Code: read <state dir>/server-startup.log")
    if "pending" in low or "approv" in low:
        return Check(name, WARN, row, "start `claude` in this folder and approve the 'jmag' server when asked")
    return Check(name, PASS, row)


def repo_skills_dir() -> Path | None:
    """`.claude/skills` of the source checkout this package runs from (editable install), else None."""
    d = Path(__file__).resolve().parents[2] / ".claude" / "skills"
    return d if d.is_dir() else None


def user_skills_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".claude" / "skills"


def check_skills(repo: Path | None = None, dest: Path | None = None) -> Check:
    repo = repo_skills_dir() if repo is None else repo
    dest = user_skills_dir() if dest is None else dest
    installed = sorted(p.name for p in dest.glob("jmag*") if p.is_dir()) if dest.is_dir() else []
    if repo is None:
        if installed:
            return Check("skills", PASS, f"{len(installed)} in {dest}: {', '.join(installed)}")
        return Check("skills", WARN, f"no jmag* skills in {dest} (fine if installed as a plugin)",
                     "copy .claude/skills/* from the repo, or install the plugin")
    expected = sorted(p.name for p in repo.iterdir() if (p / "SKILL.md").is_file())
    missing = [s for s in expected if s not in installed]
    stale = [s for s in expected if s not in missing and _differs(repo / s / "SKILL.md", dest / s / "SKILL.md")]
    if missing:
        return Check("skills", WARN, f"{len(expected) - len(missing)}/{len(expected)} in {dest}; missing: {', '.join(missing)}"
                                     " (fine if installed as a plugin)", ".\\scripts\\install.ps1 copies them")
    if stale:
        return Check("skills", WARN, f"{len(expected)}/{len(expected)} installed; out of date: {', '.join(stale)}",
                     ".\\scripts\\install.ps1 refreshes them")
    return Check("skills", PASS, f"{len(expected)}/{len(expected)} in {dest}, up to date")


def _differs(a: Path, b: Path) -> bool:
    try:
        return a.read_bytes().replace(b"\r\n", b"\n") != b.read_bytes().replace(b"\r\n", b"\n")
    except OSError:
        return True


def check_designer_processes() -> Check:
    if os.name != "nt":
        return Check("designer processes", PASS, "not Windows: skipped")
    from .launcher import running_instances
    rows = running_instances()
    if not rows:
        return Check("designer processes", PASS, "none running (jmag_connect launches one)")
    desc = ", ".join(f"pid {r['pid']}" + (f" '{r['title']}'" if r.get("title") else "") for r in rows)
    return Check("designer processes", PASS, f"{len(rows)} running: {desc}")


# =========================================================================== runner and output

def _guard(name: str, fn: Callable[..., Check], *args) -> Check:
    try:
        return fn(*args)
    except Exception as e:  # one broken check must never hide the others
        return Check(name, FAIL, f"check crashed: {type(e).__name__}: {e}",
                     "report this at https://github.com/Zwl20085/jmag-agent-suite/issues with the doctor output")


def run_checks(run_claude_cli: bool = True) -> list[Check]:
    checks = [_guard("host python", check_host_python), _guard("mcp package", check_mcp)]
    holder: dict = {}

    def jmag() -> Check:
        holder["install"] = config.find_jmag()
        return check_jmag_install(holder["install"])

    checks.append(_guard("jmag install", jmag))
    install = holder.get("install")
    if install is not None:
        checks.append(_guard("designer.exe", check_designer_exe, install))
        py = _guard("jmag python", check_jmag_python, install)
        checks.append(py)
        if py.status == PASS:
            runs = _guard("jmag python runs", check_jmag_python_runs, install)
            checks.append(runs)
            if runs.status != FAIL:
                checks.append(_guard("pyjmag import", check_pyjmag_import, install))
    checks += [_guard("state dir", check_state_dir), _guard("matplotlib", check_matplotlib),
               _guard("claude cli", check_claude_cli),
               _guard("mcp registration", check_mcp_registration, run_claude_cli),
               _guard("skills", check_skills), _guard("designer processes", check_designer_processes)]
    return checks


def summarize(checks: list[Check]) -> str:
    n = {s: sum(c.status == s for c in checks) for s in (PASS, WARN, FAIL)}
    return f"{n[PASS]} pass / {n[WARN]} warn / {n[FAIL]} fail"


def report(checks: list[Check]) -> dict:
    return {"summary": summarize(checks), "ok": not any(c.status == FAIL for c in checks),
            "checks": [asdict(c) for c in checks]}


def format_table(checks: list[Check]) -> str:
    w = max(len(c.name) for c in checks) if checks else 10
    lines = [f"jmag-agent-suite {__version__} doctor", ""]
    for c in checks:
        lines.append(f"[{c.status}] {c.name.ljust(w)}  {c.detail}")
        if c.status != PASS and c.fix:
            lines.append(f"       {' ' * w}  fix: {c.fix}")
    lines += ["", summarize(checks)]
    return "\n".join(lines)


def cli(as_json: bool = False) -> int:
    """Print the doctor report; exit code 0 iff no check FAILed."""
    checks = run_checks()
    text = json.dumps(report(checks), indent=1) if as_json else format_table(checks)
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # cp1252 consoles vs claude's check marks
    except (AttributeError, ValueError):
        pass
    print(text)
    return 0 if report(checks)["ok"] else 1


def record_startup_failure(exc: BaseException) -> Path | None:
    """Write the traceback to <state dir>/server-startup.log and a one-line pointer to stderr (Claude Code hides
    a server's traceback, so this is the only trace a user can find)."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log: Path | None = None
    for base in (lambda: config.state_dir(), lambda: Path(tempfile.gettempdir())):
        try:
            log = base() / "server-startup.log"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {sys.executable} jmag-agent-suite {__version__}\n{tb}\n")
            break
        except Exception:
            log = None
    where = f" (traceback in {log})" if log else ""
    print(f"jmag-agent-suite failed to start: {type(exc).__name__}: {exc}{where}. "
          f"Run `python -m jmag_bridge --doctor`.", file=sys.stderr, flush=True)
    return log
