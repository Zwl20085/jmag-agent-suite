# 3D Static study: STEP model, coil excitation, symmetry, inductance

Contents: 1 Status - 2 Coil excitation: which mode - 3 Workflow step by step - 4 Result keys -
5 Traps - 6 Cross-validation numbers - 7 Not verified yet -
8 Rotating machines in 3D Static (PM machine, rotor positions, periodic sector, torque)

## 1. Status

**VERIFIED (23.0, 2026-10-19)** = run live on JMAG-Designer 23.0.01zv on 2026-10-19 with the result
checked against independent FE. Two linear transformers were driven end to end through the bridge's
worker: an E55/28/21 pair with a 1 mm centre-leg gap (genuinely 3D) and a P66/56 pot core (checkable
against an axisymmetric reference). **UNVERIFIED** = documented in the 23.0 reference or inferred,
not run. The ready-made tools cover project, import, rename, study, materials, mesh, run and export;
the 3D `Current` condition, face-selected `SymmetryBoundary`, mesh controls and the result read go
through `jmag_exec_python` (snippets S-3D-1 to S-3D-5 in `jmag-scripting` -> `references/snippets.md`).
The 2026-10-19 runs called the worker ops behind these tools directly (the same code the tools wrap,
without the MCP layer). The server-side extras were therefore not exercised on a 3D model: the
`jmag_check` preflight that `jmag_run` runs first (`check_first=true`) and the post-run check may
flag a 3D `Current` condition they do not know (it has no `Direction2D`); read such a finding, do
not take it as proof of a wrong set-up. 3D runs are long: `jmag_run(study, wait_seconds=30)` and
poll `jmag_run_status`.

**VERIFIED (23.0, 2026-10-20)** = the second batch, run live on 23.0.01zv on 2026-10-20: an axial-flux PM
machine (12 slots / 10 poles, linear materials) as a full 360 deg model in 13 rotor-position cases plus one
180 deg periodic sector, checked against an independent 3D FE solver. It covers magnets, slit coils with
leads, the nodal `Torque` condition, `RotationPeriodicBoundary` and per-angle STEP cases: section 8.

## 2. Coil excitation: which mode

A 3D static study takes no circuit and no FEM coil. The `Current` condition is the coil, and its
`DirectionType` decides whether a closed winding carries any current.

| Winding | Use | Status |
|---|---|---|
| Closed ring / racetrack (any real transformer or inductor winding) | **half model** cut through the winding axis, `SymmetryBoundary` on the cut plane, `Current` with `DirectionType 0` (InflowFace), one end face of the U-shaped half winding as inflow face | VERIFIED (23.0, 2026-10-19) |
| Same winding, `DirectionType 1` (Vector) with `Direction` / `Origin` | do not use: a uniform current direction that a closed loop cannot carry; the run finishes, no error, flux linkage ~2e-20 Wb | VERIFIED negative (23.0, 2026-10-19) |
| Winding slit (C shape) with both ends inside the air | fails at Run: "Cannot find flow-out face of FEM coil (or Current condition). Case 1: flow-out face is not set on Symmetry boundary (3D). Case 2: ... Periodic boundary. Case 3: Duplicated nodes" | VERIFIED negative (23.0, 2026-10-19) |
| Closed tooth coil of a rotating machine (full or sector model) | slit the coil (0.5 mm) and run two leads to the outer wall of the air box; `SymmetryBoundary` on every outer face incl. the two lead end faces; InflowFace = one lead end face (section 8.4) | VERIFIED (23.0, 2026-10-20) |
| Winding cut by a periodic boundary (sector model) | InflowFace with the end faces on the periodic boundary; allowed by the same error message, not run | UNVERIFIED |
| Straight conductor bar ending on the outer boundary | InflowFace with the outflow face on the model's outer surface | UNVERIFIED (same rule; the slit-coil leads above are the verified instance) |
| Closed winding driven by a `CurrentDensity` condition (listed by `GetConditionTypes()`) | not tried | UNVERIFIED |
| Circuit-driven coil, AC, eddy currents | FEM coil in a `Transient` / `Frequency` study (3D FEM coils need inflow faces too) | UNVERIFIED in 3D |

