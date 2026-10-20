# JMAG behaviours that surprise people (verified on JMAG-Designer 23.0)

Each item was observed while building this bridge. The bridge works around most of them; knowing
them lets you interpret what you see.

## API and process
- The Python API (`jmag/designer.py`, ProcessLink) only works with JMAG's bundled **Python 3.8**;
  with 3.11 every call returning nothing crashes with an access violation. The bridge runs its
  worker under `<JMAG>/python3.8/python.exe` for this reason.
- Errors are logged, not raised - even with `SetUseExceptions(True)`. A failed lookup returns an
  object whose `IsValid()` is False and every later call on it silently does nothing.
- Probing for existence logs an error too (e.g. `GetCondition("missing")`, `GetLink()` on a
  non-circuit condition, `GetValue` on Integer/Vector properties).
- Attaching to a dead PID "succeeds"; afterwards every call returns None.
- `Load` / Geometry `Load` of a missing file crashes the client: paths are validated first.
- PyJmag and designer.exe print to stdout; a child designer inherits pipes. The bridge isolates
  its protocol from this noise.
- `GetDataFromName` wants internal keys (`LineCurrent`, `TerminalVoltage`, `FEMCoilFlux`,
  `FEMCoilInductance`, `StoredEnergy`); display names ("Circuit Voltage") silently return empty data.
- `Application.CancelProcess` blocks when called from another connection.
- A Designer launched as a child of an agent session dies with the session; the bridge launches it
  detached (WMI) so it survives.

## Modelling
- Static2D/StaticAX/Static (3D) accept **no circuit**; FEM coils/conductors need Transient or
  Frequency studies. Static coil excitation = `Current` condition (Turn, Current, Direction2D;
  in 3D `DirectionType` instead, see the 3D section below).
- 2D `ModelThickness` (stack length) defaults to **1 mm**.
- A custom B-H material made through the MaterialObject flags is a **magnet** unless the
  Material-Editor view says `MagneticPropertyType = 0`; the solver then aborts with "Check the
  point array setting in material ...". `jmag_create_material` sets and verifies this.
- An attached JMAG keeps the error flag of the previous session's failed call; `jmag_connect`
  clears it (before the fix a clean connect reported that stale error as its own failure).
- A FEM coil's `Turn` is spread over all regions of one coil side: with k regions in the "up"
  side each region carries Turn*I/k. For k series tooth coils of N turns use Turn = k*N.
- The slide mesh needs `AutoDivision = 0` (and `AutoGapDivision = 0`) for your
  `CircumferentialDivision` to be used; otherwise JMAG picks its own and rewrites the step control.
- `View.ShowMesh()` persists: a later contour export draws every element edge unless the view is
  reset with `ShowModel()` (the bridge does both now).
- A closed shaft-bore loop in a DXF becomes its own region: name it (e.g. `Shaft`, Air) and
  move it with the rotor instead of suppressing it.
- Ground components ignore the requested name (always "Ground"); other components get an empty
  name if none is given.
- Circuit terminals (grid units, rotation 0): two-terminal parts (x-2,y),(x+2,y); one-terminal
  sources (x+2,y); 3-phase sources U(x+2,y+2) V(x+2,y) W(x+2,y-2); Ground (x,y+2); VoltageProbe (x,y-2).
- A 3-phase source's own current column is the phase sum (zero when balanced).
- Magnets: `Radial` pattern + `SetOrientation(True)` outward / `False` inward; `Parallel` + direction.
- The Geometry Editor needs a **saved** project, otherwise Designer blocks on a modal "save the
  project file" dialog.
- DXF import: every closed face becomes a part, including the air gap between closed rotor and
  stator outlines. Part names are `<layer>/Region.N`. Centroids of C/L shapes lie outside the
  part - use `inside_point`.
- Rotating parts on the normal mesh -> "negative element volume" abort. Use the slide mesh and keep
  user parts out of the gap (suppress the gap region).
- With motion, JMAG may rewrite the step control (e.g. 121 -> 129 rows) - info
  STEP_AUTO_UPDATED; trust the returned time axis.
- Results of an aborted run: tables contain only the solved steps.

## Solver behaviour
- Time integration is implicit (backward) Euler: v_k = R i_k + (psi_k - psi_{k-1})/dt holds to
  ~1e-7 - use backward differences when checking.
- Newton often "does not converge" at current zero crossings; harmless there.
- Peak |B| at re-entrant corners grows with mesh refinement (field singularity).
- The progress window ("Run Analysis") has an Abort button that raises "Stop calculation";
  after answering it the solver can keep writing results for minutes - the bridge then terminates
  it. Killing the solver *before* answering that prompt has crashed Designer.
- Every Designer instance spawns a Geometry Editor helper (`modeller.exe`); it is left behind when
  Designer dies -> `jmag_cleanup`.

## 3D Static (verified 2026-10-19; details in `references/static-3d.md`)
- `Current` with `DirectionType = 1` (Vector) on a closed winding: the run finishes, no error, and the
  coil carries no current (flux linkage ~2e-20 Wb). It is not a "circulate about an axis" mode.
- `DirectionType = 0` (InflowFace) needs the outflow face on a symmetry or periodic boundary. A slit
  winding inside the air fails at Run with "Cannot find flow-out face of FEM coil (or Current
  condition) ...". Use a half model cut through the winding axis with `SymmetryBoundary` on the cut
  plane (exact when the plane is flux-parallel) and pick one end face as the inflow face.
- `SelectFaceByPosition` on a face shared by two solids selects both faces; check `NumFaces()`.
- `GetUserMaterial` returns an invalid object for library materials; `GetValue` on a Complex
  material property (e.g. `Permeability` set with `SetComplexValue`) logs an error, so it cannot be
  read back. Use a linear B-H table for a custom permeability.
- gmsh's STEP writer drops solid names; a 360 deg OCC revolve leaves free faces that export as extra
  sheet products. `python -m jmag_bridge.cad3d model.step` flags both before import.
- Imported STEP parts are named `<assembly>/<product>`; rename by inside point.
- `jmag_add_current` writes `Direction2D` and is for 2D studies; the 3D `Current` goes through
  `jmag_exec_python` (snippet S-3D-2).

## 3D Static, rotating machines (verified 2026-10-20; `references/static-3d.md` section 8)
- No `RotationMotion` in a Static study: one rotor angle per case. Write one merged STEP per angle and
  re-import it (~80 s per case at ~620 k elements, ~160 s with a load study).
- `SymmetryBoundary` has no type property; it is always flux-parallel. Faces with no condition are
  natural (flux normal): for an `A_t = 0` box put `SymmetryBoundary` on every outer face.
- `RotationPeriodicBoundary` `BoundaryType` 1 (Reverse) = anti-periodic; one cut plane is enough. A
  sector's `CoilFlux` / `StoredEnergy` are already full-model values: no symmetry factor.
- Closed tooth coils need a slit and two leads to the outer wall for InflowFace (lead end faces under
  `SymmetryBoundary`).
- `Torque_Surface` takes no parts and the run aborts; an internal face picked by position selects both
  sides and cancels. Use the nodal `Torque` condition (result key `Torque`).
- `GetValue` of a property the condition does not have (`TargetType` on `Torque`) fails the call:
  check `GetPropertyNames()` first.
- Per-angle meshes make cogging noisy (+-0.035 N m on a 0.33 N m peak-to-peak here); `StoredEnergy`
  with magnets is not comparable with another tool's 1/2 integral(nu B^2).
