# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

JMAG Agent Suite (repo `jmag-agent-suite`, Python package `jmag_bridge`, MCP server `jmag`): an MCP
server plus a family of Claude skills (`.claude/skills/`) that let an agent drive JMAG-Designer
(commercial electromagnetic FEA, Windows) and judge whether a simulation is behaving normally.
Target: JMAG-Designer 23.x. The skills are the operating manual for *using* the tools; this
file is for *changing* the code. Measured JMAG behaviour lives in `docs/jmag-facts.md`.

## Commands

```powershell
python -m venv .venv; .venv\Scripts\pip install -e ".[dev,plots]"   # dev install (Python >= 3.10)
.venv\Scripts\python -m pytest                                 # offline tests (no JMAG needed)
.venv\Scripts\python -m pytest tests/test_health.py::test_power_balance_ideal_pm_machine   # one test
.venv\Scripts\python -m jmag_bridge                           # run the MCP server (stdio)
```

Live checks against a licensed JMAG (slow, launch real JMAG windows):

```powershell
.venv\Scripts\python -c "from jmag_bridge.config import find_jmag; from jmag_bridge.selftest import run_self_test; print(run_self_test(find_jmag()))"
.venv\Scripts\python examples\spm_motor\run_mcp.py <work_dir>          # full motor flow over MCP
.venv\Scripts\python tests\live\mcp_client_flow.py <jmag_pid> <study>   # tool smoke test on a solved study
```

Run the self-test after touching `worker/ops.py`, `server.py` run/cancel logic, or `selftest.py`.
pytest's default `-m 'not live'` keeps CI JMAG-free.

## Architecture (the parts you only see by reading several files)

```
MCP client --stdio--> server.py (Python >=3.10, MCP SDK)
                        |-- worker_client.Worker "main"   --pipes--> worker/jmag_worker.py + ops.py
                        |-- worker_client.Worker "runner" (one per jmag_run)   (JMAG's python3.8)
                        |                                                   --ProcessLink--> designer.exe
                        |-- health.py         pure rules + physics invariants (unit-tested)
                        |-- experiments.py    server-side orchestration of standard tests over ops
                        |-- estimate.py / winding.py / units.py   analytic layer (pure, unit-tested)
                        |-- cad3d.py           STEP product / solid names before a 3D import (pure, unit-tested)
                        |-- plotting.py / reporting.py / compare.py   post-processing (pure)
                        |-- solver_monitor.py  reads jmonitor/jcgmntr/Designer.log, kills ExecSolver
                        |-- winui.py + ps/*.ps1  UI Automation: dialogs, clicks, window screenshots
                        |-- launcher.py        starts designer.exe detached (WMI) so it outlives the session
                        `-- docs_index.py      indexes the *installed* JMAG help/API at runtime
```

Hard constraints behind this split:

- **`worker/ops.py` and `worker/jmag_worker.py` must stay Python 3.8 compatible** (no `match`,
  no `X | Y` / `list[str]` at runtime, no walrus-dependent 3.9+ APIs). They run under
  `<JMAG>/python3.8/python.exe` because `PyJmag.dll` crashes on void calls under newer CPython.
  `server.py` imports only `ops.pid_alive` from there.
- **Protocol isolation:** the worker dups the original stdin/stdout for JSON lines prefixed with
  `@@JMAGRPC@@`, then points fd 0/1/2 (and the Win32 std handles) at NUL/log so PyJmag and
  designer.exe chatter can't corrupt the stream. `Worker._reader` ignores unprefixed lines.
- **Error model:** JMAG logs errors instead of raising. `Context.dispatch` clears the error state,
  diffs `app.GetAllMessages()` around each op and sets `ok=false` (`JmagError`) if `HasError()`.
  Consequence: an op must not *probe* with calls that log errors (missing-name lookups,
  `GetLink` on non-circuit conditions, `GetValue` on Integer/Vector properties). Use
  `_find_condition`, `read_prop`, type gates; if a probe is unavoidable, `ClearError()` after it.
- Every object fetched goes through `need()` (IsValid), every value set goes through `set_props`
  (read-back + close-match suggestions for unknown names). Keep that pattern for new ops.
- Ops are registered with `@op(read_only=...)`; the server keeps its own `READ_ONLY_OPS` set
  (calls allowed while a solve runs) and journals every non-read-only call to
  `%LOCALAPPDATA%/jmag-agent-suite/journal.jsonl` (`replay.py` turns it into a script).
- **Runs** happen in a separate "runner" worker attached to the same PID, so status/progress/cancel
  stay responsive. Cancel order matters (verified): press Abort -> answer "Stop calculation" ->
  only then kill the ExecSolver/jam tree -> release the runner. Killing first crashed Designer.
- Tool envelope: `{"ok", "result", "error": {"message","hint"}, "jmag_messages", "seconds"}`.
  Hints are part of the UX - write them as the next action the agent should take.

## Adding a tool

1. Implement the op in `worker/ops.py` (3.8 syntax, `need`/`set_props`/`resolve_part`).
2. Expose it in `server.py` with a docstring written for an LLM caller (units, examples, pitfalls);
   add it to `READ_ONLY_OPS` if it doesn't change state.
3. If it adds a rule or invariant, put the logic in `health.py` and test it in `tests/test_health.py`.
4. Document verified property names/behaviour in `.claude/skills/jmag-simulation/references/`
   (advanced API: `references/advanced-api.md`) and mention the tool in
   `.claude/skills/jmag/references/tool-map.md`. Only write down behaviour you observed on a real
   JMAG; mark anything else as unverified, in the docstring too.
5. `tests/test_tool_names.py` fails if README or any skill names a tool that is not registered.
   Pure logic (metrics, estimates, diffs) goes in its own module with tests; server-side
   orchestration (`experiments.py`) composes ops and never talks to JMAG directly.

## Facts that are easy to get wrong

- Static2D/Static studies accept no circuit; FEM coils need Transient/Frequency studies.
- 2D `ModelThickness` defaults to 1 mm. Ground components are always named "Ground".
- Circuit terminal offsets are in `ops.TERMINALS` (calibrated from exported circuit images).
- `GetDataFromName` needs internal keys (LineCurrent, TerminalVoltage, FEMCoilFlux ...);
  `_table_data` matches display names first.
- JMAG may rewrite step control for motion; post-processing must use returned time axes.
- Geometry Editor import requires a saved project, else a modal dialog blocks forever.

## Repo hygiene

This repo is public: never commit `.jproj`/`.jfiles`, solver outputs, JMAG manuals or anything
from the JMAG installation (`.gitignore` covers the usual artefacts), and keep personal paths out
of examples/tests (take paths as arguments).
