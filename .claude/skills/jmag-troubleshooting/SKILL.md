---
name: jmag-troubleshooting
description: Diagnoses JMAG-Designer problems seen through the jmag MCP tools - hangs and modal dialogs, crashes (JmagCrashed), worker timeouts and restarts, non-convergence, negative element volume, wrong torque sign or magnitude, empty or missing result tables, parts missing or merged after import, licence failures, and orphaned JMAG processes - with a symptom -> tool -> fix tree and the meaning of every finding code from jmag_check and the post-run check. Use when a jmag_* call fails or times out, a run aborts, results look wrong or empty, or the user reports that JMAG is stuck, crashed or behaving strangely.
---

# Troubleshooting JMAG through the bridge

Diagnose before you fix: collect the evidence with read-only tools, name the cause with the
evidence, then change one thing and rerun the cheapest check that would show the fix.
Never "fix" by deleting results, killing processes or re-running blindly without saying so.

## First five moves (cheap, read-only)

1. `jmag_status` - is JMAG alive, which instance, is a run active, any dialogs?
2. `jmag_dialogs` (+ `jmag_screenshot`) - a modal dialog explains most hangs.
3. The failing call's own `error.type`, `error.hint`, `jmag_messages` - usually the answer.
4. `jmag_solver_report(study)` / `jmag_run_status` - solver errors, warnings, convergence.
5. `jmag_list_conditions`, `jmag_circuit_image`, `jmag_model_image` - setup as JMAG sees it.

## Symptom -> tool -> fix

| Symptom | Look with | Usual cause -> fix |
|---|---|---|
| Call times out; "worker was restarted" | `jmag_dialogs`, `jmag_screenshot` | modal dialog (save-project prompt, licence, overwrite) -> press a safe button (`jmag_dialogs(click='Cancel')`), then `jmag_connect` |
| Geometry import hangs | `jmag_dialogs` | project never saved -> `jmag_new_project` / `jmag_save_project` first |
| `JmagCrashed` | `jmag_status` | Designer died -> `jmag_connect(launch_new=true)`, reopen the last saved copy, `jmag_cleanup`; save before risky steps next time |
| `ok: false`, `JmagError` | `jmag_messages` | JMAG logged an error in that op -> treat the result as invalid, fix the named object/property |
| `ok: false`, `OpError` | `error.hint` | bridge refused (unknown name, missing part, read-back mismatch) -> use the suggested names |
| "a solve is running; only read-only tools are allowed" | `jmag_run_status` | wait, or `jmag_cancel_run` |
| Run never finishes / no progress | `jmag_run_status` (steps, convergence, dialogs) | dialog waiting, huge mesh, Newton stuck -> `jmag_cancel_run` (never kill Designer) |
| NEGATIVE_ELEMENT_VOLUME | contour/model image with mesh | moving parts on a normal mesh or a part bridging rotor and stator -> slide mesh (`MeshType 1`, `AutoSlide 1`), suppress the gap part |
| NONLINEAR_NOT_CONVERGED / ITERATION_LIMIT | `convergence.steps_at_iteration_limit` | at current zero-crossings: harmless; at peak load: `NonlinearMaxIteration 50`, refine mesh, shorter step, check B-H |
| Torque / flux / EMF too small by a factor = stack length in mm (e.g. 50x) | `jmag_get_properties(kind='study')` | `ModelThickness` still 1 mm (DEFAULT_STACK_LENGTH) |
| Torque wrong sign | source phase, rotation direction, Torque axis | convention, not physics -> sweep the current angle, state the convention |
| Torque wrong magnitude (other factors) | `jmag_physics_check('power_balance')`, flux linkage vs estimate | turns (Turn spread over k regions), magnet orientation, phase angle, rotor parts missing from Torque condition |
| Empty result table | `jmag_list_results` | display name used in raw API (needs internal key), table not enabled (`Energy: 1`), study not solved, aborted run (fewer rows) |
| ZERO_CURRENT / currents all 0 | `jmag_circuit_image`, `jmag_circuit_summary` | source amplitude 0, wiring, FEM coil not linked (UNLINKED/BAD_LINK) |
| KVL residual > 1e-3 | `jmag_circuit_image` | probe on the wrong node, coil polarity, wrong resistance passed |
| Parts missing / merged after import | `jmag_list_parts` areas vs script | open loop or tiny gap in the DXF -> fix the script, re-import |
| Unnamed `0/Region.N` left | `jmag_list_parts` | air gap / pocket face without a label -> rename and suppress or set Air |
| Results look identical across cases | `jmag_compare` | the variable never reached the study (equation not used as a property value) |
| Licence error / feature dialog | `jmag_dialogs`, `jmag_capabilities` | module not licensed or licence server down -> tell the user; do not retry in a loop. `jmag_capabilities` lists offered study types, but JMAG has no licence query: the run report names a missing module |
| Crash during a field-table export or case report | `jmag_status` | a probe sequence ExportTable -> ExportResultValuesTable -> WriteCaseReport crashed Designer on 23.0 -> save before `jmag_export_table` (field kinds) / `jmag_case_report`; prefer `jmag_export_results` |
| Loss study aborts "Frequency is 0[Hz]" | `jmag_solver_report` | loss study without `frequency_hz` -> pass `jmag_loss_study(frequency_hz=f_e)` |
| Many JMAG processes, helpers left over | `jmag_status`, `jmag_cleanup` | orphaned `modeller.exe` from dead Designers -> `jmag_cleanup` (only touches orphans) |
| Toolchain doubt | `jmag_self_test` | 6/6 PASS means bridge + JMAG + licence are fine; the problem is the model |

Longer explanations and the decision tree for hangs, crashes and wrong results:
`references/symptoms.md`. Every finding code with level and fix: `references/finding-codes.md`.

## Process hygiene (what is and is not safe)

- `jmag_cancel_run` follows the verified order: Abort -> answer "Stop calculation" -> only then
  terminate the solver. Killing the solver first has crashed Designer.
- `jmag_cleanup` only kills helpers whose parent Designer is gone. Never kill a Designer the
  engineer opened; ask.
- A Designer launched by the bridge is detached and survives the agent session; tell the user
  it is still open (or `jmag_quit(save=...)` when they agree).
- After any crash, assume the project on disk is the last saved state; unsaved changes are lost.
  `jmag_journal(export_script=...)` can replay the lost steps.

## When to stop and ask the user

- The fix would delete studies/results, overwrite their project, or kill a process the bridge
  did not start.
- The same step crashed Designer twice: report the evidence (step, messages, screenshot) and
  propose a simpler path rather than a third attempt.
- A licence or module is missing: only the user (or their admin) can change that.
- The evidence points to the user's inputs (geometry script, material data, turns) rather than
  to the setup: show the discrepancy and ask which value is right.

## How to report a problem you diagnosed

Symptom (what was seen, with the finding code or message) -> evidence (tool outputs) -> cause
-> fix applied -> the check that now passes (e.g. "power balance 0.4 %, KVL 2e-7"). If no
check passes yet, say the problem is open; never present a workaround as a verified fix.
