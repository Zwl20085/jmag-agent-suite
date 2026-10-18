# Tool map: every jmag_* tool in one line

Contents: Session - Project - Geometry - Design helpers - Study setup - Materials - Excitation -
Circuit - Mesh - Run - Standard tests - Losses and efficiency - Parametric - Results -
Knowledge - Meta

Every tool returns `{"ok", "result", "error": {"message", "hint"}, "jmag_messages", "seconds"}`.
The tool's own description is authoritative for parameters; this file is for finding the tool.
Tools marked (new) were added with the skill family; read their description before first use.

## Session
- `jmag_status` - installation, worker and connection state, running JMAG instances, open dialogs. Call first.
- `jmag_connect(pid?, launch_new?, visible?)` - attach to a JMAG (by pid / last used) or start a detached new one.
- `jmag_capabilities(model?)` (new) - JMAG version, study types offered (magnetic, thermal, structural ...), API members present for cases/loss/maps/optimisation/winding/export, solver executables. No licence query exists: a listed type can still fail at Run.
- `jmag_dialogs(click?, title_filter?)` - list modal dialogs; `click="Cancel"` presses a button.
- `jmag_screenshot(out_dir?)` - images of every JMAG window, including hidden dialogs.
- `jmag_cleanup` - kill orphaned `modeller.exe` helpers of dead Designers; report solver processes.
- `jmag_quit(save?)` - close the connected Designer.

## Project
- `jmag_new_project(path, overwrite?)` - create and save a `.jproj` (required before DXF import).
- `jmag_open_project(path)` - open a project (work on a copy).
- `jmag_save_project(path?)` - save, or Save As with `path`.
- `jmag_summary` - models (dimension, parts) and studies (type, results).
- `jmag_inventory` (new) - project tree: models -> studies (type, solved, conditions, result tables, calc folder); flags unsolved studies. Use first in a project you did not build.

## Geometry
- `jmag_import_dxf(path, model_name?)` - 2D import; every closed loop becomes a part (area, centroid returned).
- `jmag_import_cad(path, model_name?, healing?, remove_sheet?, unit?)` - 3D STEP/IGES/Parasolid/SAT import.
- `jmag_list_parts(model?)` - id, name, area/volume, centroid.
- `jmag_rename_parts(renames)` - rename by name, id or an `[x, y]` point inside the part.
- `jmag_suppress_parts(parts, suppress?, study?)` - treat parts as absent (e.g. a DXF air-gap ring).
- `jmag_model_image(path?, show_mesh?)` - PNG of geometry or mesh.

## Design helpers (pure Python, no JMAG needed)
- `jmag_estimate(kind, params?)` (new) - hand calculation with value, formula, assumptions and expected FEA tolerance; `kind='list'` lists kinds (gap B, Carter, winding factor, EMF/torque constant, dq torque, inductances, gapped core, tooth/yoke B, slot fill, copper loss, electrical frequency, step plan, Steinmetz iron loss).
- `jmag_units(convert, value, from_unit, to_unit, poles?, base?)` (new) - speed (mech/electrical), length, flux density, angle (mech/electrical), area incl. AWG, per-unit.
- `jmag_winding_plan(slots, poles, phases?, layers?, coil_pitch?, turns?, parallel_paths?)` (new) - star-of-slots layout: balance check, slot map with part names, kw per harmonic, MMF harmonics, symmetry sector, `labels_coils` for FEM-coil sides.

## Study setup
- `jmag_create_study(study_type, name, model?)` - Static2D / Transient2D / Frequency2D (+AX), Static / Transient / Frequency (3D).
- `jmag_duplicate_study(study, new_name)` - copy settings without results (variants, operating points).
- `jmag_delete_study(study)` - delete a study and its results (ask first).
- `jmag_get_properties(study, kind?, name?, index?, names?, with_help?)` - list property names/types/values.
- `jmag_set_properties(study, values, kind?, name?, index?, strict?)` - set with read-back; unknown names get suggestions.
- `jmag_set_step(study, steps?, time_step?, end_time?, step_type?)` - transient step control.
- `jmag_set_equations(study, equations, descriptions?)` - study parameters/equations usable as property values.
- `jmag_get_equations(study)` - list equations with expression and value.

## Materials
- `jmag_find_materials(query?, limit?)` - search the material library.
- `jmag_create_material(name, bh, density?, conductivity?, loss?, overwrite?)` - custom soft-magnetic B-H material, verified.
- `jmag_set_material(study, parts, material, properties?, orientation?)` - assign material and magnet orientation.

