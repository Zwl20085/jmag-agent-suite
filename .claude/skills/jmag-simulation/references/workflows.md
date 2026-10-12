# Workflow recipes

Verified end-to-end on JMAG-Designer 23.0 through the MCP tools. Numbers in "expect" lines are
what the reference models produced, so you can tell normal from abnormal.

Contents: 1 Static inductor - 2 Circuit-driven coil (transient) - 3 Surface-PM motor -
4 3D STEP model - 5 Variants and sweeps - 6 Exporting for the engineer

---

## 1. Static inductor (Static2D, `Current` condition)

```
jmag_new_project(path="...\\ccore.jproj")
jmag_import_dxf(path="...\\ccore.dxf", model_name="ccore")
jmag_rename_parts(renames=[{"part":[5,30],"name":"Core"},{"part":[20,30],"name":"CoilIn"},{"part":[-5,30],"name":"CoilOut"}])
jmag_create_study(study_type="Static2D", name="MS")
jmag_set_material(study="MS", parts=["Core"], material="50JN400")
jmag_set_material(study="MS", parts=["CoilIn","CoilOut"], material="Copper", properties={"EddyCurrentCalculation":0})
jmag_set_properties(study="MS", values={"ModelThickness": 50, "Energy": 1})
jmag_add_current(study="MS", name="In",  parts=["CoilIn"],  turns=100, current=5, direction="up")
jmag_add_current(study="MS", name="Out", parts=["CoilOut"], turns=100, current=5, direction="down")
jmag_check(study="MS") -> jmag_run(study="MS")
jmag_get_result(study="MS", table="Magnetic Flux of Current Condition")   # psi per condition
jmag_physics_check(study="MS", check="energy")
```

L = (psi_In + psi_Out) / I. Expect 11.0 mH vs. analytic N^2 mu0 A/g = 9.42 mH (2D fringing +17 %);
energy identity within 1 %. `Energy: 1` turns on the stored-energy table needed for the check.

## 2. Circuit-driven coil (Transient2D, FEM coil)

```
jmag_create_study(study_type="Transient2D", name="TR")
... materials + ModelThickness as above ...
jmag_circuit_quick(study="TR", topology="current_source",
    coils=[{"name":"Coil1","turns":100,"resistance":0.1}],
    source={"type":"sin","amplitude":5,"frequency":50,"phase":0}, probe=true)
jmag_add_fem_coil(study="TR", name="Phase", component="Coil1",
    sides=[{"parts":["CoilIn"],"direction":"up"},{"parts":["CoilOut"],"direction":"down"}])
jmag_set_step(study="TR", steps=41, time_step=0.0005)
jmag_check(study="TR", electrical_frequency_hz=50) -> jmag_run(study="TR")
jmag_physics_check(study="TR", check="kvl", coil="Coil1", probe="VP_Coil1", resistance=0.1)
```

Expect psi/i at peak = static L within 0.1 %, KVL residual ~1e-7 (proves wiring + link).
`voltage_source` topology is the same with V instead of A; then current is the unknown.

## 3. Surface-PM motor (Transient2D, rotation, 3-phase)

Full script: `examples/spm_motor/run_mcp.py`. Essential steps after naming parts:

1. If an unnamed region remains between rotor and stator (the air gap the DXF closed), name it
   and `jmag_suppress_parts(parts=["GapAir"], study=S)`.
