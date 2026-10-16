---
name: jmag-experiments
description: Standard virtual tests in JMAG-Designer through the jmag MCP tools - cogging torque, open-circuit back-EMF, Ld/Lq and inductance, load point and torque vs current angle, torque-speed envelope, induction-machine no-load and locked-rotor, transformer open/short-circuit, inductor L(I), solenoid force-stroke and coupled-coil (wireless power) coupling factor. Gives setup, step/period rules, sign conventions, expected ranges and the invariant to report for each, and says which are one call (jmag_experiment) and which are manual recipes. Use when the user asks for any of these tests or for "the usual characterisation" of a motor, inductor, transformer or actuator.
---

# Standard experiments

A test is only finished when its number comes with (a) the setup that produced it, (b) the
invariant that proves it, and (c) a hand estimate (`jmag-machine-design`). Build and debug the
underlying study with `jmag-simulation` first; run tests on **duplicates**
(`jmag_duplicate_study`) so the base study stays clean.

## Which tests exist and how to run them

| Test | Device | Route | Details |
|---|---|---|---|
| Cogging torque | PM machines | `jmag_experiment(kind='cogging')` | `references/pmsm.md` 2 |
| Open-circuit back-EMF | PM / wound field | `jmag_experiment(kind='back_emf')` | `references/pmsm.md` 3 |
| Load point (T, ripple, psi, power balance) | PM, SynRM | `jmag_experiment(kind='load_point')` | `references/pmsm.md` 4 |
| Static inductance / Ld, Lq at a locked angle | any | `jmag_experiment(kind='inductance_static')` | `references/pmsm.md` 5, `references/transformer-inductor.md` 1 |
| Torque vs current angle, MTPA | PM, SynRM | manual: duplicate + `jmag_run_cases` over the phase | `references/pmsm.md` 6 |
| Torque-speed envelope | PM, SynRM | manual: flux map + voltage limit, or `jmag_efficiency_map` | `references/pmsm.md` 7 |
| No-load, locked rotor, slip sweep | induction | manual recipes | `references/induction.md` |
| Inductor L, L(I) saturation | inductors | manual (Static2D + Current) | `references/transformer-inductor.md` 1-3 |
| Open / short circuit | transformers | manual | `references/transformer-inductor.md` 4 |
| Coupling factor k, M | WPT / coupled coils | manual | `references/transformer-inductor.md` 5 |
| Force-stroke, force-current | solenoids, actuators | manual (geometry per position) | `references/actuator.md` |
| 3D models | any | manual | `references/actuator.md` 4 |

`jmag_experiment(kind, study, params)` duplicates a working study (`<study>_<kind>`), changes
only what the test needs (everything read back), solves and returns named metrics with the
matching invariant:
- `cogging` {slots, poles, rpm? (60), steps_per_span? (90)}: zero current, one cogging period.
- `back_emf` {poles, rpm, periods?, steps_per_period?}: zero current; psi1, E1 = omega_e psi1,
  THD, phase imbalance, E = omega psi vs `VP_<coil>` probes.
- `load_point` {current_peak, phase_u_deg, rpm, periods? (2), steps_per_period? (60)}: torque
  mean / pk-pk / ripple over the last period + power balance.
- `inductance_static` on a Static study with `Current` conditions: {current_peak, conditions}
  -> L; or {current_peak, phases: {U: [...], V: [...], W: [...]}, d_axis_elec_deg} -> Ld, Lq,
  psi_pm, saliency.
Pass `estimate={'kind': ..., 'params': {...}}` to attach the `jmag_estimate` expectation; if the
solve outlives `wait_seconds`, call again with `action='analyze'`. The tool description states
VERIFIED/UNVERIFIED per kind - repeat that status in your report. If it refuses, the error names
the missing prerequisite - fix the base study, do not force the test.

## Rules shared by all tests

- **Steps and periods.** `f_e = rpm/60 * p`. >= 60 steps per electrical period for EMF/torque,
  >= 20-30 samples per cogging period, 2+ periods for anything with a circuit (the first period
  carries the start-up transient). Use the time axis JMAG returns (STEP_AUTO_UPDATED is normal).
- **Stack length.** Set `ModelThickness` before any test; every 2D number scales with it.
- **One variable at a time.** A test changes only its own variable (angle, current, speed,
  position); everything else is copied from the validated base study.
- **Steady state.** Read the last full period (`jmag_get_result(electrical_frequency_hz=f)`
  returns `last_period` and `periodicity.steady`); NOT_STEADY means run longer, not average more.
- **Mesh.** A test that compares small differences (cogging, ripple, Ld - Lq) needs a mesh
  study first: refine the gap, rerun, change < 1-2 % (`jmag-parametric-optimisation`).

## Sign conventions (state them in every report)

- Torque sign follows the Torque condition axis and the rotation direction of RotationMotion;
  verified on the 12s4p SPM: with phase U's axis on the q-axis at t = 0, source `phase = -90`
  gave motoring (+torque), `phase = +90` the same magnitude negative. For a new machine sweep
  the phase and report the convention you found.
- Back-EMF `e = -dpsi/dt` (generator convention) vs terminal voltage `v = R i + dpsi/dt`
  (motor convention, what JMAG's voltage probe and the KVL check use).
- Current direction `up` = +z out of the 2D plane; go and return sides opposite.
- dq: d-axis on the magnet (PM) or on the high-permeance path (SynRM); say which.

## What to report for each test

Setup (study, mesh size, steps, speed, current, stack), the metric with its source table and
column, the invariant result, the hand estimate and ratio, the verdicts (PASS/CHECK/FAIL with
codes) and open doubts. `jmag_plot` for waveforms/spectra, `jmag_make_report` for the bundle
(`jmag-postprocess`).

## Invariants per test (minimum)

| Test | Invariant | Normal |
|---|---|---|
| Cogging | mean over one cogging period ~ 0; period = 360/LCM(Q,2p) mech | |mean| < 5 % of pk-pk |
| Back-EMF | `E_1 = omega_e psi_1`; probe voltage = dpsi/dt at i = 0 (KVL) | < 1 %; KVL ~1e-7 |
| Load point | power balance `<sum i dpsi/dt> = <T> omega` | < 1-5 % |
| Load point | `T` vs `3/2 p (psi_d i_q - psi_q i_d)` | < 2-5 % |
| Inductance (static) | energy `W = 1/2 sum i psi` | < 1 % linear, few % saturated |
| Transformer / coupled coils | `M12 = M21`, `k <= 1` | reciprocity < 1 % |
| Force-stroke | `F ~ dW'/dx` from the energy at two positions | < 5-10 % |
