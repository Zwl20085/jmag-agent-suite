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
