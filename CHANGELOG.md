# Changelog

## 0.4.0 (2026-11-08)

- New: fields outside JMAG (2D studies). `jmag_field_data` exports the field of one step as plain JSON
  (nodes in mm, triangles with quads split, part names and materials, per-element B/Bx/By/J, nodal Az, JMAG
  node/element ids) plus per-part max/min/area-weighted mean; `jmag_field_plot` renders it with the bridge's own
  plotting (`plotting.field` / `plotting.mesh`: flux lines, part outlines, `hide_air`, `parts`, shared colour
  scales) and re-plots a saved JSON without JMAG (`data_path=`); `jmag_field_probe` samples a circle or segment
  (air-gap Br/Bt with harmonics in electrical orders, THD and non-multiple orders) and draws the waveform.
  Data come from the study's own result file through JMAG's `JplotReader.dll` and the run's
  case-folder mesh (`Designer_input.jcf` / `Designer.jcf`, else `WriteMeshJcf`; worker op `field_export`, read-only; refused for 3D, axisymmetric and while a
  solve runs). Verified on the 12s/4p SPM: max |B| equals `jmag_contour_image` to all digits at steps 40 and
  129. New pure module `fielddata` (mesh.bin parser, assembly, rotor displacement, `part_stats`,
  `nodal_average`, `probe`, `circle_points`, `segment_points`), tests in `tests/test_fielddata.py`.
  `jmag_field_plot` hides the air regions by default (`hide_air=False` shows the full domain, both kinds) and
  shades elemental B smoothly (`smooth=True`: values averaged onto the nodes of each part for the picture only;
  statistics and the max marker stay elemental, annotations `values` / `shading`); `dpi=` for larger images.
  `jmag_field_data` summarises B and J per part by default (`per_part=[...]` for others, e.g. Az).
  `jmag_field_probe` draws the requested quantity only, with a harmonic-bar panel below it on a circle
  (`plotting.probe`).
  The default step is the last step stored in the result file and `available_steps` (count / first / last)
  is reported, because output-step control can store only some steps; a missing step is refused with that range.
  The mesh is checked against the result file (element ids, types and centres); when only the part ids differ (a
  `study.WriteMeshJcf` mesh numbers JMAG's air regions differently) they are taken from the result file and a
  `warnings` entry says so. An explicit `parts=` overrides `hide_air`, unknown part names are an error listing
  the valid ones. The field JSON is written once per export and only after a successful render. Probes locate
  the points once for all components. When the result file holds no element B (all runs after the first in a
  project, measured) B/Bx/By are derived as the curl of Az (equal to JMAG's element B to 1e-8 T).
- Requirement: the `plots` and `dev` extras now need matplotlib >= 3.8 (field plots call `set_path_effects` on
  a contour set, which 3.7 lacks); renderer errors come back as an envelope with that hint.
- New: `jmag_create_material(kind="magnet")` - linear magnet from `br` + `mu_r` with optional
  `temperature` / `br_temp_coeff` (Br(T) = Br20 (1 + a (T - 20)), applied value reported), or a nonlinear
  demagnetisation curve `bh`; validated by `materials.check_magnet` and read back. `kind="steel"` (default) is
  unchanged. Linear magnets verified on 23.0; the nonlinear table is read back but its solve is not verified.
- `jmag_plot` points field/mesh plots to `jmag_field_plot`.
- Plotting restyle (all server-side PNGs, `plotting.py`): one house style - white surface with a hairline
  border, recessive grid, fixed categorical palette (never cycled; series past the 8th are grey), frameless
  legends above the data, 160 dpi. `torque_speed` draws torque and power as two stacked panels instead of a
  dual y axis; `fft` labels the fundamental and the largest harmonics directly on the bars; `map` (heatmap)
  uses a one-hue sequential ramp with labelled contours and marks the best point; field plots use the
  perceptual `magma` map. `plotting.plot()` also takes kinds `field` and `mesh` (field-data dict).
- README figures in `docs/images/` re-rendered by the bridge itself: `spm_flux_density.png`, `spm_mesh.png`,
  `spm_airgap_br.png`, `spm_torque.png`, `ccore_flux_density.png`.

### Safety contract for agents (no silently wrong or unchecked answer)

- Envelope: every tool now answers `{ok, result | error, seconds}` (`seconds` is 0.0 when unknown). Wrapped in that
  envelope (shape change, keys moved under `result`): `jmag_status`, `jmag_journal`, `jmag_physics_check`,
  `jmag_self_test`, `jmag_star_repo`, `jmag_docs_search`, `jmag_docs_page`, `jmag_api_lookup`, `jmag_cleanup`.
  `jmag_status` without a JMAG install is `ok: false` (hint: `JMAG_HOME`, `jmag_doctor`), with the install facts in
  `result`; the docs tools return `ok: false` instead of raising when JMAG is not installed. `jmag_dialogs` (a list)
  and the image tools (`[json, Image]`) are unchanged.
- Tool ran vs physics passed: `ok` means the tool ran. `jmag_check` adds `result = {stage, passed, verdict, counts,
  hint}` (`passed` = no error finding; `hint` lists the fixes of the error findings, or of the warnings for CHECK);
  its old top-level keys stay. `jmag_physics_check` returns `result = {check, passed, verdict PASS|FAIL, ...numbers}`
  (the invariant's own `ok` is now `passed`); a failed invariant is `ok: true, passed: false` with `result.hint`
  naming likely causes (`server_support.INVARIANT_HINTS`). `jmag_self_test`: `result.passed` / `verdict`, `ok: false`
  only when it could not run. Unknown `jmag_check` stage is now an error.
- `jmag_experiment` no longer swallows failures: a refused pre-check (`result.precheck`), a failed run or
  `solver_aborted` returns `ok: false` with the run details and a hint naming the leftover duplicated study
  (`<study>_<kind>`) and `jmag_delete_study`; `next` ("poll jmag_run_status") only when `state == 'running'`. A copy
  name that already exists fails with "pass new_study=..., jmag_delete_study(...), or analyze it with
  duplicate=false". Docstring: `poles` = 2p, rpm defaults (cogging 60, back_emf / load_point 1000), duplicate names.
  New `experiments.ExperimentFailed`, `experiments.dq_to_abc`.
- Health (`health.py`): transient pre-checks without `electrical_frequency_hz` now say so (info
  `STEP_CHECKS_SKIPPED`, fix "pass electrical_frequency_hz=<poles/2 * rpm/60>") unless the frequency can be derived
  from the facts already gathered (no new JMAG probe): RotationMotion speed + a `poles`/`p` study equation, or the
  speed + a circuit source `Frequency` whose implied pole count is an even integer (info `FE_DERIVED` names the
  source; `health.derive_electrical_frequency`). The post-run check derives it the same way (so `NOT_STEADY` runs).
  New post-run warning `TORQUE_ZERO` (torque table identically zero, or flat to 1e-9 relative with >= 2 rows, while a
  Torque condition exists). `NOT_STEADY` is now a warning for torque and flux-linkage tables (info for currents).
- Unverified results are visible: the bridge remembers per study (in memory, cleared on project open/new, connect,
  quit; reset when the study is solved again) whether `jmag_check(stage='post')` (or a finished `jmag_run_status`,
  which runs the same check) and `jmag_physics_check` ran on its current results. `jmag_get_result`,
  `jmag_export_results`, `jmag_plot` (study source) and `jmag_experiment` add `result.unverified` (`[]` when both
  ran; reads of another design-table case are never covered). `jmag_make_report` uses the recorded physics checks
  when `invariants` omits a study and lists every missing check as an open doubt; invariant rows read `passed`.
- `jmag_physics_check` defaults: omitted `speed_rpm` (RotationMotion), `electrical_frequency_hz` (as above), and for
  `kvl` `coil` (the only circuit Coil), `probe` (`VP_<coil>` or the only VoltageProbe) and `resistance` (the Coil's
  Resistance) are taken from the study; explicit arguments win; `result.defaults_used` says what was derived.
  `jmag_physics_check(study, 'power_balance')` is one call for a standard 3-phase PMSM study.
- Run contract in the server instructions (state running|finished|failed with `ok` and `solver_aborted`, poll
  `jmag_run_status`, cancel only with `jmag_cancel_run`, never press Cancel on 'Run Analysis', keep `wait_seconds`
  below the client's tool timeout, `result.passed` / `result.unverified`). `jmag_dialogs` documents that progress
  windows are left alone and recommends `title_filter`. `BLOCKING_TOOLS` notes with the maximum blocking time on
  `jmag_mesh` (3600 s), `jmag_import_cad` (1800 s), `jmag_loss_study` run (3600 s), `jmag_experiment` (wait_seconds,
  600 s), `jmag_exec_python` (timeout_s, 600 s), `jmag_self_test` (~120 s), `jmag_docs_search` (first call ~60 s).
  `jmag_mesh` / `jmag_import_cad` got no `wait_seconds` background mode: meshing or importing from a second
  (runner) connection is not verified on a live JMAG, so their limits are only documented.
- Runs the bridge does not own (server restart, GUI-started solve): `jmag_status.result` adds `solver_processes`
  (ExecSolver/jam pids), `newest_monitor_age_s` and `stalled` (age > 600 s while a solver runs; calc folder from the
  job or the ExecSolver command line). `jmag_run_status` without a job returns those fields, `owned: false`, and
  `state: running` (ok) when a solver or a 'Run Analysis' window exists, else `state: none` (ok false).
  `jmag_cancel_run` without a job but with a 'Run Analysis' window runs the verified Abort -> 'Stop calculation' ->
  kill order (factored into `_abort_protocol`, order unchanged) and kills the solver tree only when exactly one calc
  folder is found - unverified live. `solver_monitor`: `SOLVER_EXES`, `folder_from_command`, `parse_process_lines`,
  `solver_processes(names=...)` (default unchanged).
- Worker hints (`worker/ops.py`, Python 3.8): `OpError` without a hint gets "see jmag_messages; the tool docstring
  lists the preconditions"; the `JmagError` hint says `jmag_messages`; `BadArguments` points to the jmag_* tool's
  description; 'has no results' -> "jmag_run then jmag_check(stage='post')"; 'study has no circuit' ->
  "jmag_circuit_quick(...) first"; "condition 'X' not found" lists the existing condition names in the message;
  "study 'X' already exists" -> "pass another name or jmag_delete_study"; Geometry Editor start failure ->
  "jmag_dialogs; a modal dialog may be open"; read-back mismatches -> "jmag_get_properties to see what JMAG kept".
  `duplicate_study` now refuses an existing target name (checked by listing, no JMAG error probe).

### New

- `jmag_dxf_inspect(path)` (read-only, no JMAG): reads LINE / ARC / CIRCLE / LWPOLYLINE / POLYLINE (bulges), splits
  edges at T-junctions, merges duplicate edges and returns the regions JMAG's import will create (area mm^2,
  centroid, a point strictly inside, r_min / r_max, layer, holes), role guesses with confidence (stator / rotor /
  slot / magnet / airgap / shaft for machines about the origin, core / coil otherwise; layer names override) and a
  draft labels JSON in the jmag-geometry labels schema. New module `dxf_inspect`, `dxf.inspect()`. Round-trip tests
  with the SPM example and the self-test C-core plus a hand-written fixture (`tests/fixtures/hand_minimal.dxf`).
- `jmag_estimate` kinds `dq_to_phase` (`id`, `iq`, `theta_e_deg` -> U/V/W currents and a `jmag_circuit_quick`
  source; the PhaseU convention `I sin(2 pi f t + PhaseU)` is unverified live) and `phase_to_dq` (`abc`,
  `theta_e_deg` -> d, q), the same amplitude-invariant Park transform as `jmag_experiment`.
- `jmag_get_result(n_harmonics=10)`: `harmonic_amplitudes` holds orders 0 .. n-1 (default 0-9, max 64).

### Review fixes

- `jmag_cancel_run` after the bridge's own run finished answers "nothing is running" again; the no-job path acts
  only on the connected JMAG (its pid filters the windows: `dialogs.ps1 -OwnerPid`, `winui.dialogs/click(pid=)`)
  and kills a solver only when exactly one calc folder lies in that instance's project (`<project>.jfiles`,
  `server_support.folder_in_project`). Bridge jobs remember the JMAG pid.
- `jmag_make_report` reads `passed` inside a whole `jmag_physics_check` envelope (`ok` there only means it ran).
- `jmag_run_cases` invalidates the verification record; the record is keyed by (model, study).
- `jmag_dxf_inspect`: entities with extrusion (0,0,-1) are mirrored (x -> -x, arcs stay CCW, bulges negated), other
  non-Z extrusions are skipped and listed; `units` comes from `$INSUNITS` (mm | in | unknown, with a warning).
- `solver_monitor.folder_from_command` accepts only absolute `.jcf` paths (also unquoted with spaces) and
  `parse_process_lines(folder=)` matches the parsed calc folder as a path prefix, not a substring.
- `jmag_get_result`: `thd_pct` is over orders 2..15 whatever `n_harmonics` is.
- Default hints for worker exceptions and for `WorkerError` in the bridge; `experiments._ok` no longer prints
  "hint: None".
- f_e derivation accepts a pole equation only as an even integer 2..120 and a pole-pair equation only as an
  integer 1..60 (an equation `P` = 1000 W is ignored); the post-run check derives f_e for Transient studies only.

### Plumbing

- `.claude-plugin/plugin.json` version 0.4.0; `tests/test_versions.py` asserts pyproject == `__version__` ==
  plugin.json == CHANGELOG top version and that README / README.zh-CN list the same, complete tool set (79 tools).
- New pure module `server_support.py` (envelope `wrap`, `VerifyRecord`, verdicts, physics-check defaults,
  `solver_status`, dq kinds). Tests: `test_server_support.py`, `test_server_contract.py`, `test_dxf_inspect.py`,
  `test_versions.py`, additions to `test_health.py`, `test_experiments.py`, `test_ops_fake.py`.
- Skills and docs follow the API: finding codes (`STEP_CHECKS_SKIPPED`, `FE_DERIVED`, `TORQUE_ZERO`, `NOT_STEADY`
  level), the run contract, the new envelope shapes, `jmag_dxf_inspect` in the tool map; the 3D Static reference is
  split into `static-3d-basics.md`, `static-3d-rotating.md`, `static-3d-nonlinear.md` (`static-3d.md` is the index;
  section numbers unchanged).

## 0.3.6

- Fix: the server process now ends within about a second of its MCP client closing stdio, also with a tool call in
  flight. Before, a thread still inside `Worker.call` (any JMAG op, up to its timeout of 600 s by default; a
  `jmag_run` / `jmag_run_cases` solve waits up to 30 days) kept `python -m jmag_bridge` alive after the session ended,
  because asyncio joins its default executor on the way out; the MCP Python client then killed the whole process
  tree after its 2 s grace, JMAG worker included, and clients without such a kill waited for ever. On stdin EOF the
  server now releases every JMAG worker (blocked calls return at once, each worker's stdin is closed so it exits
  after its current op; nothing is killed, JMAG-Designer is never touched) and a daemon timer hard-exits the process
  if anything still blocks after `SHUTDOWN_GRACE_S` (5 s). New `Worker.abandon()` / `worker_client.abandon_all()`.
  Tests: `tests/test_stdio_shutdown.py` (real server over pipes with a stand-in JMAG Python; both cases failed before).
- Note for client scripts: leaving an MCP stdio session with an exception in flight can also hang on the *client*
  side when the session and transport context managers are exited by hand (`await session.__aexit__(...)` raises the
  in-flight exception as an `ExceptionGroup`, so a following `await stdio_client_cm.__aexit__(...)` never runs, the
  server's stdin is never closed and asyncio's shutdown waits on the transport's stdout drain). Use nested
  `async with` or a `contextlib.AsyncExitStack`.
- `jmag_run` / `jmag_run_status` (and `jmag_run_cases`) now carry the envelope's top-level `ok`: true while running
  and on a successful finish, false when the run failed. Before, the key was missing, so a client reading
  `out.get("ok", True)` took a failed solve for a success. New field `solver_aborted` (finished `jmag_run`): true when
  the run errors, the JMAG messages logged during the run or the Error entries of the solver's Designer.log say the
  solver aborted ("The solver module jam was aborted", "jam.exe failed to execute", "fail to execute the solver",
  "Size of point array is zero"); then `ok` is false with an `error` (message + hint) even if the study reports
  results. `state` keeps its meaning (`finished` iff the run op returned ok). Patterns: `health.SOLVER_ABORT_PATTERNS`,
  `health.solver_abort_messages()`, unit-tested on messages recorded from JMAG-Designer 23.0. Live check pending for
  the "results present but aborted" case (not observed yet; both recorded aborts had no results).

## 0.3.5

- 3D Static at arbitrary (off-grid) rotor angles verified (JMAG-Designer 23.0, 2026-10-20) on the 12-slot / 10-pole
  axial-flux PM machine of 0.3.2 (linear steel, slit coils with leads, full 360 deg, 621-622 k elements): no load at
  0.7 / 1.3 / 2.9 deg and I_q = 5 A at 0.7 deg, one STEP per angle, against an independent 3D FE solver with a
  mortar / interpolation sliding interface: psi 0.6 % (the on-grid offset), load torque 0.3-0.7 %, cogging within
  0.019 N m (5.4 % of the 0.357 N m peak-to-peak; that solver pair scattered by up to 0.028 N m on the 1 deg grid).
  JMAG's own consistency: the off-grid points lie on the periodic cubic through its on-grid points to 0.007 N m
  (2 % of its peak-to-peak) and 1e-4 in psi, the measured noise of per-angle remeshing. 77-80 s per no-load case.
  New `static-3d-rotating.md` section 8.9; `jmag-experiments` PMSM section 2b and README note. No code changes.

## 0.3.4

- 2D cogging mesh convergence (JMAG-Designer 23.0, 2026-10-20) on the 12-slot / 10-pole radial SPM cross-section of
  0.3.3, gap / magnet element size 0.5 .. 0.03125 mm and steel / slot-air / coil size 1.5 .. 0.125 mm (19 k .. 3.1 M
  elements): refining the gap alone converged the cogging to 1.34 N m/m at a 1.5 mm steel size, refining the
  slot-opening corners too took it to 1.15 N m/m; against an independent solver at the finest matched mesh 5 %
  (1.152 vs 1.094 N m/m). `jmag-experiments` PMSM section 2c rewritten as mesh guidance (refine the tooth-tip
  corners with the gap, two angles for the expensive levels, cost per level).
- Deep-saturation nonlinear 3D Static verified (2026-10-20) on an axial-flux PM variant (yoke 4 mm, tooth arc ratio
  0.25, 8 mm / 1.4 T magnets, 100 A): median tooth |B| 1.84 T, 99th percentile 2.89 T (beyond the 2.2 T table end);
  psi 0.2-0.5 %, load torque 0.4-1.0 % vs the independent solver, torque at 100 A 0.69-0.71 of the linear-steel
  value in both tools; 10-12 nonlinear iterations (default limit 15 would have sufficed), 245-300 s solve at 832 k
  elements. `static-3d-nonlinear.md` section 9.7.
- 3D Static of a radial-flux SPM with straight end windings verified (2026-10-20): full machine from STEP, arc
  magnets with `SetPattern("Radial")` + `SetOriginXYZ` + `SetAxisXYZ(0, 0, 1)` + `SetOrientation`, one InflowFace
  `Current` per straight coil side with both end faces on `SymmetryBoundary` end planes, `SymmetryBoundary` on all
  62 boundary faces, 721 k elements, 27 s mesh + 43 s solve: psi 0.1 %, load torque 0.2-0.6 % vs the independent
  solver; nodal torque at zero-cogging positions scattered by 1.7 % of the load torque. New `static-3d-nonlinear.md` section
  10; the magnet material's axis / origin method names recorded.
- README coverage (2D cogging convergence, deep saturation, radial 3D Static with end windings);
  `docs/jmag-facts.md` 2026-10-20 M11 section. No code changes.

## 0.3.3

- Nonlinear 3D Static verified on JMAG-Designer 23.0 (2026-10-20) on the 12-slot / 10-pole axial-flux PM machine of
  0.3.2 with M270-35A B-H steel, against an independent 3D FE solver with the same table: flux linkage 0.2-0.6 %,
  load torque 0.6 % at 5 A and 15 A, cogging peak-to-peak 0.03 %. New section 9 "Nonlinear 3D Static" in
  `jmag-simulation/references/static-3d-nonlinear.md` (section 9): user B-H steel through `create_material` (23 rows read back equal,
  `IsMagnet` False), `NonlinearMaxIteration` / `NonlinearTolerance` defaults 15 / 1e-3 (1e-5 changed psi by 4e-8),
  `IccgTolerance` / `IccgMaxIteration` defaults, iterations from `jcgmntr` (8-9), cost per case, the over-B-H-curve
  extrapolation warning.
- Sector torque: a loaded 180 deg `RotationPeriodicBoundary` `Reverse` sector reports the rotor `Torque` as the
  full-model value (1.863 vs 1.882 N m, factor 1), with `FullModelConversion` 1 / `ConversionFactor` 1 read and left
  at their defaults (non-default values unverified); the stator condition has the opposite sign. `static-3d-basics.md`
  section 7 and `static-3d-rotating.md` sections 8.2, 8.5, 8.8 updated.
- 2D Static radial SPM route verified (2026-10-20): one rotated full DXF per rotor angle, `add_current` per coil side,
  Radial magnets, `SymmetryBoundary` on the outer and bore circles, `AirRegion` 0 in the 2D mesh control, nodal
  `Torque`; psi 0.03 %, load torque 0.3 % vs an independent solver, 5 s mesh + 2 s solve at 20 k elements. Cogging
  moved 30 % between 20 k and 101 k elements (psi 0.3 %). New `jmag-experiments` PMSM section 2c.
- `jmag-scripting` snippets S-3D-10 (user B-H read-back), S-3D-11 (nonlinear study properties), S-3D-12 (`jcgmntr`
  iteration count), all VERIFIED; S-3D-6 note updated (sector torque factor 1).
- Docs: `docs/jmag-facts.md` 2026-10-20 nonlinear / 2D section; gotchas (nonlinear defaults, B-H extrapolation
  warning, sector torque, 2D cogging mesh sensitivity, symmetry-enclosed region warning, duplicate label names);
  `jmag-geometry` unique label names per angle; result keys (`Torque` in 2D Static, sector factor 1, `CoilFlux` in
  2D Static); study property defaults; README coverage (3D Static nonlinear PM machine, 2D radial SPM). No code
  changes.

## 0.3.2

- 3D Static verified for a rotating PM machine on JMAG-Designer 23.0 (2026-10-20): a 12-slot / 10-pole axial-flux PM
  machine, full 360 deg model in 13 rotor-position cases plus a 180 deg periodic sector, against an independent 3D FE
  solver: flux linkage 0.2-0.6 %, load torque 0.3 %, cogging peak-to-peak 0.3 %. New section 8 "Rotating machines in
  3D Static" in `jmag-simulation/references/static-3d-rotating.md`: the 29 condition types (no `RotationMotion`) and their
  property names, `SymmetryBoundary` (no properties, always flux parallel; no condition = natural),
  `RotationPeriodicBoundary` `Reverse` = anti-periodic on one cut plane with sector results already converted to the
  full model, custom Linear(Br) magnets and per-part direction, slit tooth coils with leads to the outer wall, nodal
  `Torque` (verified) vs `Torque_Surface` (does not work: run aborts on parts, internal face cancels), one STEP per
  rotor angle with timings, mesh-noise in per-angle cogging, `StoredEnergy` not comparable across tools with magnets.
- `jmag-scripting` snippets S-3D-6 to S-3D-9 (periodic sector, linear magnet + direction, nodal torque + `Torque`
  table, condition-type dump), all VERIFIED; S-3D-2 / S-3D-3 notes updated (lead faces on the outer wall,
  `SymmetryBoundary` has no type property).
- Docs: `docs/jmag-facts.md` 2026-10-20 section; gotchas; `jmag-experiments` PMSM section 2b (3D Static per-angle
  cases); `jmag-postprocess` result keys (`Torque` in 3D Static, sector `CoilFlux` factor 1); README coverage row for
  3D Static now verified for an SPM machine. No code changes.

## 0.3.1

- 3D Static verified on JMAG-Designer 23.0 (2026-10-19): two linear transformers (E55/28/21 with a 1 mm gap, P66/56
  pot core) driven end to end through the worker, L within 0.5 % / 0.85 % of an independent 3D FE solver and the pot
  core's L_m 10.37 mH vs 10.42 mH axisymmetric. New reference `jmag-simulation/references/static-3d.md` (now `static-3d-basics.md`): the step list
  with the exact API calls (each tagged VERIFIED / UNVERIFIED), the coil-excitation decision table (closed winding ->
  half model + `Current` `DirectionType` InflowFace; Vector mode gives zero current on a closed loop, silently;
  InflowFace needs the outflow face on a symmetry / periodic boundary), result keys `CoilFlux` / `CoilCurrent` /
  `StoredEnergy`, traps and the cross-validation numbers.
- `jmag-scripting` snippets S-3D-1 to S-3D-5 (STEP import + rename by point, 3D `Current` with inflow face,
  `SymmetryBoundary` by face position, 3D mesh controls + energy table, coil flux / energy read), all VERIFIED; S11/S12
  (B probe, section graph) stay UNVERIFIED.
- `jmag_bridge.cad3d` (pure Python, no JMAG): `read_step_names(path)` / `python -m jmag_bridge.cad3d model.step` lists a
  STEP file's products, the expected JMAG part names `<assembly>/<product>`, solids and sheets per product, and warns
  about unnamed products (gmsh's STEP writer), sheet-only products (free faces of a 360 deg revolve), duplicates and
  multi-solid products; `compare_names()` against the labels JSON. Not an MCP tool.
- Docs: `docs/jmag-facts.md` 3D Static section; gotchas, transformer (new section 6), actuator (3D InflowFace rule),
  geometry and router skills point to the 3D workflow; `jmag_add_current` documented as 2D only. README coverage table
  split into 2D / 3D Static rows, transformer cells say what was actually verified (2D inductor, 3D transformer L
  matrix), and a row for the unverified 3D transient / winding / field export.

## 0.3.0

- 3 new MCP tools (72 -> 75): `jmag_doctor`, `jmag_guide`, `jmag_star_repo`.
- Install diagnostics: `python -m jmag_bridge --doctor [--json]` (exit 1 if any check FAILs) and the read-only
  tool `jmag_doctor` run the same stdlib-only checks without touching JMAG: host Python, `mcp` package, JMAG
  install (`JMAG_HOME`), `designer.exe`, JMAG's Python (`JMAG_PYTHON`) and whether it runs, PyJmag import under it,
  state dir writable, matplotlib, claude CLI, `claude mcp list` registration (CLI only), installed skills up to
  date, running Designer PIDs. Every non-PASS row carries a `fix`. `--version` prints the package version.
- Start-up failures (import or `mcp.run`) are appended with their traceback to `<state dir>/server-startup.log`
  (fallback `%TEMP%`) with a one-line pointer on stderr, since Claude Code hides a failing server's output.
  The `jmag-mcp` console script (used by the plugin's `uvx` launch) now goes through `jmag_bridge.__main__`, so
  import-time failures are logged there too.
- `scripts/install.ps1` hardened: finds Python >= 3.10 (`py -3.14` .. `-3.10`, then `python`/`python3`), rejects
  the Microsoft Store stub and Store Python, `-Python <exe>` to choose; rejects a `.venv` that no longer runs or is
  older than 3.10; checks for the `claude` CLI unless `-NoMcp`; validates `-JmagHome` (needs `designer.exe`, warns
  without `python3.8`) and passes it to the MCP registration and to the final doctor run; installs `.[plots]`;
  checks the exit code of venv, pip and `claude mcp add`; `-Uninstall` removes the registration and the skills;
  ends with a doctor run and exits 1 if it reports FAIL.
- The skills ship inside the wheel (`jmag_bridge/skills`) and the sdist; a checkout still reads `.claude/skills`
  directly. `jmag_guide(topic?, reference?)` and the resources `jmag://guide`, `jmag://guide/{topic}`,
  `jmag://guide/{topic}/{reference}` serve them to clients that do not load Claude skills; the server instructions
  tell such clients to call `jmag_guide` first.
- One-time, opt-in offer to star the repository: `jmag_capabilities` and `jmag_self_test` carry a
  `first_run_note` until the user answers; `jmag_star_repo(action)` (`star` via `gh api`, falling back to opening
  the page in the browser; `open`; `dismiss`). The answer is recorded in `state.json` (`star_prompt`).
- `jmag_bridge.__version__` matches pyproject again (it still said 0.1.0 in 0.2.0).
- Docs: `docs/quickstart.md` (first ten minutes), `docs/install-troubleshooting.md` (one section per symptom),
  `docs/other-clients.md` (Cursor, Codex, Claude Desktop, VS Code, Windsurf), Chinese README `README.zh-CN.md`.
  `tests/test_tool_names.py` now also checks `docs/*.md` and `README.zh-CN.md`.

## 0.2.0

- Renamed to `jmag-agent-suite` (console script `jmag-mcp` and package `jmag_bridge` unchanged).
- Claude Code plugin and marketplace packaging: `/plugin marketplace add Zwl20085/jmag-agent-suite`,
  then `/plugin install jmag@jmag-agent-suite`.
- Health checks: `ZERO_CURRENT` is a warning in pre- and postflight (error only if flux linkage is also
  zero); postflight honours the known-message classifier levels; limits exposed as `health.THRESHOLDS`.
- CI runs the worker protocol tests on Python 3.8.
- 17 new MCP tools (55 -> 72):
  - analytic layer, no JMAG needed: `jmag_estimate` (15 hand-calc kinds), `jmag_units`, `jmag_winding_plan`
  - project and results: `jmag_inventory`, `jmag_compare`, `jmag_plot` (matplotlib, `plots` extra),
    `jmag_make_report`, `jmag_export_table`, `jmag_case_report`; `case=` on `jmag_list_results` / `jmag_get_result`
  - JMAG API: `jmag_capabilities`, `jmag_case_table`, `jmag_run_cases`, `jmag_loss_study`,
    `jmag_efficiency_map` (unverified), `jmag_winding`, `jmag_optimization_table` (set unverified)
  - `jmag_experiment`: cogging, back-EMF, load point, static inductance / Ld-Lq as one call
  - `jmag_report` renamed `jmag_solver_report`
- Skills: 10-skill family (`jmag` router, simulation, geometry, machine-design, experiments,
  losses-efficiency, parametric-optimisation, postprocess, scripting, troubleshooting).
- Live-verified on JMAG-Designer 23.0: case runs, iron-loss study, back-EMF, cogging, Ld/Lq, load point.
  Calls that crashed Designer (JMAG winding definition, field value exports) are refused unless `force=true`.
- `tests/test_tool_names.py` fails the build if README or a skill names a tool that does not exist.
- New hero banner (`docs/hero/make_hero.py` generates `docs/images/hero.svg`); JMAG facts moved to `docs/jmag-facts.md`.

## 0.1.0

- Initial MCP server and Claude skills for JMAG-Designer with engineering feedback (setup lint,
  solver monitoring, physics invariants).
- Custom B-H material creation (`jmag_create_material`), connect/model image fixes.
- DC-dominant result tables in `jmag_get_result`; cleaner contour images.
