# PM synchronous machines (SPM, IPM; SynRM where noted)

Contents
1. Base study: surface-PM motor recipe (verified on 23.0) and IPM differences
2. Cogging torque
3. Open-circuit back-EMF
4. Load point
5. Inductance, Ld / Lq
6. Torque vs current angle, MTPA
7. Torque-speed envelope

---

## 1. Base study: surface-PM motor (Transient2D, rotation, 3-phase)

Verified end-to-end on JMAG-Designer 23.0 through the MCP tools; full script
`examples/spm_motor/run_mcp.py`. Essential steps after naming parts:

1. If an unnamed region remains between rotor and stator (the air gap the DXF closed), name it
   and `jmag_suppress_parts(parts=["GapAir"], study=S)`.
2. Iron `35JN210` (or the user's grade); magnets e.g. `N40H` with
   `orientation={"pattern": "Radial", "outward": true|false}` alternating per pole; coils Copper
   with `EddyCurrentCalculation: 0`. `ModelThickness` = stack length.
3. `jmag_circuit_quick(topology="three_phase_current", coils=[CoilU, CoilV, CoilW],
   source={"type": "sin", "amplitude": I_peak, "frequency": f_e, "phase": phi})`, then one
   `jmag_add_fem_coil` per phase with the go/return slot parts (or `jmag_winding` from a plan).
4. `jmag_add_condition(RotationMotion, parts=rotor parts, properties={"AngularVelocity": rpm})`
   and `jmag_add_condition(Torque, parts=rotor parts)`.
5. Steps: >= 60 per electrical period, 2+ periods; `f_e = rpm/60 * poles/2`.
6. Slide mesh: `jmag_set_properties(kind="mesh", values={"MeshType": 1, "AutoSlide": 1,
   "RadialDivision": 4, "CircumferentialDivision": N})` with N a multiple of
   360/(degrees per step) (and `AutoDivision: 0` so N is used - see jmag-gotchas).
7. Check, run (`wait_seconds` large or poll), then
   `jmag_physics_check(check="power_balance", speed_rpm=rpm, electrical_frequency_hz=f_e)` and
   `jmag_get_result(table="Coil Flux-Linkage", electrical_frequency_hz=f_e)`.

Expect (12s4p example, 10 A, 40 turns/phase, 50 mm): psi1 = 0.059 Wb, mean torque 1.774 Nm vs
`3/2 * p * psi1 * I` = 1.777 Nm, power balance 0.1 %, 6th-harmonic torque ripple ~3 Nm
(single-layer 12s4p is ripple-rich), JMAG infos STEP_AUTO_UPDATED and TORQUE_AREA_EXTENDED.

Current phase: with the U axis on the q-axis at t = 0, `phase = -90` gave motoring (+torque)
and `phase = +90` the same magnitude with negative sign. For an unknown machine, sweep the phase
and pick the maximum-torque angle; report the sign convention you found.

### 1b. Interior-PM (IPM) differences

Same flow; **not yet verified end-to-end through the bridge**, so lean harder on the checks.
- V / bar magnets: `orientation={"pattern": "Parallel", "direction": [cos a, sin a, 0]}` per
  magnet, `a` = that magnet's magnetisation axis, sign alternating per pole (not `Radial`).
- Pockets and barriers are parts: `Air`, moving with the rotor (`jmag-geometry` templates).
- Bridges are thin: local mesh sizes (`jmag_mesh_size`); they saturate by design.
- Torque has a reluctance part, `T = 3/2 p (psi_m i_q + (L_d - L_q) i_d i_q)`, so MTPA needs
  `i_d < 0`: sweep the current angle 0..60 deg beyond the q-axis (section 6).

## 2. Cogging torque

One call: `jmag_experiment(kind='cogging', study=S, params={"slots": Q, "poles": 2p})`
(defaults 60 rpm, 90 steps over one cogging period). Manual equivalent:
1. Duplicate the validated study. Zero current: source `amplitude: 0` (FEM coils stay, i = 0),
   magnets with `EddyCurrentCalculation: 0` so speed does not matter.
2. Span one cogging period `360 / LCM(Q, 2p)` mech deg (two for a periodicity check) with
   >= 30 steps per period; keep `CircumferentialDivision` a multiple of 360 / step angle.
   Example 12s4p: period 30 deg; 60 steps -> 0.5 deg/step -> `CircumferentialDivision` 720.
   Time step at speed n rpm: `dt = step_angle / (6 n)` seconds.
3. Read `Torque`; report pk-pk, the dominant order and the mean.

Expect: fundamental at `LCM(Q, 2p)` per revolution; mean ~ 0 (no net work over a period). Typical
pk-pk: integer-slot q = 1 without skew 3-10 % of rated torque; well-chosen fractional-slot
(high LCM) < 1-2 %. Cogging is a small difference of large forces: do a mesh study (refine the
gap, < 5 % change in pk-pk) before believing it.

### 2b. 3D Static per-angle cases (cogging, torque, psi)

`jmag_experiment` works on a 2D transient study. For a machine that needs 3D (axial flux, end
effects) use 3D Static cases instead, one rotor angle each (`jmag-simulation` ->
`references/static-3d.md` section 8; VERIFIED on 23.0, 2026-10-20, 12-slot / 10-pole axial-flux PM):
1. One merged STEP per angle (stator + rotated rotor + air, gap split into stator / rotor slabs);
   >= 6 angles per cogging period plus the period end as a repeat check.
2. Per case: import, rename, materials (magnets: S-3D-7), `SymmetryBoundary` on every outer face
   or a `RotationPeriodicBoundary` sector (S-3D-6), slit coils with leads + `Current` (S-3D-2),
   nodal `Torque` on the rotor (S-3D-8; not `Torque_Surface`), mesh, run, read `Torque` and
   `CoilFlux`. Load: duplicate the study, currents from dq at that angle.
3. psi_phase = sum polarity x `CoilFlux`; back-EMF from d psi / d theta x omega across the cases.

Expect ~80 s per no-load case at ~620 k elements. Each angle has its own mesh, so cogging carries
mesh noise (+-0.035 N m on 0.33 N m pk-pk there; the two ends of a period differed by 0.011 N m):
report pk-pk over the period and the end-of-period mismatch as the noise level. Achieved vs an
independent 3D FE: psi 0.2-0.6 %, load torque 0.3 %, cogging pk-pk 0.3 %. With nonlinear B-H steel
(`static-3d.md` section 9: user B-H material, `NonlinearMaxIteration` / `NonlinearTolerance`,
iterations from `jcgmntr`): about 9 iterations, mesh 30-47 s + solve 120-200 s per case at 622 k
elements; psi 0.2-0.6 %, load torque 0.6 % at 5 A and 15 A, cogging pk-pk 0.03 %. A 180 deg periodic
sector's `Torque` is the full-model value (factor 1). Arbitrary (off-grid) rotor angles: the same
route, one STEP per angle (`static-3d.md` section 8.9; VERIFIED 2026-10-20 at 0.7 / 1.3 / 2.9 deg:
psi 0.6 %, load torque 0.3-0.7 %, cogging within 0.019 N m of an independent 3D FE with a mortar
sliding interface; the off-grid points lie on the cubic through JMAG's own 1 deg points to
0.007 N m, which is this route's measured noise).

### 2c. 2D Static per-angle cases from rotated DXFs (radial machine)

When a quick static check of a radial machine is wanted without a transient (cross-check of psi and
torque at a few rotor angles), use one 2D Static study per angle. VERIFIED on 23.0, 2026-10-20, on a
12-slot / 10-pole radial SPM cross-section (linear steel, B_r 1.2 T magnets, 30 turns per coil
side) against an independent solver:
1. Geometry (`jmag-geometry`): one **full** cross-section DXF per rotor angle with the rotor turned
   in the geometry, plus its labels JSON (no motion in a Static study).
2. `jmag_new_project`, `jmag_import_dxf`, `jmag_rename_parts` from the labels (check areas; dedupe
   repeated names first, below), `jmag_create_study(study_type="Static2D")`.
3. Materials: steel `linear_2000` (or the grade); magnets with pattern `Radial`, origin (0, 0, 0)
   and `SetOrientation` outward / inward alternating per pole (the run used the custom Linear(Br)
   magnet of `static-3d.md` 8.3 through `jmag_exec_python`); coils `Copper`; the rest `Air`.
4. One `jmag_add_current` per coil side (`turns`, `direction` up / down from the winding table);
   load study = `jmag_duplicate_study` + `jmag_set_properties(kind="condition")` `Current` = i_phase
   from the dq currents at that angle.
5. `SymmetryBoundary` (A_z = 0) on the edges of the outer stator circle and of the bore circle
   (`SelectEdgeByPosition` in `jmag_exec_python`); `ModelThickness` = stack; mesh control
   `AirRegion` 0 (the 2D mesh control has it and accepts 0), `AutoMeshSize` 0, `MeshSize` + part
   sizes; `Energy` 1.
6. Nodal `Torque` on the rotor parts (`Origin` 0, `Axis2D` 0); after the run read `Torque` and
   `CoilFlux` by internal key; psi_phase = sum of `CoilFlux` over the up and down sides of the phase
   (each condition's flux follows its own direction).

Expect 5 s mesh + 2 s solve per study at 20 k elements. Agreement with the independent solver at
matched mesh sizes: psi 0.03 %, load torque 0.3 %. **Cogging is not converged at that size**:
going from 20 k to 101 k elements moved the cogging peaks by 30 % while psi moved 0.3 %, so run a
mesh study before quoting cogging (section 2). Measured on that machine (VERIFIED, 23.0, 2026-10-20;
peak-to-peak T(1.5 deg) - T(4.5 deg), period 6 deg):
- Refine the **tooth-tip corners at the slot opening together with the gap**. With the steel /
  slot-air / coil part size held at 1.5 mm, refining the gap and magnet elements 0.5 -> 0.03125 mm
  settled at 1.34 N m/m (19 k -> 1.3 M elements); refining the steel / slot air / coils as well
  (0.5, 0.25, 0.125 mm) took it to 1.28, 1.21, 1.18 and, with the gap at 0.03125 mm, 1.15 N m/m
  (3.1 M elements). An independent solver converged to within 5 % of that at the same sizes; the
  value a psi / torque-tuned mesh gives can be 80 % high.
- Each level is a part-size change only (`Part` mesh conditions); the zero-cogging angles (0, 3,
  6 deg here) stayed below 0.06 N m/m at every level, a cheap noise check.
- Cost per study: 2-3 s at 20 k elements, 130-190 s mesh + 80-120 s solve at 1.3 M, about 250 s + 185 s at
  3.1 M (Ryzen 7 3800X): for the finest levels solve only the two extreme angles. Benign warning: "Check if there are one or more
independent models that are surrounded by the symmetry boundaries" for a source-free bore disc
whose whole boundary is `SymmetryBoundary` (its field is zero). Labels trap: a part straddling
theta = 0 at a rotated angle can appear twice with the same name; rename the second `<name>__2`
before `jmag_rename_parts` and keep it in the same material and rotor group.

## 3. Open-circuit back-EMF

One call: `jmag_experiment(kind='back_emf', params={"poles": 2p, "rpm": n})`. Manual: same study with source
`amplitude: 0` (or a duplicate) at speed -> `Coil Flux-Linkage`, EMF = -dpsi/dt; fundamental
`E1 = omega_e * psi1`. With a voltage probe, the probe voltage equals dpsi/dt (i = 0), so
`jmag_physics_check(check='kvl', resistance=R)` proves the probe and link.

Report: phase and line-line fundamental (peak and rms), THD, harmonic table, `Ke = E1 / omega_m`.
Line-line = sqrt(3) x phase for a balanced star; triplen harmonics cancel line-line.
Expect: E1 within 5-10 % of `omega_e kw1 N_ph 2 B_1 r L / p`; SPM with full-pitch windings
shows strong 5th/7th; a trapezoidal EMF is normal for wide magnets.

## 4. Load point

One call: `jmag_experiment(kind='load_point', params={"current_peak": I, "phase_u_deg": phi,
"rpm": n})` (`phase_u_deg` is the 3-phase source's `PhaseU`; the current angle it gives depends
on the rotor position at t = 0), or the base study itself. Report mean
torque, ripple pk-pk and % of mean (`ripple_pct_of_mean`), dominant ripple orders (6k for
3-phase), flux-linkage fundamental, power balance, and `T` vs `3/2 p psi_1 I` (SPM) or the dq
formula (IPM). Ripple sources: cogging + MMF/permeance harmonics; ripple orders not multiple of
6 point at an unbalanced winding or a wrong phase sequence.

## 5. Inductance, Ld / Lq

- One call: `jmag_experiment(kind='inductance_static', params={"current_peak": I, "phases":
  {"U": [...], "V": [...], "W": [...]}, "d_axis_elec_deg": theta_d})` on a Static study with
  `Current` conditions per phase side: three solves (0, d, q) -> Ld, Lq, psi_pm, saliency.
- Manual, from a transient load point: Park-transform the phase flux linkages and currents
  (`jmag-machine-design` formulas, section 6) at each step using the rotor angle
  `theta_e = p * (InitialRotationAngle + 6 n t) - theta_0` deg (`theta_0` = electrical angle
  between phase U's axis and the d-axis at zero rotation; check it on the open-circuit run:
  `psi_q ~ 0` there), then `L_q = psi_q / i_q`,
  `L_d = (psi_d - psi_m) / i_d`, with `psi_m` from the open-circuit run at the same angle.
  Needs a non-zero `i_d` (set the current angle); at `i_d = 0` L_d is undefined.
- These are apparent inductances that include cross-saturation in `psi_m`; the exact split
  needs frozen permeability, which no bridge tool exposes (**unverified on 23.0** whether the
  API can).
- Expect: SPM `L_d ~ L_q` (within ~10 %), IPM `L_q / L_d` 1.5-4, SynRM 4-10. `L_d` and `L_q`
  fall with current (saturation); report the operating point.

## 6. Torque vs current angle, MTPA

1. Duplicate the load-point study. Add the source phase to the design table:
   `jmag_case_table(action='set', add_parameters=['<source name>: PhaseU'])` (full names look
   like `'Source3Ph (3PhaseCurrentSource): Amplitude'`; `candidates='PhaseU'` lists them), or
   drive `PhaseU` from an equation (`jmag_set_equations`), which joins the table by itself.
2. Append one case per angle (`cases=[{...: phi}, ...]`, 0..90 deg of current angle in 10-15 deg
   steps, refined around the peak), then `jmag_run_cases(study)` (method `loop`, verified) and
   read each case with `jmag_get_result(..., case=k)`.
3. Plot mean T vs beta (`jmag_plot`). SPM peaks at beta ~ 0 (pure q-axis); IPM peaks at
   beta = 20-45 deg (i_d < 0); SynRM near 45-60 deg with the SynRM axis convention.
4. Repeat for several current magnitudes -> the MTPA trajectory; cross-check one point with the
   dq torque formula.

## 7. Torque-speed envelope

- Analytic from the FEA parameters: take `psi_m`, `L_d(i)`, `L_q(i)` (or a full flux map
  psi_dq(i_d, i_q) from a `jmag_run_cases` grid) and apply the current limit `I <= I_max` and
  the voltage limit `omega_e sqrt((L_q i_q)^2 + (psi_m + L_d i_d)^2) <= V_dc/sqrt(3)`; below
  base speed follow MTPA, above it field-weaken (formulas in `jmag-machine-design`).
- JMAG's own route is the efficiency map (`jmag_efficiency_map`; it also yields the NT curve).
  The map's control options (`ControlName`: mtpa, mtpa+fw, unitypf, id=0) exist in the 23.0 API;
  the end-to-end run is **unverified on 23.0** (`jmag-losses-efficiency`).
- Validate 2-3 envelope points with full transient load points at that speed and current; a
  > 5 % torque mismatch means the flux map is too coarse or saturation was linearised.