## Excitation and conditions
- `jmag_add_current(study, name, parts, turns, current, direction?)` - static `Current` condition (no circuit).
- `jmag_add_fem_coil(study, name, component, sides, turns?, resistance?)` - FEM coil linked to a circuit Coil.
- `jmag_add_fem_conductor(study, name, component, conductors)` - solid conductors with eddy currents (bars, hairpins).
- `jmag_winding(study, coils, mode?, ...)` (new) - apply a `jmag_winding_plan` layout: `mode='fem_coil'` (verified) one FEM coil per phase; `mode='jmag_winding'` JMAG's winding definition (unverified).
- `jmag_add_condition(study, condition_type, name, parts?, properties?)` - RotationMotion, Torque, Force, boundaries ...
- `jmag_list_conditions(study)` - conditions with parts, links, sub-conditions, properties.
- `jmag_delete_condition(study, name)` - delete a condition.

## Circuit
- `jmag_circuit_quick(study, topology, coils, source, probe?)` - standard 1-phase / 3-phase source circuits, correctly wired.
- `jmag_circuit_add(study, components?, wires?)` - free-form circuit editing on JMAG's grid.
- `jmag_circuit_summary(study)` - components, properties, wire count, terminal offsets.
- `jmag_circuit_image(study, path?)` - render the circuit to check the wiring.

## Mesh
- `jmag_mesh(study)` - generate now; element/vertex counts; fails loudly on bad geometry.
- `jmag_mesh_size(study, parts, size, name?)` - local mesh size on parts.

## Run
- `jmag_check(study, stage?, electrical_frequency_hz?)` - pre-run lint or post-run health; PASS/CHECK/FAIL.
- `jmag_run(study, wait_seconds?, overwrite?, check_first?, force?, electrical_frequency_hz?)` - solve in a runner process.
- `jmag_run_status` - progress, ETA, convergence, dialogs; solver report and post-check when finished.
- `jmag_cancel_run` - safe cancel (Abort -> answer the prompt -> only then terminate the solver).
- `jmag_solver_report(study)` - solver errors/warnings, element count, calc folder.

## Standard tests
- `jmag_experiment(kind, study, params, action?)` (new) - `cogging` | `back_emf` | `load_point` | `inductance_static` on a duplicate study, read-back of every change, metrics plus the matching invariant; `action='analyze'` for long solves.

## Losses and efficiency
- `jmag_loss_study(action, study, ...)` (new, verified) - `create` an iron-loss study from a solved Transient study (parts, `frequency_hz`, reference steps) and run it; `results` = loss totals per part.
- `jmag_efficiency_map(action, study, ...)` (new, unverified) - EfficiencyMapDefinition settings (limits, control, loss options, temperatures) and results/exports/point queries.

## Parametric
- `jmag_case_table(study, action?, ...)` (new, verified) - design-table parameters and cases (0-based, case 0 = base), add/set/remove/activate, current case.
- `jmag_run_cases(study, cases?, method?, ...)` (new) - solve cases in the runner: `loop` (verified), `all_cases`, `optimization` (unverified); progress via `jmag_run_status`.
- `jmag_optimization_table(study, action?, ...)` (new, unverified) - variable ranges, objectives, constraints, CSV monitoring of JMAG's optimiser.

## Results
- `jmag_list_results(study, case?)` - result tables (name, unit, columns, rows); `case` = design-table case.
- `jmag_get_result(study, table, columns?, electrical_frequency_hz?, max_points?, case?)` - one table with stats, harmonics, periodicity.
- `jmag_export_results(study, out_dir, tables?)` - one CSV per table.
- `jmag_export_table(study, path, kind?, ...)` (new) - JMAG's own writers: all tables, all cases, one table, field tables (save first: a field-export sequence crashed Designer once).
- `jmag_case_report(study, path, case?)` (new) - JMAG's own case report (`WriteCaseReport`); save the project first.
- `jmag_contour_image(study, result_type?, component?, step?, path?)` - contour PNG with min/max.
- `jmag_physics_check(study, check, ...)` - `kvl` | `energy` | `power_balance` invariants.
- `jmag_compare(study_a, study_b, tables?, threshold_pct?)` (new) - setup differences that could explain a change, plus result statistics side by side with % deltas.
- `jmag_plot(kind, data | csv_path | study+table, ...)` (new) - `waveform` | `fft` | `sweep` | `map` | `torque_speed` PNG plus the plotted numbers in text (needs the `plots` extra / matplotlib).
- `jmag_make_report(title, studies, out_dir, ...)` (new) - Markdown folder (setup, every finding, convergence, statistics with sources, images, invariants you pass in, sweeps, doubts, replay script).

## Knowledge and escape hatch
- `jmag_docs_search(query, limit?)` - search the JMAG manual installed on this machine.
- `jmag_docs_page(page, max_chars?)` - full text of one manual page.
- `jmag_api_lookup(query, limit?)` - JMAG Python API methods from the installed `designer.py`.
- `jmag_exec_python(code, timeout_s?)` - run Python 3.8 in the worker with `app` in scope.

## Meta
- `jmag_self_test(keep_open?, work_dir?)` - known-answer C-core test of the toolchain (6 checks).
- `jmag_journal(export_script?, session_only?)` - log of state changes; exports a replayable script.
- `jmag_star_repo(action)` - answer to the one-time `first_run_note` (star | open | dismiss); only after the user answered.