The rule behind the table: with InflowFace the solver looks for the outflow face on a symmetry or
periodic boundary, so **the inflow and outflow faces must lie on the model's outer surface**. A half
model whose cut plane contains the winding axis is exact when that plane is flux-parallel (B
tangential to it), which holds for coaxial windings on a core symmetric about the plane. The half
model has the side benefit of half the elements.

Direction: current enters through the selected inflow face. With the inflow face at the x > 0 end
on the plane y = 0 the current runs in +y there, i.e. right-handed about +z. Check the sign of
`CoilFlux` against that: a reversed inflow face flips the sign of M.

## 3. Workflow step by step

| # | Step | Call | Status |
|---|---|---|---|
| 1 | Check the STEP before import | `python -m jmag_bridge.cad3d model.step`: leaf product names, solids per product, warnings (unnamed products, sheet-only products, duplicates). Exit 1 on warnings | offline helper; the name pattern it predicts is VERIFIED (23.0, 2026-10-19) for one assembly level |
| 2 | Project | `jmag_connect(launch_new=true)`, `jmag_new_project(path)` | VERIFIED (23.0, 2026-10-19) |
| 3 | Import | `jmag_import_cad(path, unit="mm")` = `LoadCadDataWithOption(path, name, healing=True, remove_sheet=True, "mm")`. Parts arrive as `<assembly>/<product>` (e.g. `E_core_pair/Core_Top`); volumes matched the STEP to < 2e-5 | VERIFIED (23.0, 2026-10-19) |
| 4 | Rename and check | `jmag_rename_parts(renames=[{"part": [x, y, z], "name": "Core_Top"}, ...])` (= `GetPartByPosition(CreatePoint(x, y, z))` + `SetName`), one point strictly inside each solid; compare every returned `volume` with the CAD (1e-3) | VERIFIED (23.0, 2026-10-19) |
| 5 | Study | `jmag_create_study(study_type="Static", name=...)` (= `CreateStudy("Static", name)`). `GetConditionTypes()` lists 29 types incl. `Current`, `SymmetryBoundary`, `CurrentDensity` | VERIFIED (23.0, 2026-10-19) |
| 6 | Materials | `jmag_set_material(..., material="linear_2000")` (library linear steels `linear_<mu_r>`), `Copper` for coils, `Air` for gap and air solids. Other mu_r: `jmag_create_material(name, bh=[[H, mu0 mu_r H], ...])` with a linear table to 1e6 A/m; with mu_r 2000 it reproduced `linear_2000` to 1e-11 | VERIFIED (23.0, 2026-10-19) |
| 7 | Coil | `jmag_exec_python`, snippet S-3D-2: `CreateCondition("Current")`, `Turn`, `Current`, `DirectionType 0`, `UniformCurrent 1`, part selection, then `GetSecondarySelection().SelectFaceByPosition(x, y, z)` + `AddSecondarySelected` on the inflow end face. Selecting the outflow face is not needed (the solver finds it on the symmetry boundary). Not `jmag_add_current`: it writes `Direction2D` | VERIFIED (23.0, 2026-10-19) |
| 8 | Symmetry | `jmag_exec_python`, snippet S-3D-3: `CreateCondition("SymmetryBoundary")`, one `SelectFaceByPosition` per face of the cut plane (and the outer air faces), check `GetSelection().NumFaces()` against the point count. `SymmetryBoundary` has no properties at all (probe of 2026-10-20): it is always flux-parallel (B tangential; agreed with an `A_t = 0` solver to 1e-4 in k). A face with no condition is JMAG's natural boundary (flux normal) | VERIFIED (23.0, 2026-10-19 / 2026-10-20) |
| 9 | Mesh | snippet S-3D-4: mesh control `AirRegion 0` (the STEP carries its own air solid), `AutoMeshSize 0`, `MeshSize` = air size; `Part` mesh conditions with `Size` per core / coil / gap part (`jmag_mesh_size` makes the same condition) | VERIFIED (23.0, 2026-10-19) |
| 10 | Energy | study property `Energy = 1` (`jmag_set_properties(study, {"Energy": 1})`): `StoredEnergy` then has one column per part plus `Total` | VERIFIED (23.0, 2026-10-19) |
| 11 | Second excitation | `jmag_duplicate_study(study, new_name)` copies conditions and mesh settings; set the currents of the copy (primary 0 A, secondary 1 A) | VERIFIED (23.0, 2026-10-19) |
| 12 | Save, mesh, run | `jmag_save_project`, `jmag_mesh(study)` (`CreateMesh`, element / vertex count), `jmag_run(study)`; save before each | VERIFIED (23.0, 2026-10-19) |
| 13 | Results | snippet S-3D-5: `GetResultTable().GetDataFromName("CoilFlux" / "CoilCurrent" / "StoredEnergy")`; `jmag_export_results(study, out_dir)` for CSV | VERIFIED (23.0, 2026-10-19) |
| 14 | Quit | `jmag_quit(save=false)`; afterwards no `designer.exe` / `modeller.exe` was left (checked with `Get-Process designer, modeller`) | VERIFIED (23.0, 2026-10-19) |

