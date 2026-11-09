# JMAG dialogs and stalled solves

Contents: 1 Known dialogs - 2 Rules for pressing buttons - 3 Stalled solve procedure - 4 Run not owned by the bridge

Sources: `src/jmag_bridge/ps/dialogs.ps1`, `winui.py`, `jmag_cancel_run`, `docs/jmag-facts.md`,
`jmag-simulation/references/jmag-gotchas.md`. Exact dialog titles other than the two verified rows are
**unverified**: read the real title and text from `jmag_dialogs` before pressing anything.

## 1. Known dialogs

`jmag_dialogs` returns `kind` = `modal` | `progress` | `window` per dialog. Main windows
(`[Window ...`, `Geometry Editor - ...`) are not listed.

| Dialog (title) | kind | What it is | Safe button | Status |
|---|---|---|---|---|
| `JMAG-Designer: Run Analysis` | progress | solve progress window; buttons "Customize Monitor..." and "Abort" only (no Cancel) | **none: leave it alone.** Stop with `jmag_cancel_run` | verified (23.0, 2026-11-09) |
| `Stop calculation` | modal | prompt raised by Abort; `jmag_cancel_run` answers it with `Yes, and open results` | only inside `jmag_cancel_run`; do not answer by hand unless the bridge lost the job (section 4) | verified (button label used by the bridge) |
| save-project prompt when the Geometry Editor starts | modal | project was never saved | `Cancel`, then `jmag_save_project` / `jmag_new_project` and retry the import | behaviour verified (facts.md); exact title unverified |
| overwrite confirmation (existing results, files) | modal | JMAG asks before overwriting | `No` / `Cancel` unless the user asked to overwrite | unverified |
| licence / module prompt | modal | module or licence server missing | `Cancel`; report to the user, no retry loop | unverified |
| anything else | - | - | `Cancel` or `No`; if neither exists, screenshot and ask the user | unverified |

## 2. Rules for pressing buttons

- Always pass `title_filter`: `jmag_dialogs(click='Cancel', title_filter='<part of the title>')`.
- Never Cancel/Abort `Run Analysis`; a modal that appeared during a run is listed in `jmag_run_status`
  under `dialogs`, separately from the progress window.
- After pressing, call `jmag_dialogs` again and `jmag_connect` if the earlier call had timed out
  (the worker was restarted).

## 3. Stalled solve procedure

A solve is stalled when `jmag_status` shows `stalled: true` (or `newest_monitor_age_s` far above the usual
step time while `solver_processes` is non-empty). Typical `last_write_age_s` during long 3D solves is
unverified; compare with the elapsed time per step in earlier runs of the same model.

1. `jmag_run_status`: steps done vs the last poll, `convergence`, `dialogs`. A blocking modal explains most stalls.
2. `jmag_screenshot` and `jmag_dialogs`: read the real text. Do not press anything on `Run Analysis`.
3. `jmag_status.solver_processes`: no solver process but a running job means the solve died; go to step 5.
4. Large 3D or nonlinear steps can pause the monitor files for a long time: wait two more polls (60 s each) before acting.
5. Still no change: `jmag_cancel_run`, then re-check `jmag_dialogs` and `jmag_solver_report`; re-run only after
   changing something (mesh, tolerance, step), saying what.
6. Never kill `designer.exe`; ask the user first.

## 4. Run not owned by the bridge

The engineer started the solve by hand, or the bridge restarted and lost the job (`jmag_run_status` says
"no run started in this session"):

- `jmag_cancel_run` works without a bridge-owned job when the connected JMAG shows a `Run Analysis` window
  (verified on 23.0, 2026-11-09: Abort, 'Yes, and open results', the solver ended within 10 s, Designer stayed
  alive). It acts on the connected instance only; read `steps` in its answer. The study keeps the steps solved so
  far: they are incomplete (RUN_INTERRUPTED).
- If it reports nothing to cancel: `jmag_dialogs` shows whether `Run Analysis` is present. If it is,
  ask the user to press Abort themselves and answer `Stop calculation`.
- Do not terminate `ExecSolver` / `jam` processes before the `Stop calculation` prompt is answered (crashed Designer).
- Afterwards `jmag_cleanup` reports leftover solver processes and removes orphan helpers only.
