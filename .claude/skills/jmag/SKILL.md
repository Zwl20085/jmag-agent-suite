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
| set up, run, debug or validate one study (the core loop, feedback ladder, JMAG quirks) | `jmag-simulation` |
| a 3D static study from a STEP model (transformer / inductor L matrix, coil inflow faces, symmetry; PM machine psi / torque / cogging per rotor angle, periodic sector, nonlinear B-H) | `jmag-simulation` -> `references/static-3d.md` |
| draw or import geometry (DXF/STEP + labels JSON, templates, symmetry sectors) | `jmag-geometry` |
| size a machine, pick slots/poles/winding, sanity-check a design by hand | `jmag-machine-design` |
| run a standard test: cogging, back-EMF, Ld/Lq, load point, torque-speed, locked rotor, OC/SC, force-stroke, coupling k | `jmag-experiments` |
| iron/copper/magnet loss, efficiency, efficiency maps, temperature | `jmag-losses-efficiency` |
| sweeps, design tables, DOE, optimisation, Pareto fronts, maps as sweeps | `jmag-parametric-optimisation` |
| read results, FFT/ripple/harmonics, plots, comparisons, the final report | `jmag-postprocess` |
| something no tool covers (`jmag_exec_python`, API lookup, promoting a snippet to a tool) | `jmag-scripting` |
| a hang, dialog, crash, non-convergence, wrong torque, empty tables, missing parts | `jmag-troubleshooting` |

Most real jobs chain several: design -> geometry -> simulation -> experiments -> losses ->
parametric -> postprocess.

## Safety rules (non-negotiable)

1. **Work on copies.** Never experiment in the engineer's only copy of a valuable `.jproj`:
   `jmag_save_project(path=<copy>)` first. Save before the first mesh/run of a new setup.
2. **Do not grab the engineer's windows.** `jmag_connect` without `pid` lists running
   instances instead of attaching; attach only to the instance the user names.
3. **`ok: false` is a stop sign.** JMAG logs errors instead of raising them; a failed step
   leaves an invalid object and everything built on it silently does nothing. Read
   `error.hint` and `jmag_messages`, fix, then continue.
4. **Never claim success because a run finished.** Back every number with checks from the
   feedback ladder (`jmag-simulation`): post-run verdict, a physics invariant, and a hand
   estimate. Report FAIL/CHECK findings; never omit them.
5. **Ask before destructive or expensive actions** (deleting studies, overwriting results,
   long batch runs, killing processes other than orphans).
6. **Know the install.** `jmag_capabilities` lists the study types and API features this JMAG
   offers before you promise a loss study, efficiency map, thermal or optimisation run. JMAG
   has no licence query: a listed type can still fail at Run if the module is not licensed.
   When `jmag_status`/`jmag_connect` report JMAG not found or the worker failed, call
   `jmag_doctor` and relay each `fix` before suggesting a reinstall.
   - If `jmag_capabilities` or `jmag_self_test` returns `first_run_note`, relay it to the user
     verbatim and follow `first_run_action`: wait for the answer, then call `jmag_star_repo` with
     `star`, `open` or `dismiss`. Never call `jmag_star_repo` without an explicit answer.

## Tool map by phase

| Phase | Tools |
|---|---|
| Session | `jmag_status` `jmag_connect` `jmag_capabilities` `jmag_dialogs` `jmag_screenshot` `jmag_cleanup` `jmag_quit` `jmag_doctor` |
| Project | `jmag_new_project` `jmag_open_project` `jmag_save_project` `jmag_summary` `jmag_inventory` |
| Geometry | `jmag_import_dxf` `jmag_import_cad` `jmag_list_parts` `jmag_rename_parts` `jmag_suppress_parts` `jmag_model_image` (3D: check the STEP first with `python -m jmag_bridge.cad3d`, then `jmag-simulation` -> `references/static-3d.md`) |
| Design (pure, offline) | `jmag_estimate` `jmag_units` `jmag_winding_plan` |
| Study setup | `jmag_create_study` `jmag_duplicate_study` `jmag_delete_study` `jmag_get_properties` `jmag_set_properties` `jmag_set_step` `jmag_set_equations` `jmag_get_equations` |
| Materials | `jmag_find_materials` `jmag_create_material` `jmag_set_material` |
| Excitation | `jmag_add_current` `jmag_add_fem_coil` `jmag_add_fem_conductor` `jmag_winding` `jmag_add_condition` `jmag_list_conditions` `jmag_delete_condition` |
| Circuit | `jmag_circuit_quick` `jmag_circuit_add` `jmag_circuit_summary` `jmag_circuit_image` |
| Mesh | `jmag_mesh` `jmag_mesh_size` |
| Run | `jmag_check` `jmag_run` `jmag_run_status` `jmag_cancel_run` `jmag_solver_report` |
| Standard tests | `jmag_experiment` |
| Losses / efficiency | `jmag_loss_study` `jmag_efficiency_map` |
| Parametric | `jmag_case_table` `jmag_run_cases` `jmag_optimization_table` |
| Results | `jmag_list_results` `jmag_get_result` `jmag_export_results` `jmag_export_table` `jmag_case_report` `jmag_contour_image` `jmag_physics_check` `jmag_compare` `jmag_plot` `jmag_make_report` |
| Knowledge / escape hatch | `jmag_docs_search` `jmag_docs_page` `jmag_api_lookup` `jmag_exec_python` |
| Meta | `jmag_self_test` `jmag_journal` `jmag_guide` |

One line per tool: `references/tool-map.md`. Never call a `jmag_*` name that is not in that
list; if something is missing, use `jmag-scripting`.