Inductance from the two studies (I = 1 A each, half model, factor sf = 2):
`L_jk = sf psi_j / I_k`, `W = sf StoredEnergy.Total`. Report the two invariants with the numbers:
energy identity `W / (L I^2 / 2) - 1` and reciprocity `M12 / M21 - 1` (both < 1.3e-10 in every run on
2026-10-19), plus `0 <= k < 1`. The factor was applied by hand; JMAG's `FullModelConversion` /
`ConversionFactor` were not used (UNVERIFIED for 3D).

Runtime (Ryzen 7 3800X): 1 M elements mesh in ~22 s and solve in ~80 s; 2.7 M elements ~28 s +
~280 s per study. Start coarse (core size ~4 mm), then halve.

## 4. Result keys (3D Static, Current condition)

| Internal key | Columns | Notes | Status |
|---|---|---|---|
| `CoilFlux` | one per `Current` condition | flux linkage, turns included; a half model with `SymmetryBoundary` reports the half-model value (x2 by hand), a `RotationPeriodicBoundary` sector reports the full-model value (factor 1); a 0 A condition still reports its flux | VERIFIED (23.0, 2026-10-19 / 2026-10-20) |
| `CoilCurrent` | one per `Current` condition | the set current | VERIFIED (23.0, 2026-10-19) |
| `StoredEnergy` | one per part + `Total` | needs `Energy = 1`; full-model value in a periodic sector; with magnets it is not 1/2 sum(nu B^2 V) (section 8.8) | VERIFIED (23.0, 2026-10-19 / 2026-10-20) |
| `Torque` | one per `Torque` condition, one row | nodal-force torque about the condition's axis, N m (section 8.5) | VERIFIED (23.0, 2026-10-20) |

The verified driver matched a column by the condition name first, else by the single column name
ending with it, and failed on zero or several matches (defensive; whether JMAG prefixes the names
was not recorded).

## 5. Traps

1. `DirectionType 1` (Vector) on a closed winding: zero current, no error (section 2). A flux
   linkage of ~1e-20 Wb is this trap, not a tiny inductance.
2. InflowFace with the coil ends inside the air: fails at Run with the "Cannot find flow-out face"
   message. Cut the model so the end faces lie on a symmetry boundary.
3. `SelectFaceByPosition` on a face shared by two solids selects both (two face ids). On the
   symmetry plane the coil end face is external and one id comes back; check `NumFaces()`.
4. `GetUserMaterial` returns an invalid object for library materials (only custom materials have a
   Material-Editor view). Reading a Complex material property with `GetValue` logs "The property
   should be a flag or real property" and turns the call into `ok: false`;
   `SetComplexValue("Permeability", mu, 0)` therefore cannot be read back (UNVERIFIED as a route; use
   the linear B-H table).
5. gmsh's own STEP writer drops names (products become `Open CASCADE STEP translator 7.8 n`); rename
   the PRODUCTs in the CAD script, or rename every part by inside point. `jmag_bridge.cad3d` flags it.
