# 3D Static: nonlinear steel, radial machines (sections 9-10)

Part 3 of 3 of the 3D Static reference (index: `static-3d.md`). Basics: `static-3d-basics.md`; rotating machines: `static-3d-rotating.md`.

Contents: 9 Nonlinear 3D Static (9.1 User B-H steel - 9.7 Deep saturation) - 10 Radial-flux machine with straight end windings

## 9. Nonlinear 3D Static

**VERIFIED (23.0, 2026-10-20)** on the axial-flux PM machine of `static-3d-rotating.md` section 8 (same geometry, mesh,
622 k elements, and cases: no load at 10 angles, I_q = 5 A and 15 A at 3 angles, plus the loaded
180 deg sector of 8.2) with the steel parts (stator yoke, teeth, rotor iron) on a user B-H curve,
M270-35A, 22 points 0.1 .. 2.2 T. Compared with an independent 3D FE solver using the same B-H
table and a Newton solve.

### 9.1 User B-H steel

| Step | Call | Status |
|---|---|---|
| Create | `jmag_create_material(name, bh=[[H, B], ...])` (worker op `create_material`): `CreateCustomMaterial`, then on `GetUserMaterial(name)` `MagneticPropertyType` 0 (soft-magnetic steel; 1 is a magnet), `MagneticSteelPermeabilityType` 2 (B-H curve), `SetTable("BhTable", ...)` | VERIFIED (3D, 2026-10-20) |
| Table | the 22 points plus the origin `(0, 0)` as the first row, H in A/m, B in T | VERIFIED |
| Read back | `GetUserMaterial(name).GetTable("BhTable")`: all 23 rows equal to the input, `MagneticPropertyType` 0, `IsMagnet()` False (snippet S-3D-10) | VERIFIED |
| Assign | `s.SetMaterialByName(part, name)` per steel part, check `s.GetMaterial(part).GetName()` | VERIFIED |

Do not copy the flags of a protected library steel: its MaterialObject flag `MagneticType` 0
("PermON_BH") is a magnet with a demagnetisation table (`jmag-simulation` ->
`references/properties.md`). `create_material` sets the Material-Editor flags above and checks them.

### 9.2 Analysis control

| Study property | Default read | Set here | Note |
|---|---|---|---|
| `NonlinearMaxIteration` | 15 | 50 | no case reached the limit |
| `NonlinearTolerance` | 1e-3 | 1e-5 | at 15 A, 3 deg: default vs 1e-5 gave psi equal to 4e-8 and torque to 3e-7 (relative), 7 vs 9 iterations |
| `IccgTolerance` | 1e-8 | unchanged | linear solver |
| `IccgMaxIteration` | 5000 | unchanged | linear solver |
| `FullModelConversion` / `ConversionFactor` | 1 / 1 | unchanged | sector values are full-model values (8.2) |

Read each property gated on `GetPropertyType` and set it with `SetValue` on
`s.GetStudyProperties()` (snippet S-3D-11; the runs went through `jmag_exec_python`). The default
tolerance was already enough for psi and torque on this machine; the tighter one costs about two
iterations. VERIFIED (23.0, 2026-10-20).

### 9.3 Convergence: iteration count from `jcgmntr`

The case folder of a finished run (`<project>.jfiles/<model>~N/<study>~M/CaseK`) holds `jcgmntr`,
rows `step  nonlinear_iteration  linear_iterations`. The bridge parses it with
`jmag_bridge.solver_monitor.convergence(folder, max_iter)` (max iteration per step, mean, steps at
the limit); the runs here called it on the `calc_folder` the run reported (snippet S-3D-12), and
`jmag_check(stage="post")` returns the same dictionary as `convergence`. Here: 8-9 nonlinear
iterations per case, none at the limit. VERIFIED (23.0, 2026-10-20).

### 9.4 Cost

Ryzen 7 3800X, 622 k elements, another heavy job on the machine (upper bounds): mesh 29-47 s and
solve 119-198 s per case (about 9 nonlinear iterations), against ~40 s for the linear solve of 8.6.
The 311 k element 180 deg sector solved in 48 s. Full set (13 load and no-load cases, the tolerance
probe, the sector) about 65 min.

### 9.5 Cross-validation numbers

JMAG 23.0 vs the independent 3D FE solver, both nonlinear with the same table: psi_A -0.2 to
-0.6 % over all angles; load torque -0.6 % at 5 A and -0.5 % at 15 A; cogging peak-to-peak 0.3267
vs 0.3268 N m (-0.03 %). The nonlinear-minus-linear change of psi agreed to 0.005 % (absolute) in
the two tools (no load, 0 / 3 / 6 deg: JMAG -0.652 / -0.570 / -0.435 %). At 15 A the tooth tips
saturate (|B| 99th percentile 2.21 T, max 2.49 T, past the table's 2.2 T: both tools extrapolate
the curve there) while torque stays linear in current (15 A / 5 A = 2.990 .. 3.001).

### 9.6 Traps

