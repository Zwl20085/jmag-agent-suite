"""Start JMAG-Designer so that it outlives the agent session.

MCP clients on Windows may run servers inside a Job Object that kills the whole process
tree when the session ends; a JMAG launched from our worker would die with it (unsaved
work included). Launching through WMI (Win32_Process.Create) parents designer.exe to the
WMI service instead, outside any job. A JMAG started this way - or by the user from the
Start menu - can be attached by PID.
"""
from __future__ import annotations

import json
import os
import subprocess
import time

from .config import JmagInstall

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(cmd: str, timeout: int = 60) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True,
                          timeout=timeout, creationflags=_NO_WINDOW).stdout.strip()


def running_instances() -> list[dict]:
    if os.name != "nt":
        return []
    out = _ps("Get-Process designer -ErrorAction SilentlyContinue | "
              "Select-Object Id, MainWindowTitle, @{n='Started';e={$_.StartTime.ToString('s')}} | ConvertTo-Json -Compress")
    if not out:
        return []
    rows = json.loads(out)
    rows = rows if isinstance(rows, list) else [rows]
    return [{"pid": r["Id"], "title": r["MainWindowTitle"], "started": r["Started"]} for r in rows]


def launch_detached(install: JmagInstall, timeout: float = 180) -> dict:
    """Start designer.exe outside our process tree and wait for its main window."""
    exe = str(install.home / "designer.exe")
    cmd = ("$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
           "@{CommandLine='\"%s\" --process-link --no-splash'; CurrentDirectory='%s'}; "
           "\"$($r.ReturnValue) $($r.ProcessId)\"" % (exe, str(install.home)))
    rv, _, pid = _ps(cmd).partition(" ")
    if rv.strip() != "0" or not pid.strip().isdigit():
        raise RuntimeError(f"Win32_Process.Create failed (return {rv!r})")
    pid_i = int(pid)
    t_end = time.time() + timeout
    while time.time() < t_end:
        title = _ps(f"(Get-Process -Id {pid_i} -ErrorAction SilentlyContinue).MainWindowTitle")
        if title.startswith("JMAG-Designer"):
            time.sleep(2)  # let the GUI finish initialising before the first API call
            return {"pid": pid_i, "title": title}
        if not _ps(f"Get-Process -Id {pid_i} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"):
            raise RuntimeError("designer.exe exited during start-up (license problem?)")
        time.sleep(1)
    raise TimeoutError(f"JMAG-Designer (pid {pid_i}) showed no main window within {timeout:.0f}s; "
                       "a license or start-up dialog may be waiting (jmag_dialogs / jmag_screenshot)")
