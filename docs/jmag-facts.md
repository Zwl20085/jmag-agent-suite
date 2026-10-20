# Hard-won JMAG facts

Things measured while building this toolkit on JMAG-Designer 23.0. Each one cost a hang, a crash or a wrong
number; the bridge encodes all of them. Add to this list only what you observed on a real JMAG, and say which
version.

## Process and API

- `PyJmag.dll` is only safe under CPython 3.8; on 3.11 every void call is an access violation. The worker therefore
  runs on JMAG's own bundled Python.
- Errors are logged, not raised, even with `SetUseExceptions(True)`. Probing calls (`GetCondition("x")`,
  `GetLink()` on a non-circuit condition, `GetValue` on Integer/Vector/Table properties) log errors too.
- Attaching to a dead PID "works"; then every call returns `None`.
- `designer.exe` and PyJmag print to stdout, and a child Designer inherits pipes. The worker isolates the JSON
  protocol with a magic line prefix and redirects fd 0/1/2.
- A Designer launched inside an agent session dies with the session; it is launched detached via WMI instead.
- Dead Designers leave `modeller.exe` helpers behind (`jmag_cleanup`).
- There is no licence query in the API. `jmag_capabilities` lists the study types and API members that exist;
  a listed type can still fail at Run when the module is not licensed.

## Geometry and model

- The Geometry Editor needs a saved project, otherwise a modal dialog blocks forever.
- Every closed loop in a DXF becomes a part, including air. A user-drawn part bridging rotor and stator breaks the
  slide mesh; suppress it.
- 2D `ModelThickness` defaults to 1 mm. Torque, flux and EMF scale with it.

## Studies, circuits, results

- Static studies accept no circuit (use the `Current` condition). FEM coils need Transient/Frequency studies.
- Circuit terminal offsets were calibrated from exported circuit images (`ops.TERMINALS`). Ground components are
  always named "Ground".
- `GetDataFromName` wants internal keys (`LineCurrent`, `TerminalVoltage`, `FEMCoilFlux`); display names return
  empty tables.
- Rotating machines: slide mesh plus no user part bridging rotor and stator. JMAG may rewrite step control for
  motion; post-processing must use the returned time axis.
- Peak |B| at a re-entrant corner is a mesh-dependent singularity; judge saturation by area-averaged B.
- Newton non-convergence exactly at current zero-crossings is usually harmless.
- An iron-loss study created from a transient study needs `frequency_hz`; otherwise JMAG reports "Frequency is 0 [Hz]".
- Design-table cases are 0-based and case 0 is the base case.

## Running and cancelling

- `CancelProcess` blocks from a second connection. The working order is: press Abort, answer the "Stop
  calculation" dialog, only then terminate the ExecSolver/jam tree, then release the runner. Killing the solver
  before answering the dialog crashed Designer.
- The solver may keep writing result files for minutes after a cancel.
- A field-table export followed by a case report crashed Designer once. Save the project before either.

## 3D Static (measured 2026-10-19, JMAG-Designer 23.0.01zv)

Two linear transformers (E55/28/21 with a 1 mm gap; P66/56 pot core) driven end to end through the worker.
Workflow and snippets: `.claude/skills/jmag-simulation/references/static-3d.md`.

- `LoadCadDataWithOption(step, name, True, True, "mm")` on a named Open CASCADE STEP assembly gives parts named
  `<assembly>/<product>` (`E_core_pair/Core_Top`); volumes match the CAD to < 2e-5. gmsh's own STEP writer drops
  the names (products become `Open CASCADE STEP translator 7.8 n`).
- `GetPartByPosition(CreatePoint(x, y, z))` + `SetName` renames 3D parts by an inside point.
- A 3D `Current` condition with `DirectionType = 1` (Vector) on a closed winding runs without any error and
  carries no current (flux linkage ~2e-20 Wb).
- `DirectionType = 0` (InflowFace) with the inflow face picked by `GetSecondarySelection().SelectFaceByPosition`
  works when the winding's end faces lie on a `SymmetryBoundary`; the solver finds the outflow face itself.
  With the ends inside the air, Run fails: "Cannot find flow-out face of FEM coil (or Current condition). Case 1:
  flow-out face is not set on Symmetry boundary (3D) ...". Hence: half model cut through the winding axis.