2. Iron `35JN210` (or the user's grade); magnets e.g. `N40H` with
   `orientation={"pattern":"Radial","outward": true|false}` alternating per pole; coils Copper with
   `EddyCurrentCalculation: 0`. `ModelThickness` = stack length.
3. `jmag_circuit_quick(topology="three_phase_current", coils=[CoilU, CoilV, CoilW], source={"type":"sin",
   "amplitude": I_peak, "frequency": f_e, "phase": phi})` then one `jmag_add_fem_coil` per phase with
   the go/return slot parts.
4. `jmag_add_condition(RotationMotion, parts=rotor parts, properties={"AngularVelocity": rpm})` and
   `jmag_add_condition(Torque, parts=rotor parts)`.
5. Steps: >= 60 per electrical period, 2+ periods; `f_e = rpm/60 * poles/2`.
6. Slide mesh: `jmag_set_properties(kind="mesh", values={"MeshType":1, "AutoSlide":1,
   "RadialDivision":4, "CircumferentialDivision": N})` with N a multiple of 360/(degrees per step).
7. Check, run (`wait_seconds` large or poll), then
   `jmag_physics_check(check="power_balance", speed_rpm=rpm, electrical_frequency_hz=f_e)` and
   `jmag_get_result(table="Coil Flux-Linkage", electrical_frequency_hz=f_e)`.

Expect (12s4p example, 10 A, 40 turns/phase, 50 mm): psi1 = 0.059 Wb, mean torque 1.774 Nm vs
3/2*p*psi1*I = 1.777 Nm, power balance 0.1 %, 6th-harmonic torque ripple ~3 Nm (single-layer
12s4p is ripple-rich), JMAG infos STEP_AUTO_UPDATED and TORQUE_AREA_EXTENDED.

Current phase: with the U axis on the q-axis at t=0, `phase=-90` gave motoring (+torque) and
`phase=+90` the same magnitude with negative sign. For an unknown machine, sweep the phase
(duplicate the study) and pick the maximum-torque angle; report the sign convention you found.

Open-circuit back-EMF: same study with `amplitude: 0` (or a duplicate) -> `Coil Flux-Linkage`,
EMF = -dpsi/dt; fundamental `E1 = omega_e * psi1`.

### 3b. Interior-PM (IPM) differences

Same flow as 3; not yet verified end-to-end through the bridge, so lean harder on the checks.
- Bar/V magnets: `orientation={"pattern": "Parallel", "direction": [cos a, sin a, 0]}` per magnet,
  `a` = the magnet's magnetization axis, sign alternating per pole (not `Radial`).
- Flux barriers/bridges are thin: give them local mesh sizes (`jmag_mesh_size`) and expect high B
  in the bridges (they saturate by design).
- Torque has a reluctance part, `T = 3/2 p (psi_m i_q + (L_d - L_q) i_d i_q)`, so maximum torque
  per amp needs `i_d < 0`: sweep the current phase over ~0..60 deg beyond the q-axis rather than
  assuming q-axis current is optimal.

## 4. 3D STEP model

`jmag_import_cad(path, model_name, unit="mm")` -> parts with volumes (compare with CAD!) ->
`jmag_rename_parts` by points or ids (CAD names come through when the script names solids) ->
`jmag_create_study(study_type="Static" | "Transient")`. 3D FEM coils need inflow faces; for
anything beyond the `Current` condition use `jmag_exec_python` with `Study.CreateCondition` and
face selections (`jmag_api_lookup("SelectFace")`). 3D runs are long: always use `wait_seconds`
small + `jmag_run_status`.

## 5. Variants and sweeps

- Mesh study: `jmag_duplicate_study` -> `jmag_mesh_size` on air gap / teeth -> rerun -> compare
  torque/inductance; < 1-2 % change means mesh-converged.
- Operating points: `jmag_set_equations(study, {"I": 10, "rpm": 1500})` and use the names as
  property values (`{"AngularVelocity": "rpm"}`), or duplicate studies per point.
- Large parametric sets (design table cases) are an advanced topic: `jmag_exec_python` with
  `study.GetDesignTable()` (`AddParameterVariableName`, `AddCase`, `SetValue`) and `RunAllCases`.

## 6. Exporting for the engineer

`jmag_export_results(study, out_dir)` writes one CSV per table (x column + data columns, units in
headers). `jmag_contour_image` / `jmag_circuit_image` produce PNGs. `jmag_journal(export_script=...)`
writes a replayable script of every state change - attach it to reports for traceability.
