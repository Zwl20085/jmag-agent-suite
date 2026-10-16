# Symptoms in depth

Contents: 1 Hangs and dialogs - 2 Crashes and worker restarts - 3 Non-convergence -
4 Negative element volume - 5 Wrong torque - 6 Empty or missing results - 7 Import problems -
8 Licence - 9 Orphan processes - 10 A worked diagnosis

All behaviours below were observed on JMAG-Designer 23.0 unless marked otherwise
(`jmag-simulation/references/jmag-gotchas.md`).

## 1. Hangs and dialogs

- JMAG blocks on modal dialogs; the API call never returns, the bridge times out, restarts the
  worker and attaches the open dialogs to the error.
- Known triggers: Geometry Editor without a saved project ("save the project file"), licence
  prompts, overwrite confirmations, the "Stop calculation" prompt after Abort.
- Fix: `jmag_dialogs` -> read title and text -> press the safe button (Cancel/No) unless you
  know what it does -> `jmag_connect` -> repeat the failed step after removing the cause.
- During a run, `jmag_run_status` lists blocking dialogs separately from the progress window.

## 2. Crashes and worker restarts

- `JmagCrashed`: the Designer PID is gone. Unsaved work is lost. Reconnect with
  `launch_new=true`, reopen the last saved copy, `jmag_cleanup` for its helpers.
- Attaching to a dead PID "succeeds" in the raw API; the bridge checks the PID first.
- Repeated crashes on the same step: save before it, simplify (mesh size, fewer parts), and
  run `jmag_self_test` to separate model problems from installation problems.
- Worker timeout without a dialog: a genuinely long op (3D import, mesh). Retry with the tool's
  own longer timeout or check `jmag_status` before assuming a hang.

## 3. Non-convergence

- Read `convergence.steps_at_iteration_limit` (post-check). Spaced exactly half an electrical
  period apart = current zero-crossings (B ~ 0, relative tolerance artefact) = harmless.
- Clustered at peak load: raise `NonlinearMaxIteration` (e.g. 50), refine the mesh where B
  changes fast (teeth tips, bridges), shorten the time step, try the direct solver
  (`DirectSolverType`), check the B-H curve is monotonic and extends to >= 1e5 A/m.
- A custom material accidentally created as a magnet aborts with "Check the point array
  setting in material ..." - use `jmag_create_material` (verifies the soft-magnetic flag).

## 4. Negative element volume

- Moving parts meshed with the normal mesh, or a user part spanning rotor and stator so the
  slide surface cannot separate them.
- Fix: `MeshType 1`, `AutoSlide 1`, `AutoDivision 0` with a sensible `CircumferentialDivision`
  (one step = integer number of elements); suppress the gap part; nothing user-drawn crosses
  the gap mid-radius (`jmag-geometry`).

## 5. Wrong torque

Work through in this order (each step is cheap):
1. Stack length (`ModelThickness`): 2D torque scales linearly with it.
2. Torque condition parts = all rotor parts (core, magnets, pockets, shaft region).
3. Speed and rotation direction of RotationMotion; sign of the Torque axis.
4. Turns: FEM coil `Turn` is spread over the regions of a side (k regions -> Turn*I/k each);
   for k series tooth coils use Turn = k N.
5. Magnet orientation: B contour shows alternating poles with the intended direction.
6. Current angle: sweep the source phase; a pure sign flip is a convention, a magnitude far
   below `3/2 p psi I` at every angle is a winding/orientation error.
7. Power balance: `jmag_physics_check('power_balance')` - if torque and flux disagree, the
   torque condition or the motion is wrong; if they agree but disagree with the estimate,
   the model (turns, magnets, stack) differs from what you think.

## 6. Empty or missing results

- Raw `GetDataFromName` wants internal keys (`LineCurrent`, `TerminalVoltage`, `FEMCoilFlux`,
  `FEMCoilInductance`, `StoredEnergy`); display names return empty data silently.
- `Stored Energy` needs study property `Energy: 1` before the run.
- No `Torque` table without a Torque condition (NO_TORQUE_COND info).
- An aborted run keeps only the solved steps; a cancelled run has none.
- A 3-phase source's own current column is the phase sum (zero when balanced) - not a fault.

## 7. Import problems

- Every closed face becomes a part; open outlines produce no part or merge neighbours.
- Names after import are `<layer>/Region.N`; rename by inside points.
- Centroids of C/L/V shapes lie outside the part; use inside points.
- `Load` of a missing file crashes the client; the bridge validates paths first.
- Compare `jmag_import_dxf` areas with the script's expectations; mismatch = broken loop.

## 8. Licence

- Symptoms: a dialog about licences or modules, features that fail only on this machine, the
  solver refusing to start. `jmag_capabilities` lists what is available.
- Do not retry in a loop (licence servers count checkouts); report to the user with the dialog
  text.

## 9. Orphan processes

- Every Designer starts a Geometry Editor helper (`modeller.exe`) that stays behind when the
  Designer dies. `jmag_cleanup` kills only helpers whose parent Designer no longer exists and
  reports solver processes.
- Solver processes still writing after a cancel: the bridge terminates them after the
  "Stop calculation" prompt has been answered (never before).

## 10. A worked diagnosis (from the bridge's A/B test)

"IPM run aborted with negative element volume, torque -0.3 Nm":
1. Post-check: NEGATIVE_ELEMENT_VOLUME + NO_RESULT for the later steps.
2. `jmag_list_parts`: an unnamed region spans the air gap -> the DXF closed the gap -> name it,
   suppress it, slide mesh on.
3. Torque magnitude: `jmag_get_properties(kind='study')` shows `ModelThickness` = 1 -> set the
   real stack (the -0.3 Nm becomes about -36 Nm at 120 mm).
4. Sign: sweep the current angle; report the convention.
5. Prove: rerun, power balance < 1-5 %, torque vs `3/2 p (psi_m i_q + (L_d - L_q) i_d i_q)`.