- `SymmetryBoundary` at its default type acted flux-parallel (k agreed with an `A_t = 0` solver to 1e-4).
- `SelectFaceByPosition` on a face shared by two solids selects both.
- Mesh control `AirRegion 0` uses the STEP's own air solid; `AutoMeshSize 0` + `MeshSize` + `Part` size conditions
  set the mesh. `Energy = 1` gives `StoredEnergy` with one column per part plus `Total`.
- Internal result keys of the `Current` condition: `CoilFlux` (turns included, model value) and `CoilCurrent`;
  a 0 A condition still reports its flux. Energy identity and `M12 = M21` held to < 1.3e-10.
- `GetUserMaterial` is invalid for library materials; `GetValue` on a Complex material property logs an error.
  A custom steel with a linear B-H table reproduced the library `linear_2000` to 1e-11.
- Accuracy: L within 0.5 % (E core) and 0.85 % (pot core) of an independent 3D FE solver; pot core
  L_m 10.37 mH vs an axisymmetric FE reference of 10.42 mH. JMAG needed ~3x the elements for the same value.
- `quit_app` left no `designer.exe` / `modeller.exe` behind; no modal dialog or crash in any 3D run.

## 3D Static, rotating PM machine (measured 2026-10-20, JMAG-Designer 23.0.01zv)

Axial-flux PM machine, 12 slots / 10 poles, linear materials, full 360 deg model (622 k elements) in 13 rotor
positions plus a 180 deg periodic sector. Details: `.claude/skills/jmag-simulation/references/static-3d.md` section 8.

- `GetConditionTypes()` of a 3D Static study lists 29 types, among them `RotationPeriodicBoundary`,
  `TranslationPeriodicBoundary`, `Torque`, `Torque_Surface`, `Current`, `CurrentDensity`, `VectorPotentialBoundary`;
  there is no `RotationMotion`, so each rotor position is its own case (one merged STEP per angle, re-imported).
- `SymmetryBoundary` has no properties and is always flux-parallel. A face with no condition is natural (flux normal);
  to reproduce an `A_t = 0` outer box put `SymmetryBoundary` on every outer face.
- `RotationPeriodicBoundary` (`Axis`, `Origin`, `Angle`, `AutoPeriodicFromWinding`, `BoundaryType` with keys
  `Normal` / `Reverse` / `AxialReverse`): `BoundaryType` 1 = Reverse = anti-periodic, set on the faces of one cut
  plane only. A 180 deg sector (6 slots / 5 poles) agreed with the full model to 0.03-0.06 % in flux linkage.
  The sector's `CoilFlux` and `StoredEnergy` are reported already converted to the full model (factor 1, unlike the
  x2 of the 2026-10-19 half model with `SymmetryBoundary`). `FullModelConversion` / `ConversionFactor` untouched.
- Custom linear magnet: `MagneticPropertyType` 1, `MagnetPermeabilityType` 6 (Linear(Br)),
  `ResidualMagneticFluxDensity`, `MagnetPermeability` read back fine; per part `SetPattern("Parallel")` +
  `SetDirectionXYZ(0, 0, +-1)` + `GetDirection()`. Library `NdFeB_Br=1.2(T)` = custom B_r 1.2 T / mu_r 1.05 to 1e-8;
  reversing every magnet negates psi exactly.
- Closed tooth coils: slit each coil and run two leads to the outer wall; `Current` InflowFace with the lead end faces
  on outer-wall `SymmetryBoundary` faces works. Negative `Current` and `UniformCurrent` 0 / 1 both work (0 vs 1:
  0.3 %).