6. A 360 deg revolve in Open CASCADE leaves the seam faces as free faces; exported, they become extra
   sheet products. Remove them before export (`jmag_bridge.cad3d` warns on sheet-only products).
7. A gap solid that closes a winding window splits the air: give the window air its own solid and name.
8. No modal dialog and no Designer crash occurred in any 3D run on 2026-10-19.

## 6. Cross-validation numbers

JMAG 23.0 vs an independent edge-element 3D FE solver (same geometry, full model / 15 deg sector):
E core L11 -0.51 %, L22 -0.42 %, M -0.47 %, k -1e-4 (JMAG 208.91 / 53.93 / 104.89 uH, k 0.98816 at
979 k elements). Pot core L_m -0.85 %, k -6e-4; vs an axisymmetric FE chain L_m 10.42 mH -> JMAG
10.37 mH (-0.48 %). Both formulations converge from below; JMAG needed ~3x the elements for the same
value. Use this as the expected accuracy of a well-meshed linear 3D Static run.

## 7. Not verified yet

3D Transient / Frequency with FEM coils, circuits or eddy currents; windings (`jmag_winding`) in 3D;
field exports, point probes and section graphs in 3D (snippets S11 / S12 are UNVERIFIED in 2D too);
nonlinear B-H in 3D; `FullModelConversion` / `ConversionFactor` (left at their defaults); the torque
conversion of a periodic sector (only a cogging-zero position was run on a sector); a coil cut by a
periodic boundary; `RotationPeriodicBoundary` `Normal` / `AxialReverse`; `CurrentDensity`;
`Torque_Surface` on an external face; rotor motion inside one Static study (no `RotationMotion`:
rotor positions are separate cases).

## 8. Rotating machines in 3D Static

**VERIFIED (23.0, 2026-10-20)** on an axial-flux surface-PM machine, 12 slots / 10 poles, linear
materials (steel mu_r 2000, magnets B_r 1.2 T / mu_r 1.05, axial and alternating), 30-turn tooth
coils; full 360 deg model, 622 k elements / 111 k nodes; 13 rotor-position cases (no load at 10
angles, load at 3) and one 180 deg periodic sector. Compared with an independent 3D FE solver on the
same geometry and air box.

### 8.1 What a 3D Static study offers

`GetConditionTypes()` (29, snippet S-3D-9): SymmetryBoundary, NaturalBoundary,
RotationPeriodicBoundary, TranslationPeriodicBoundary, Force, LorentzForce, Force_Surface, Torque,
LorentzTorque, Torque_Surface, EddyCurrent, StressDistribution, MagneticFlux, PartialModel,
ExternalField, LineCurrent, Gap, Current, CurrentDensity, Ironloss, Magnetostriction,
ResidualStrain, FrozenPermeability, TemperatureDistribution, Displacement, ReuseDemagnetization,
VectorPotentialBoundary, TopologyOptimization, SensitivityAnalysis. **No `RotationMotion`**: a Static
study is one rotor position, so a cogging or torque-vs-angle curve is a set of cases (8.6).

Property names read with `GetPropertyNames()` / `GetPropertyTable()`:

| Condition | Properties |
|---|---|
| `RotationPeriodicBoundary` | `Axis`, `Origin`, `BoundaryType` (keys `Normal` / `Reverse` / `AxialReverse`), `AutoPeriodicFromWinding`, `Angle` |
| `Torque` | `Origin`, `Axis`, `ExpandTarget` |
| `Torque_Surface` | `Origin`, `Axis` |
| `SymmetryBoundary` | none (always flux parallel) |
| `Current` | `Turn`, `Current`, `Direction`, `Origin`, `UniformCurrent`, `DirectionType` |

`c.GetValue(name)` of a property the condition does not have logs "No property such as ..." and
fails the worker call even inside `try` (seen with `TargetType` on `Torque`): read
`GetPropertyNames()` first.

### 8.2 Outer and periodic boundaries

