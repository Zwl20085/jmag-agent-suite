"""Watch a running JMAG solve from the outside (files + processes), no JMAG API needed.

The calculation folder of a case (``<project>.jfiles/<model>~N/<study>~M/CaseK``) contains

* ``jmonitor``  - one ``*End_of_Step`` block per finished step  -> progress
* ``jcgmntr``   - rows ``step  nonlinear_iteration  linear_iterations`` -> convergence
* ``Designer.log`` - XML-ish messages with ``<status>Warning|Error</status>``

The solver runs as ``ExecSolver.exe <calc folder>/Designer.jcf`` -> ``jam.exe``.
``Application.CancelProcess`` blocks from a second connection, so cancelling is
done by terminating that process tree (Designer then returns with no result).
"""
from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path


def progress(folder: str | os.PathLike) -> dict:
    f = Path(folder) / "jmonitor"
    if not f.is_file():
        return {"steps_done": 0, "monitor_found": False}
    try:
        text = f.read_text(errors="ignore")
    except OSError:
        return {"steps_done": 0, "monitor_found": True}
    return {"steps_done": text.count("*End_of_Step"), "monitor_found": True,
            "last_write_age_s": round(max(0.0, _now() - f.stat().st_mtime), 1)}


def _now() -> float:
    import time
    return time.time()


def convergence(folder: str | os.PathLike, max_iter: int | None = None) -> dict:
    """Nonlinear iterations per step from jcgmntr; flags steps that hit the limit."""
    f = Path(folder) / "jcgmntr"
    if not f.is_file():
        return {"available": False}
    iters: dict[int, int] = defaultdict(int)
    for line in f.read_text(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            step, it = int(parts[0]), int(parts[1])
            iters[step] = max(iters[step], it)
    if not iters:
        return {"available": False}
    vals = list(iters.values())
    out = {"available": True, "steps": len(iters), "max_nonlinear_iterations": max(vals),
           "mean_nonlinear_iterations": round(sum(vals) / len(vals), 2)}
    if max_iter:
        hit = sorted(s for s, v in iters.items() if v >= max_iter)
        out["steps_at_iteration_limit"] = hit[:50]
        out["n_steps_at_limit"] = len(hit)
    return out


_MSG = re.compile(r"<message>(.*?)</message>", re.S)


def log_messages(folder: str | os.PathLike, statuses=("Warning", "Error")) -> list[dict]:
    f = Path(folder) / "Designer.log"
    if not f.is_file():
        return []
    out = []
    for block in _MSG.findall(f.read_text(errors="ignore")):
        st = re.search(r"<status>(\w+)</status>", block)
        if not st or st.group(1) not in statuses:
            continue
        tx = re.search(r"<text>(.*?)</text>", block, re.S)
        step = re.search(r'<data key="STEP">(\d+)</data>', block)
        text = re.sub(r"<[^>]+>", " ", tx.group(1) if tx else "")
        out.append({"status": st.group(1), "text": " ".join(text.split())[:400],
                    "step": int(step.group(1)) if step else None})
    return out


def solver_processes(folder: str | None = None) -> list[dict]:
    """ExecSolver.exe processes (optionally only the one working in `folder`)."""
    if os.name != "nt":
        return []
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='ExecSolver.exe'\" | "
          "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    procs = []
    norm = _norm(folder) if folder else None
    for line in out.splitlines():
        if "\t" not in line:
            continue
        pid, cmd = line.split("\t", 1)
        if norm and norm not in _norm(cmd):
            continue
        procs.append({"pid": int(pid), "command": cmd.strip()})
    return procs


def _norm(p: str) -> str:
    return str(p).replace("\\", "/").lower().rstrip("/")


def kill_solver(folder: str | None = None) -> list[int]:
    killed = []
    for p in solver_processes(folder):
        subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        killed.append(p["pid"])
    return killed


def cleanup_orphans() -> dict:
    """Terminate modeller.exe helpers whose parent designer.exe is gone."""
    if os.name != "nt":
        return {"killed": []}
    ps = ("$d = @(Get-Process designer -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }); "
          "Get-CimInstance Win32_Process -Filter \"Name='modeller.exe'\" | "
          "Where-Object { $d -notcontains $_.ParentProcessId } | ForEach-Object { $_.ProcessId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60,
                         creationflags=subprocess.CREATE_NO_WINDOW).stdout
    killed = []
    for line in out.split():
        if line.strip().isdigit():
            subprocess.run(["taskkill", "/PID", line.strip(), "/F"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
            killed.append(int(line))
    return {"killed_orphan_modeller_pids": killed, "solver_processes": solver_processes()}
