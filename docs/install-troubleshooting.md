# Install troubleshooting

One section per symptom. Commands are PowerShell, run from the repository folder unless stated.
`<state dir>` means the bridge's state folder: `JMAG_BRIDGE_HOME` if set, otherwise
`%LOCALAPPDATA%\jmag-agent-suite` (an existing `%LOCALAPPDATA%\jmag-mcp-bridge` folder from an older install is
reused instead).

`--doctor`, `jmag_doctor` and `jmag_guide` arrived in 0.3.0 (`.\.venv\Scripts\python.exe -m jmag_bridge --version`
prints yours). On an older version, update first (see "Update or uninstall").

## Claude Code shows no jmag tools, or the MCP server failed

Cause: Claude Code reads MCP registrations at start-up, or the server process could not start (wrong path, missing
package, Python too old).

1. Restart Claude Code fully.
2. Check the registration:
   ```powershell
   claude mcp list
   ```
   `jmag` should be listed and connected. If it is missing, run `.\scripts\install.ps1` again. Run this from your
   home folder: inside the repository the plugin's `.mcp.json` (project scope) can shadow the user-scope server.
3. Run the environment checks. They need no JMAG session:
   ```powershell
   .\.venv\Scripts\python.exe -m jmag_bridge --doctor
   ```
   Each row is PASS, WARN or FAIL; every non-PASS row has a `fix:` line. The exit code is 0 when nothing FAILs.
   `--doctor --json` prints the same report as JSON. In a session, the read-only tool `jmag_doctor` runs the same
   checks (without `claude mcp list`). The installer runs the doctor as its last step.
4. Read `<state dir>\server-startup.log`. When the server cannot import or start, it appends the traceback there
   (or to `%TEMP%\server-startup.log` if the state dir is not writable) and prints a one-line pointer to stderr.
5. Start the server by hand to see its error. It waits for stdio input, so a quiet start is normal; stop it with
   Ctrl+C:
   ```powershell
   .\.venv\Scripts\python.exe -m jmag_bridge
   ```

## JMAG not found

Cause: the server looks for a folder that contains both `designer.exe` and `jmag\designer.py`. Search order in
`config.py`: the `JMAG_HOME` environment variable, then an explicit argument, then the newest match of these patterns:

- `C:\Program Files\JMAG-Designer*`, `D:\Program Files\JMAG-Designer*`, `E:\Program Files\JMAG-Designer*`
- `C:\JMAG-Designer*`, `D:\JMAG-Designer*`

`jmag_doctor` reports this as the `jmag install` row. Fix for any other location, at install time (the installer
stops if the folder has no `designer.exe`, passes the folder to the server as `JMAG_HOME` and to its final doctor
run):

```powershell
.\scripts\install.ps1 -JmagHome "E:\JMAG-Designer23.1"
```

or re-register the server with the variable:

```powershell
claude mcp remove jmag --scope user
claude mcp add jmag --scope user -e JMAG_HOME="E:\JMAG-Designer23.1" -- "$PWD\.venv\Scripts\python.exe" -m jmag_bridge
```

Check what the package sees:

```powershell
.\.venv\Scripts\python.exe -c "from jmag_bridge.config import find_jmag; i=find_jmag(); print(i.as_dict() if i else 'NOT FOUND')"
```

## The worker fails, or PyJmag crashes

Cause: JMAG's Python API (`PyJmag.dll`) is only safe under CPython 3.8. The worker therefore runs on JMAG's bundled
`<JMAG_HOME>\python3.8\python.exe`, not on your venv Python. On CPython 3.11 every void call was an access violation
(`docs/jmag-facts.md`).

Fixes:

- Make sure `<JMAG_HOME>\python3.8\python.exe` exists. If your JMAG keeps it elsewhere, point to it:
  ```powershell
  claude mcp remove jmag --scope user
  claude mcp add jmag --scope user -e JMAG_PYTHON="C:\path\to\python.exe" -- "$PWD\.venv\Scripts\python.exe" -m jmag_bridge
  ```
- Do not set `JMAG_PYTHON` to a newer CPython. It is expected to crash.
- Run the doctor: the `jmag python`, `jmag python runs` and `pyjmag import` rows start JMAG's Python and import
  `jmag.designer` under it without starting Designer.
- Call `jmag_status`. The failing call's `error.hint` and `jmag_messages` usually name the cause. The worker's own log
  is `<state dir>\worker-main.log` (`worker-runner.log` for solves, `worker-selftest.log` for the self-test).

## `python` is the Microsoft Store stub

Cause: Windows ships a `python.exe` alias that opens the Store instead of running Python, so `python -m venv .venv`
fails or does nothing useful. `where python` shows a path under `WindowsApps` if so.

`install.ps1` looks for Python 3.10 or newer in this order: `py -3.14` down to `py -3.10`, then `python` and
`python3` on PATH. It skips the Store stub (does not run), Store Python (its AppData is sandboxed) and anything older
than 3.10, and if nothing is left it stops with the list of rejected interpreters.

Fix: install Python 3.10 or newer from python.org (or `winget install Python.Python.3.12`), then either switch off the
alias (Settings, Apps, Advanced app settings, App execution aliases) or call the launcher explicitly:

```powershell
.\scripts\install.ps1 -Python "C:\Python312\python.exe"
```

