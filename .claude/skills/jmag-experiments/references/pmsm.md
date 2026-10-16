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