1. B beyond the last table point is extrapolated: every nonlinear case here (no load included)
   reported the solver warning "Magnetic flux density is over B-H curve. Please check B-H curve.
   ... Solver extrapolate the magnetic permeability using the extrapolation method specified."
   It is a warning, not an error, and the results still agreed with the other solver; when the
   result depends on the deep-saturation region, extend the table past the peak |B| instead.
2. The defaults (15 iterations, 1e-3) were adequate here; on a harder saturation case read the
   `jcgmntr` count (9.3) before trusting a result, and raise `NonlinearMaxIteration` when a step
   sits at the limit.
3. Sector torque: factor 1, stator sign opposite (8.5).

### 9.7 Deep saturation

**VERIFIED (23.0, 2026-10-20)** on a variant of the section-8 machine built to saturate the teeth in
bulk: stator yoke 4 mm, tooth arc ratio 0.25, magnets 8 mm thick, B_r 1.4 T, pole arc 0.9, gap
0.6 mm, I_q = 100 A (30 turns), same M270-35A table, full 360 deg model, 832 k elements, the
settings of 9.2. At 100 A and 3 deg the independent solver put the median tooth |B| at 1.84 T, the
99th percentile at 2.89 T and the maximum at 3.18 T: far past the table's last point 2.2 T (that
table ends on a slope of about mu0, so the extrapolation is near vacuum slope).

| Quantity | Result |
|---|---|
| Nonlinear iterations (`jcgmntr`) | 10-12 per case (none at the limit of 50; the default 15 would have held) |
| Solve / mesh | 245-300 s / 40 s per case; the same cases with `linear_2000` steel: 55 s |
| psi_A vs the independent solver | 0.2-0.5 % (no load and 100 A, 0 and 3 deg) |
| Load torque vs the independent solver | 0.4 % (0 deg), 1.0 % (3 deg) |
| Torque at 100 A, B-H steel / linear steel | 0.706 / 0.693 (0 / 3 deg); the independent solver 0.700 / 0.684 |
| No-load psi_A, B-H steel / linear steel | -25.4 % (the independent solver -26.1 %) |

So JMAG's nonlinear 3D Static stays within 1 % of an independent Newton solver at a bulk-
saturated point, the over-B-H-curve warning of 9.6 included.

## 10. Radial-flux machine with straight end windings

**VERIFIED (23.0, 2026-10-20)** on a 12-slot / 10-pole radial SPM (stack 10 mm, tooth coils
whose sides run straight 5 mm past both stack ends, air around the overhang, linear steel mu_r
2000, arc magnets B_r 1.2 T / mu_r 1.05 radial and alternating, 30 turns), full 360 deg model
from one STEP per rotor angle (52 solids), 3 no-load angles and one load point, against an
independent 3D FE solver of the same model.

1. Import the STEP, rename by inside points, check volumes (6.5e-6 here).
2. Arc magnets: on each magnet part `SetPattern("Radial")`, `SetOriginXYZ(0, 0, 0)`,
   `SetAxisXYZ(0, 0, 1)`, `SetOrientation(True)` outward / `False` inward; read back `GetPattern()`.
   The material object also offers `SetAxis`, `SetAxisFromReferenceTarget`, `SetOrigin`,
   `SetReverseAxisDirection`, `GetAxis`, `GetOrigin` (method names listed with `dir()`).
3. Coils without end turns: one `Current` condition per coil SIDE, `Turn` N, `DirectionType` 0
   (InflowFace), the inflow face = that side's end face on one end plane (selected by position);
   the other end face lies on the opposite end plane and JMAG finds it as the outflow face. Run the
   current up one side and down the other (inflow at opposite ends); psi_coil = sum of the two
   sides' `CoilFlux` (each oriented with its own current), psi_phase = sum polarity x psi_coil.
4. `SymmetryBoundary` on every boundary face (stator outer cylinder, bore, both end planes with
   all coil end faces): 62 faces here; selecting them by one point per face gave 62 of 62.
5. Nodal `Torque` on the rotor iron + magnets; `AirRegion` 0; part sizes gap shells 0.4, magnets
   0.75, steel / coils / air 1.5 mm: 721 k elements, 131 k nodes, 27 s mesh + 43 s solve.

Results vs the independent solver (per metre of stack): psi_A 0.11-0.14 % at 0 / 3 / 6 deg no load
and at I_q = 5 A; load torque 41.51 N m/m vs 41.25 (Maxwell, -0.6 %) and 41.57 (virtual work,
+0.2 %). At the zero-cogging positions 0 / 3 / 6 deg the nodal torque read +0.05 / -0.03 / -0.72
N m/m (a new mesh per angle; 1.7 % of the load torque): quote 3D cogging only from a mesh study as
in `jmag-experiments` -> `references/pmsm.md` section 2c.

Trap (geometry side): STEP solids written one per region are separate shells; a CAD kernel that
takes the combined boundary without gluing them first reports every internal face as boundary.
Glue (e.g. gmsh `removeAllDuplicates`) before collecting the boundary-face points.