`-Python` picks the interpreter for a new `.venv`. An existing `.venv` is reused; if it no longer runs or is older
than 3.10 the installer stops and asks you to delete it.

## Skills are not loaded

Cause: the ten skills must be where Claude Code can see them. `install.ps1` copies each folder from `.claude\skills`
to `%USERPROFILE%\.claude\skills\`, replacing old copies. Inside the repository they load without copying. The
`-NoSkills` switch skips the copy.

The doctor's `skills` row reports skills that are missing or out of date compared with the repository.

Fix:

```powershell
.\scripts\install.ps1
dir $env:USERPROFILE\.claude\skills\jmag*
```

Restart Claude Code afterwards. If the client cannot load skills at all (any client other than Claude Code), ask the
agent to call `jmag_guide` first. With no arguments it returns the index and the router skill;
`jmag_guide(topic="jmag-simulation")` returns one skill. See [other-clients.md](other-clients.md).

## install.ps1 stops with an error

- *the Claude Code CLI ('claude') is not on PATH*: install Claude Code, open a new terminal and rerun, or pass
  `-NoMcp` to install the venv and the skills without registering the server (for other MCP clients).
- *-JmagHome '...' has no designer.exe*: pass the folder that contains `designer.exe`.
- *no usable Python >= 3.10*: see the Microsoft Store section above.
- *pip install -e .[plots] failed*: usually network or proxy; the pip output is printed below the message.
- *Installed, but the doctor reported FAIL rows*: the install itself finished; apply the `fix:` lines and rerun
  `.\.venv\Scripts\python.exe -m jmag_bridge --doctor`.

## The self-test fails

`jmag_self_test` builds a C-core inductor, solves it, and checks six known answers. Read the failure text:

- Licence: a licence dialog or an unlicensed module stops the launch or the run. Call `jmag_dialogs` and
  `jmag_screenshot`. JMAG has no licence query; only you or your administrator can fix it. Do not retry in a loop.
- Modal dialogs: an open dialog blocks every call. Close it with `jmag_dialogs(click='Cancel')`.
- Geometry import hangs: the Geometry Editor needs a saved project. The self-test creates one; for your own scripts
  call `jmag_new_project` or `jmag_save_project` first.
- Values outside range: the result lists each check with its range. Send it with a bug report. The README says
  versions other than 23.0 may need small adjustments.
- Orphaned helpers from earlier crashes: `jmag_cleanup` (touches only orphans).

## A dialog blocks JMAG

Cause: JMAG blocks on modal dialogs (save-project prompt, licence, overwrite). A call then times out or the worker
restarts.

```text
jmag_dialogs                      list dialogs with title, text and buttons
jmag_screenshot                   all JMAG windows as images, even hidden ones
jmag_dialogs(click='Cancel')      press a safe button, then call jmag_connect
```

## Where are the logs and the journal

All under `<state dir>`:

| Item | Location |
|---|---|
| Journal of every state-changing tool call | `<state dir>\journal.jsonl` (read it with `jmag_journal`; `jmag_journal(export_script=...)` can replay lost steps) |
| Persistent state, including the star-prompt answer | `<state dir>\state.json` |
| Server start-up log | `<state dir>\server-startup.log` (tracebacks of failed starts) |
| Index of your installed JMAG manual | `<state dir>\docs\<JMAG version>\` |
| Worker logs | `<state dir>\worker-main.log`, `worker-runner.log`, `worker-selftest.log` |

Open the folder:

```powershell
explorer (Join-Path $env:LOCALAPPDATA "jmag-agent-suite")
```

## Update or uninstall

Update:

```powershell
git pull
.\scripts\install.ps1
```

The installer reuses `.venv`, reinstalls the package, re-registers the server, replaces the skills and runs the
doctor. Restart Claude Code.

Uninstall:

```powershell
.\scripts\install.ps1 -Uninstall        # add -Scope project|local if you registered with that scope
```

This removes the `jmag` MCP registration and the installed skills and keeps `.venv`. Then delete the repository
folder (this removes `.venv`). Delete `<state dir>` only if you do not need the journal.

## Plugin install needs uv

Cause: the plugin's `.mcp.json` starts the server with `uvx --from ${CLAUDE_PLUGIN_ROOT} jmag-mcp`, so `uv` must be
on PATH.

```powershell
winget install --id astral-sh.uv
uvx --version
```

Then run `/plugin install jmag@jmag-agent-suite` again and restart Claude Code. The plugin asks for an optional
`JMAG_HOME`; leave it empty to auto-detect. If you do not want uv, use `.\scripts\install.ps1` instead. A plugin
server that fails to start also writes `<state dir>\server-startup.log`.

## Reporting a bug

Open an issue at https://github.com/Zwl20085/jmag-agent-suite/issues and paste:

1. The doctor output: `.\.venv\Scripts\python.exe -m jmag_bridge --doctor`.
2. The last 50 lines of the start-up log:
   ```powershell
   Get-Content "$env:LOCALAPPDATA\jmag-agent-suite\server-startup.log" -Tail 50
   ```
3. The relevant part of `jmag_journal`, and the failing tool's `error.message`, `error.hint` and `jmag_messages`.
4. Your JMAG version, Windows version and Python version.

Do not attach `.jproj` files, JMAG manuals or files from the JMAG installation.
