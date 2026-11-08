---
name: jmag
description: Entry point for any JMAG-Designer or electrical-machine / magnetic-device FEA request (motors, generators, inductors, transformers, actuators, wireless-power coils). Routes to the right jmag-* skill, states the safety rules for driving JMAG through the jmag MCP tools (jmag_*), and gives a phase-by-phase tool map. Use it first whenever the user mentions JMAG, a .jproj project, FEA of an electric machine or magnetic component, or asks which JMAG tool or skill to use.
---

# JMAG agent toolkit: start here

You operate JMAG-Designer 23 for an engineer through the `jmag` MCP server (tools `jmag_*`;
fully qualified in Claude Code as `mcp__jmag__<tool>`). This skill only routes; load the
specialised skill for the actual work.

## Which skill next

| The user wants to ... | Load |
|---|---|
| set up, run, debug or validate one study (core loop, feedback ladder, JMAG quirks) | `jmag-simulation` |
| a 3D static study from a STEP model (transformer / inductor L, PM machine psi / torque / cogging per angle) | `jmag-simulation` -> `references/static-3d.md` (index) |
| draw or import geometry (DXF/STEP + labels JSON, templates, a user-supplied DXF) | `jmag-geometry` |
| size a machine, pick slots/poles/winding, sanity-check a design by hand | `jmag-machine-design` |
| a standard test: cogging, back-EMF, Ld/Lq, load point, torque-speed, OC/SC, force-stroke, coupling k | `jmag-experiments` |
| iron/copper/magnet loss, efficiency, efficiency maps, temperature | `jmag-losses-efficiency` |
| sweeps, design tables, DOE, optimisation, Pareto fronts | `jmag-parametric-optimisation` |
| read results, FFT/ripple, plots, comparisons, the final report | `jmag-postprocess` |
| something no tool covers (`jmag_exec_python`, API lookup) | `jmag-scripting` |
| a hang, dialog, crash, stalled solve, non-convergence, wrong torque, empty tables | `jmag-troubleshooting` |

Real jobs chain several: design -> geometry -> simulation -> experiments -> losses -> parametric -> postprocess.

## First calls in a new conversation

1. `jmag_status`: install found? worker alive? JMAG instances, open dialogs, a job still running?
2. `jmag_capabilities`: JMAG version, study types, API members. Do this before promising a loss study,
   efficiency map, thermal or optimisation run (no licence query exists: a listed type can still fail at Run).
3. If status shows a problem (install null, worker failed, JMAG not found): `jmag_doctor`, relay each `fix`.
4. If any result carries `first_run_note`, relay it verbatim, wait for the answer, then call
   `jmag_star_repo` with `star`, `open` or `dismiss` (`first_run_action`). Never call it unanswered.
5. In a `.jproj` you did not build: `jmag_open_project(<copy>)` then `jmag_inventory` before touching anything.

## Conventions that hold everywhere

- Lengths mm; currents A **peak** (never rms) unless a tool says otherwise; `AngularVelocity` in rpm;
  time and steps in s; angles deg; torque N·m; flux linkage Wb.
- 2D `ModelThickness` defaults to **1 mm**: set the stack length
  (`jmag_set_properties(kind='study', values={'ModelThickness': mm})`) or torque, flux and EMF are wrong.
- `poles` always means 2p (the number of poles), never pole pairs.
- Every result is `{ok, result | error, seconds}`. Result reads (`jmag_get_result`, `jmag_export_results`,
  `jmag_plot`, `jmag_experiment`) carry `result.unverified`: the checks not yet run on that study's current
  results this session (`post-check` = `jmag_check(stage='post')`, `physics-check` = `jmag_physics_check`). Run
  them, or report the numbers as unverified. `jmag_check` / `jmag_physics_check` / `jmag_self_test` use `ok` for
  "the tool ran"; the judgement is `passed` and `verdict` in `result`.

## Run contract

- `jmag_run` returns `state` = `running` | `finished` | `failed`, plus `ok` and `solver_aborted`; `ok:false` with `solver_aborted:true` means unusable results.
- While `running`, poll `jmag_run_status` every 30-60 s; `jmag_status` shows `stalled` / `newest_monitor_age_s`.
- Cancel only with `jmag_cancel_run`.
- Never press Cancel/Abort on the 'Run Analysis' window with `jmag_dialogs`.
- Keep `wait_seconds` below your client's tool-call timeout: check the client's setting (Claude Code reads it from
  the `MCP_TOOL_TIMEOUT` environment variable). If you cannot find it, keep the default `wait_seconds` and poll;
  the run continues in the background either way.

## Safety rules (non-negotiable)

1. **Work on copies.** `jmag_save_project(path=<copy>)` before experimenting; save before the first mesh/run of a new setup.
2. **Do not grab the engineer's windows.** `jmag_connect` without `pid` lists running instances; attach only to the one the user names.
3. **`ok: false` is a stop sign.** JMAG logs errors instead of raising; a failed step leaves an invalid object.
   Read `error.hint` and `jmag_messages`, fix, then continue.
4. **A finished run proves nothing.** Back every number with the feedback ladder (`jmag-simulation`): post-run
   verdict, a physics invariant, a hand estimate. Report FAIL/CHECK findings; never omit them.
5. **Ask before destructive or expensive actions**: deleting studies, overwriting results, batches over 50 runs or 1 h, killing processes other than orphans.
6. **Know the install** (`jmag_capabilities`); on a missing install or failed worker call `jmag_doctor` before suggesting a reinstall.

## Tool map by phase

Session - Project - Geometry - Design helpers (offline) - Study setup - Materials - Excitation - Circuit -
Mesh - Run - Standard tests - Losses / efficiency - Parametric - Results - Knowledge / escape hatch - Meta.
One line per tool, new tools and the blocking time of each slow one: `references/tool-map.md`.
Never call a `jmag_*` name that is not listed there; if something is missing, use `jmag-scripting`.