- Torque: nodal `Torque` (`Origin` / `Axis`, `ExpandTarget` without effect) on the rotor parts, result key `Torque`.
  `Torque_Surface` rejects part selections and the run aborts ("The target is not specified for the electromagnetic
  force or torque calculation condition"); a mid-gap face selected by position picks both sides and cancels
  (~1e-14 N m).
- `GetValue` of a property a condition lacks (`TargetType` on `Torque`) logs an error and fails the worker call.
- Timing (Ryzen 7 3800X, ~620 k elements): ~80 s per no-load case, ~160 s with a load case. Cogging from per-angle
  meshes carries +-0.035 N m mesh noise (the identical positions 0 and 6 deg gave -0.005 / +0.006 N m).
- `StoredEnergy` with magnets is not 1/2 integral(nu B^2) (JMAG 3.82 J vs 9.27 J from another solver): do not compare
  energies across tools when magnets are present.
- Accuracy vs an independent 3D FE solver: flux linkage 0.2-0.6 %, load torque 0.3 %, cogging peak-to-peak 0.3 %.

## Nonlinear 3D Static, sector torque, 2D radial SPM (measured 2026-10-20, JMAG-Designer 23.0.01zv)

Same axial-flux PM machine as above with nonlinear steel (M270-35A, 22 points 0.1-2.2 T), and a radial-flux SPM
cross-section (12 slots / 10 poles) as a 2D Static study. Details: `static-3d.md` sections 8.2, 8.5 and 9;
`jmag-experiments` -> `references/pmsm.md` section 2c.

- User B-H steel through `create_material` (`MagneticPropertyType` 0, `MagneticSteelPermeabilityType` 2, `BhTable`):
  the 22-point table plus the `(0, 0)` origin read back as 23 rows equal to the input; `IsMagnet()` False.
- 3D Static study properties read: `NonlinearMaxIteration` 15 (Flag), `NonlinearTolerance` 1e-3 (Double),
  `IccgTolerance` 1e-8, `IccgMaxIteration` 5000, `FullModelConversion` 1, `ConversionFactor` 1. Set to 50 / 1e-5:
  at 15 A the default and the tight tolerance gave psi equal to 4e-8 and torque to 3e-7 (7 vs 9 iterations).
- The case folder's `jcgmntr` gives the nonlinear iterations per step (`solver_monitor.convergence`): 8-9 per case,
  none at the limit. Mesh 29-47 s + solve 119-198 s per case at 622 k elements (machine shared with another job).
- Every nonlinear case logged "Magnetic flux density is over B-H curve. Please check B-H curve. ... Solver
  extrapolate the magnetic permeability using the extrapolation method specified." (peak |B| 2.49 T > table end
  2.2 T); a warning only.
- 180 deg `RotationPeriodicBoundary` `Reverse` sector at a loaded position: rotor nodal `Torque` 1.863 N m vs 1.882
  N m from the full model, so the sector torque is reported as the full-model value (factor 1), as `CoilFlux` and
  `StoredEnergy` are. The stator `Torque` condition reports the opposite sign (-1.925 N m). Done with the defaults
  `FullModelConversion` 1 / `ConversionFactor` 1; other values not tried.
- Nonlinear accuracy vs an independent 3D FE solver with the same B-H table: psi 0.2-0.6 %, load torque 0.6 % at 5 A
  and 0.5 % at 15 A, cogging peak-to-peak 0.03 %.
- 2D Static radial SPM from one rotated DXF per rotor angle (`import_dxf`, rename by point, `add_current` per coil
  side up / down, Radial magnets outward / inward, `linear_2000`): `SymmetryBoundary` on the edges of the outer
  circle and of an internal bore circle accepted; the 2D mesh control has `AirRegion` and accepts 0;
  `ModelThickness` sets the stack; nodal `Torque` (`Origin`, `Axis2D`, `ExpandTarget`) gives result key `Torque`.
  5 s mesh + 2 s solve at 20 k elements.
- A zero-field bore disc enclosed by `SymmetryBoundary` edges triggers the solver warning "Check if there are one or
  more independent models that are surrounded by the symmetry boundaries" on every run; benign there.
- 2D cogging is mesh-sensitive: 20 k -> 101 k elements moved the cogging peaks by 30 % while psi moved 0.3 % and
  load torque < 0.1 %. Agreement with an independent solver at matched sizes: psi 0.03 %, load torque 0.3 %.
- `rename_parts` reports `duplicate_names` when a DXF part straddling theta = 0 was written as two regions with the
  same label name (a magnet at a rotated angle); dedupe the labels (`<name>__2`) before renaming.