| Boundary | Set-up | Status |
|---|---|---|
| Outer box, flux parallel (= `A_t = 0`) | `SymmetryBoundary` on **every** outer face of the air box, lead end faces included | VERIFIED (23.0, 2026-10-20) |
| A face with no condition | JMAG's natural boundary = flux normal; differs from `A_t = 0` | documented (manual "Symmetry Boundary Condition and Natural Boundary Condition"), avoided |
| Periodic sector | `RotationPeriodicBoundary` with `Origin` (0,0,0), `Axis` (0,0,1), `Angle` = sector angle, `AutoPeriodicFromWinding` 0, `BoundaryType` 1, on the faces of **one** cut plane only (nothing on the other); snippet S-3D-6 | VERIFIED (23.0, 2026-10-20) |
| `BoundaryType` | integer in key order: 0 `Normal`, 1 `Reverse`, 2 `AxialReverse`. 1 Reverse = anti-periodic, the choice for a sector spanning an odd number of poles; Normal (periodic, even number of poles) by name | `BoundaryType` 1 = anti-periodic VERIFIED; 0 and 2 UNVERIFIED |
| Sector results | `CoilFlux` and `StoredEnergy` of the sector come out already converted to the full model: **do not multiply by the symmetry factor** (unlike the half model of sections 2-3, which needed x2) | VERIFIED (23.0, 2026-10-20) |
| `FullModelConversion` / `ConversionFactor` | left at the defaults | UNVERIFIED |

Check: 180 deg sector (6 slots / 5 poles, `Reverse`), theta = 0, no load: psi_A 50.413 / psi_B
-25.414 mWb vs the full model 50.400 / -25.428 (+0.03 % / -0.06 %), energy 3.8257 vs 3.8245 J,
311 k elements, solve 15 s. Torque at that position is cogging-zero (sector 0.0009, full -0.0054
N m, both mesh noise), so the torque conversion of a sector is not tested.

### 8.3 Magnets

- Custom linear magnet (snippet S-3D-7): `CreateCustomMaterial`, `GetUserMaterial`,
  `MagneticPropertyType` 1 (magnet), `MagnetPermeabilityType` 6 (Linear(Br)),
  `ResidualMagneticFluxDensity` B_r [T], `MagnetPermeability` mu_r; read back, `IsMagnet()` True.
  VERIFIED (23.0, 2026-10-20).
- The library `NdFeB_Br=1.2(T)` gave the same flux linkage as the custom B_r 1.2 T, mu_r 1.05 magnet
  to 1e-8. Recoil mu_r matters: 1.00 instead of 1.05 moved psi by +1.2 %.
- Direction per part: `s.SetMaterialByName(part, mat)`, then `mt = s.GetMaterial(part)`,
  `mt.SetPattern("Parallel")`, `mt.SetDirectionXYZ(0, 0, +-1)`, read back with `mt.GetDirection()`.
  Reversing every magnet negated psi exactly. VERIFIED (23.0, 2026-10-20).

### 8.4 Coils: slit tooth coils with leads

A closed tooth coil has no face on the model's outer surface, so InflowFace cannot find an outflow
face (section 2). The verified route:

1. In the CAD, slit each coil (0.5 mm) at its outer end turn and run two radial leads from the slit
   to the outer wall of the air box (`jmag-geometry`); label the two lead end faces in the labels
   JSON.
2. `SymmetryBoundary` on every outer face of the box, the lead end faces included (the solver finds
   the outflow face there).
3. `Current` per coil (snippet S-3D-2): `Turn` N, `DirectionType` 0, part = the coil, secondary
   face = the "in" lead end face. Which way the current then circulates follows from the lead
   geometry: check the sign of one `CoilFlux` against a hand estimate.
4. Phase quantities: `i_coil = polarity x i_phase` from the winding table, `psi_phase = sum polarity
   x CoilFlux` over the coils of the phase (`CoilFlux` includes the turns).

Verified details: a negative `Current` reverses the current while `CoilFlux` keeps its orientation
(superposition held to 8e-9); `UniformCurrent` 0 and 1 both conserve the terminal current through
every cross-section and differ by < 0.3 % in psi. This is the stage-1 rule "inflow and outflow faces
on the model's outer surface" confirmed for faces on the outer wall instead of a cut plane.

