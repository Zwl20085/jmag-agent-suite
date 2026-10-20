# Inductors, transformers, coupled coils

Contents
1. Static inductor (Static2D, Current condition) - verified
2. Circuit-driven coil (Transient2D, FEM coil) - verified
3. Inductor L(I) saturation curve
4. Transformer open-circuit and short-circuit tests
5. Coupled coils / wireless power: L1, L2, M, k
6. 3D static transformer (Static, half model, `Current` InflowFace) - verified

---

## 1. Static inductor (Static2D, `Current` condition)

Verified end-to-end on JMAG-Designer 23.0 (this is the `jmag_self_test` model).

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

`L = (psi_In + psi_Out) / I`. Expect 11.0 mH vs analytic `N^2 mu0 A / g` = 9.42 mH (2D fringing
+17 %); energy identity within 1 %. `Energy: 1` turns on the stored-energy table the check needs.
`jmag_experiment(kind='inductance_static')` wraps this pattern.

## 2. Circuit-driven coil (Transient2D, FEM coil)

Verified end-to-end on JMAG-Designer 23.0.

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

Expect psi/i at peak = static L within 0.1 %, KVL residual ~1e-7 (proves wiring and link).
The `voltage_source` topology is the same with V instead of A; then current is the unknown.

## 3. Inductor L(I) saturation curve

1. Static study from section 1; make the current an equation (`jmag_set_equations(..., {"I": 1})`,
   `Current: "I"` on both conditions).
2. Cases I = 0.1 ... 3 x rated (log-spaced near the knee) with `jmag_case_table` +
   `jmag_run_cases`.
3. Report apparent `L = psi / I` and incremental `L_inc = dpsi/dI` (finite difference between
   neighbouring cases) vs I. Expect flat L while the gap dominates, then a drop where the core
   saturates; `L_inc` falls first. Energy check on 2-3 points.
4. DC-bias inductors: the ripple inductance is `L_inc` at the bias current, not the apparent L.

## 4. Transformer open-circuit (OC) and short-circuit (SC) tests

Manual recipes (the OC/SC tests are not verified through the bridge yet; the 3D static L matrix
of a transformer is, section 6).

- **OC (Transient2D):** primary FEM coil from `voltage_source` at rated voltage and frequency,
  secondary FEM coil linked to a high-value resistor (open) via `jmag_circuit_add`. Run 3-5
  periods (the DC offset of the magnetising current decays). Measure magnetising current
  (non-sinusoidal when saturated), core flux density (`V_rms = 4.44 f N A_c B_pk` check), and
  the secondary voltage -> turns ratio. Core loss needs a loss study (`jmag-losses-efficiency`).
- **SC, static shortcut:** Static2D, two `Current` conditions with ampere-turn balance
  `N1 I1 = N2 I2` in opposite directions, `Energy: 1`. Leakage inductance referred to the
  primary `L_sigma = 2 W / I1^2` (the core carries almost no flux; check it on a contour).
- **SC, transient:** secondary shorted through a small resistor, primary driven by a current
  source at rated current; `L_sigma` from `V / (omega I)` after removing `R`; copper loss
  `I^2 R` per winding (AC loss in thick conductors needs FEM conductors, see
  `jmag-losses-efficiency`).
- Expect: magnetising current 0.5-5 % of rated for power transformers (more for gapped or
  ferrite designs); per-unit leakage reactance 2-10 %.

## 5. Coupled coils / wireless power: L1, L2, M, k

- Coaxial circular coils are axisymmetric: an AX study (`StaticAX`, listed by
  `jmag_create_study`) is exact and fast; misalignment needs 3D. Axis convention of AX
  studies is **unverified on 23.0** (see `jmag-geometry` templates, section 7).
- Static method: coil 1 excited (`Current`), coil 2 carrying 0 A, `Energy: 1`:
  `L1 = psi_1 / i_1`, `M = psi_2 / i_1`; swap for `L2` and `M21`. In a 3D Static study a 0 A
  `Current` condition still reports its flux (internal key `CoilFlux`, verified 23.0,
  2026-10-19); in 2D (`Magnetic Flux of Current Condition`) this is **unverified**; if it is
  missing, give coil 2 a tiny current and subtract its self term.
- `k = M / sqrt(L1 L2)`. Invariants: `M12 = M21` within 1 %, `0 <= k < 1`, energy
  `W = 1/2 (L1 i1^2 + L2 i2^2) + M i1 i2` with both coils excited.
- Ferrite pads and shields: at the operating frequency (tens of kHz) add eddy currents in
  conductive shields with a Frequency study; ferrite loss needs loss data (`jmag-losses-efficiency`).
- Expect: k 0.1-0.4 for typical air-gapped pads, rising steeply as the gap shrinks.

## 6. 3D static transformer (Static, half model, `Current` InflowFace)

Verified end to end on JMAG-Designer 23.0 on 2026-10-19 (E55/28/21 with a 1 mm centre-leg gap and
a P66/56 pot core, linear cores). Full step list, snippets and traps: `jmag-simulation` ->
`references/static-3d.md`. The essentials:

1. Geometry: **half model** cut by a plane through the winding axis (y = 0), one named solid per
   region including the air, mm (`jmag-geometry`; check with `python -m jmag_bridge.cad3d`). Each
   winding becomes a U whose two end faces lie on the cut plane.
2. `jmag_import_cad` -> `jmag_rename_parts` with `[x, y, z]` points -> volumes vs CAD.
3. `jmag_create_study(study_type="Static")`; materials (`linear_<mu_r>` or `jmag_create_material`
   with a linear B-H table); `Energy = 1`.
4. One `Current` condition per winding: `Turn`, `Current`, `DirectionType 0`, `UniformCurrent 1`,
   inflow face = one end face on the plane (snippet S-3D-2). Not `DirectionType 1`: zero current.
5. `SymmetryBoundary` on every face of the cut plane and the outer air faces (S-3D-3); mesh with
   `AirRegion 0` and part sizes (S-3D-4).
6. Study 1: primary 1 A, secondary 0 A; `jmag_duplicate_study` -> study 2 with 0 A / 1 A.
7. Mesh, run, read `CoilFlux` and `StoredEnergy` (S-3D-5). Device `L_jk = 2 psi_j / I_k`,
   `W = 2 StoredEnergy.Total`.

Invariants to report: `M12 / M21 - 1` and `W / (L I^2 / 2) - 1` (both < 1.3e-10 on 2026-10-19),
`0 <= k < 1`, and a hand estimate (E core: gap reluctance with McLyman's fringing factor came within
0.3 % of the fine-mesh JMAG L11, without fringing 18 % low). Expected accuracy of a fine mesh: L
within 0.5-0.9 % of an independent 3D FE solver; L converges from below as the mesh is refined, so
report two mesh levels.
