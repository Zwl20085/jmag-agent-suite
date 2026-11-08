# 3D Static study: STEP model, coil excitation, symmetry, inductance (basics)

Part 1 of 3 of the 3D Static reference (index: `static-3d.md`). Sections 1-7. Rotating machines: `static-3d-rotating.md`; nonlinear steel and radial machines: `static-3d-nonlinear.md`.

Contents: 1 Status - 2 Coil excitation: which mode - 3 Workflow step by step - 4 Result keys - 5 Traps - 6 Cross-validation numbers - 7 Not verified yet

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
leads, the nodal `Torque` condition, `RotationPeriodicBoundary` and per-angle STEP cases: `static-3d-rotating.md` section 8.

**VERIFIED (23.0, 2026-10-20), third batch** = the same machine with nonlinear B-H steel (user B-H material,
nonlinear iteration / tolerance properties, iteration count from `jcgmntr`) and a loaded periodic sector that
fixes the sector torque factor at 1: `static-3d-rotating.md` sections 8.2 and 8.5, `static-3d-nonlinear.md` section 9.

## 2. Coil excitation: which mode

A 3D static study takes no circuit and no FEM coil. The `Current` condition is the coil, and its
`DirectionType` decides whether a closed winding carries any current.

| Winding | Use | Status |
|---|---|---|
| Closed ring / racetrack (any real transformer or inductor winding) | **half model** cut through the winding axis, `SymmetryBoundary` on the cut plane, `Current` with `DirectionType 0` (InflowFace), one end face of the U-shaped half winding as inflow face | VERIFIED (23.0, 2026-10-19) |
| Same winding, `DirectionType 1` (Vector) with `Direction` / `Origin` | do not use: a uniform current direction that a closed loop cannot carry; the run finishes, no error, flux linkage ~2e-20 Wb | VERIFIED negative (23.0, 2026-10-19) |
| Winding slit (C shape) with both ends inside the air | fails at Run: "Cannot find flow-out face of FEM coil (or Current condition). Case 1: flow-out face is not set on Symmetry boundary (3D). Case 2: ... Periodic boundary. Case 3: Duplicated nodes" | VERIFIED negative (23.0, 2026-10-19) |
| Closed tooth coil of a rotating machine (full or sector model) | slit the coil (0.5 mm) and run two leads to the outer wall of the air box; `SymmetryBoundary` on every outer face incl. the two lead end faces; InflowFace = one lead end face (`static-3d-rotating.md` section 8.4) | VERIFIED (23.0, 2026-10-20) |
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
| `StoredEnergy` | one per part + `Total` | needs `Energy = 1`; full-model value in a periodic sector; with magnets it is not 1/2 sum(nu B^2 V) (`static-3d-rotating.md` section 8.8) | VERIFIED (23.0, 2026-10-19 / 2026-10-20) |
| `Torque` | one per `Torque` condition, one row | nodal-force torque about the condition's axis, N m (`static-3d-rotating.md` section 8.5) | VERIFIED (23.0, 2026-10-20) |

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
`FullModelConversion` / `ConversionFactor` at non-default values (the defaults 1 / 1 were read and left
unchanged; nonlinear B-H and the sector torque conversion are verified since 2026-10-20, `static-3d-rotating.md` sections 8.2
and 9); a coil cut by a periodic boundary; `RotationPeriodicBoundary` `Normal` / `AxialReverse`; `CurrentDensity`;
`Torque_Surface` on an external face; rotor motion inside one Static study (no `RotationMotion`:
rotor positions are separate cases).