### 8.5 Torque

- Use the **nodal `Torque` condition** on the rotor parts (iron + magnets): `SetXYZPoint("Origin",
  0, 0, 0)`, `SetXYZPoint("Axis", 0, 0, 1)`, part selection (snippet S-3D-8). Result table key
  `Torque`, one column per condition, torque about +axis in N m. `ExpandTarget` 1 changed nothing (to
  all digits). A second condition on the stator parts gives about minus the rotor torque (load:
  rotor 1.906, stator -1.933 N m). VERIFIED (23.0, 2026-10-20).
- **`Torque_Surface` does not work for this**: a part selection is not accepted (`NumParts` reads 0)
  and Run aborts with "The target is not specified for the electromagnetic force or torque
  calculation condition"; selecting the internal mid-gap face by position picks both sides, whose
  contributions cancel (~1e-14 N m). VERIFIED negative (23.0, 2026-10-20).

### 8.6 Rotor positions: one STEP per angle

No `RotationMotion` in Static, so each rotor angle is its own model: write one merged STEP per angle
(stator + rotor rotated by theta + air, with the gap split into a stator-side and a rotor-side slab
for a gap mesh size), import, rename, set up and run it, then the next angle. Re-import per case is
the cheap verified route (~15 s overhead per case). Load cases: duplicate the no-load study
(`jmag_duplicate_study`) and set the coil currents from the dq currents at that angle.

Timing (Ryzen 7 3800X, 622 k elements, other load on the machine): ~80 s per no-load case (import +
set-up ~15 s, mesh ~30 s, solve ~40 s), ~160 s with a load study added; 13 cases ~20 min. The 311 k
element 180 deg sector solved in 15 s.

Mesh used: `AirRegion` 0, `AutoMeshSize` 0, air `MeshSize` 10 mm, `Part` sizes gap slabs 0.6,
magnets 1.5, steel 3, coils 3 mm. Going from 302 k to 622 k elements moved psi by +0.3 %.

### 8.7 Cogging is mesh-noise limited

Every angle gets a new mesh and nodal torque is a small difference of large forces: per angle JMAG
scattered by up to 0.035 N m against a sliding-mesh reference (0.33 N m peak-to-peak), and theta = 0
and 6 deg, which are the same position of this 12/10 machine (cogging period 6 deg), gave -0.005 and
+0.006 N m. Report peak-to-peak over a period, not single points, and refine the gap until
peak-to-peak settles (`jmag-experiments` -> `references/pmsm.md` section 2b).

### 8.8 Cross-validation numbers and traps

JMAG 23.0 vs an independent 3D FE solver (same geometry, same flux-parallel box): psi_A -0.2 to
-0.6 % over all angles; load torque within 0.3 %; cogging peak-to-peak 0.331 vs 0.332 N m (-0.3 %).

1. `StoredEnergy` in a model with magnets is not 1/2 sum(nu |B|^2 V): JMAG 3.82 J vs 9.27 J from the
   other solver, both constant to 0.1 % over the angles. Its definition with magnets is not in the
   help we have: **do not compare energies across tools when magnets are present**; compare psi and
   torque.
2. The reference's coil model matters: imposing a uniform current density |J| = N I / A_mean in a
   coil whose cross-section grows with radius over-stated psi and torque by ~10 %; JMAG's InflowFace
   current conserves N I through every section (as an electrokinetic coil-current solution does,
   which then agreed). Check the reference's coil model before blaming JMAG.
3. Leaving outer faces without a condition gives the natural (flux-normal) boundary, not `A_t = 0`.
4. Multiplying a periodic sector's `CoilFlux` by the symmetry factor double-counts (8.2).
5. `Torque_Surface` and the both-sides face selection (8.5).
6. `GetValue` of a property the condition lacks fails the call (8.1).
7. Geometry side: gmsh's `isInside(2, face, p)` ignores face trimming (a lead-end centre tested
   "inside" 5 faces); find small lead faces by bounding box and place boundary-face points on a
   coarse surface mesh, away from the face edges.
8. No modal dialog and no crash in the 2026-10-20 runs; every Designer quit at the end.
