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
  Frequency studies. Static coil excitation = `Current` condition (Turn, Current, Direction2D).
- 2D `ModelThickness` (stack length) defaults to **1 mm**.
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
