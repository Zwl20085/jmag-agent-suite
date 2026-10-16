# Actuators, solenoids, linear devices, 3D models

Contents: 1 Force-stroke (solenoid) - 2 Force-current map - 3 Linear machines - 4 3D STEP models

**Status:** only the 3D import step (section 4) and the `Force` condition names
(`Force` / `Force_Surface` / `LorentzForce`, `references/properties.md` of `jmag-simulation`)
come from verified runs; the recipes below are standard practice with existing tools.

## 1. Force-stroke (solenoid)

1. Geometry: axisymmetric or planar template (`jmag-geometry`, section 7) with the stroke `x`
   as a script parameter. Regenerate the DXF per position and import each into its own model
   (or project): this is robust and keeps the mesh clean in the working gap.
2. Static study per position (`Static2D` or the AX variant): coil as `Current` condition with
   `N I`, `Force` condition on the plunger (`jmag_add_condition(condition_type="Force",
   parts=["Plunger"])`), `Energy: 1`, stack length for planar models.
3. Mesh: the working gap needs >= 3-4 element layers; refine with `jmag_mesh_size` and repeat
   one position at a finer mesh (force change < 2 %).
4. Read the force table (`jmag_list_results` shows its name), the stored energy and the coil
   flux linkage. Plot F(x) with `jmag_plot`.

Checks:
- Virtual work: `F ~ (W'(x + dx) - W'(x)) / dx` at constant current; linear case
  `F = 1/2 I^2 dL/dx` from `L(x) = psi(x) / I`. Within 5-10 % proves the force integration.
- Maxwell estimate at small gaps: `F ~ B_gap^2 A_pole / (2 mu0)` (about 398 kN/m^2 per T^2).
- Expect F rising steeply as the gap closes until the pole saturates; a force that keeps
  growing as `1/x^2` all the way to x = 0 means the steel never saturates (check the B-H curve
  goes high enough, `jmag_create_material`).

## 2. Force-current map

Cases over (x, I) with `jmag_case_table` (I as an equation, x as geometry -> one model per x).
Report F(x, I) and psi(x, I); the dynamic model of the actuator (circuit + spring/mass) is
built outside JMAG from these maps. A transient with plunger motion has no verified recipe
(the translation-motion condition is untested through the bridge).

## 3. Linear machines

Treat a linear PM machine like a rotating one cut open: periodic boundary over an even number of
poles at the ends (`RotationPeriodicBoundary` has a translational counterpart in JMAG -
**unverified on 23.0**, check `jmag_create_study(...)` output `condition_types`), force instead
of torque. Without a verified motion condition, sweep the mover position by geometry
regeneration (thrust vs position = cogging/detent and load thrust at fixed current angle).

## 4. 3D STEP models

`jmag_import_cad(path, model_name, unit="mm")` -> parts with volumes (compare with CAD!) ->
`jmag_rename_parts` by points or ids (CAD names come through when the script names solids) ->
`jmag_create_study(study_type="Static" | "Transient")`. 3D FEM coils need inflow faces; for
anything beyond the `Current` condition use `jmag_exec_python` with `Study.CreateCondition` and
face selections (`jmag_api_lookup("SelectFace")`). 3D runs are long: always use a small
`wait_seconds` + `jmag_run_status`, and estimate runtime from a coarse mesh first.

Model-size levers: symmetry planes (`SymmetryBoundary`), periodic sectors, and a 2D model
for everything that does not need end effects (`Model.ExtendTo3DMesh` exists in the 23.0 API
to extrude a 2D model - **unverified on 23.0** through the bridge).
