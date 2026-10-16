# Induction machines

Contents: 1 Base study - 2 No-load test - 3 Locked-rotor test - 4 Slip sweep / torque-speed -
5 Checks

**Status: no induction-machine recipe has been verified end-to-end through the bridge on 23.0.**
The tools used (FEM conductors, circuits, rotation, Frequency2D studies) exist and are verified
individually on other devices; the combination below is standard practice. Lean on the checks.

## 1. Base study

- Geometry: cage template in `jmag-geometry` (bars `Bar<k>`, closed or semi-closed slots).
- Bars: aluminium or copper with eddy currents on; `jmag_add_fem_conductor` linked to circuit
  `FEMConductor` components. Stator: stranded FEM coils (`EddyCurrentCalculation: 0`).
- End rings: not in the 2D cross-section; represent them in the circuit as ring-segment
  resistors (and small inductors) between adjacent bar conductors (`jmag_circuit_add`), or let
  JMAG's cage/winding settings build them (`Study.CreateWinding`, manual chapter "Automate Coil
  Windings/Cage Settings" - **unverified on 23.0**). Verify the cage with `jmag_circuit_image`:
  every bar connected to both rings, no floating node.
- Stator supply: `three_phase_voltage` (realistic, slow to settle) or `three_phase_current`
  (fast, fixes the magnetising state).
- Time constants: the rotor time constant `L_r / R_r` is 0.05-0.5 s; a transient from rest at
  speed needs several of them to settle. Plan the step count accordingly or start from a
  Frequency2D solution where possible.

## 2. No-load test (s ~ 0)

- Rotor at synchronous speed (`AngularVelocity = 60 f / p`) or a very small slip; rated voltage.
- Measure: phase current (mostly magnetising), flux linkage, gap B, iron loss (loss study).
- Derive: `X_m ~ V1 / I0` (minus `X1`), magnetising inductance; compare with
  `L_m = (3 mu0 D L / (pi g_e)) (kw1 N_ph / p)^2` (`jmag-machine-design`).
- Expect: `I0` 25-50 % of rated current (higher for small or high-pole machines); bar currents
  near zero.

## 3. Locked-rotor test (s = 1)

- Rotor fixed (no RotationMotion, or speed 0); rated frequency; reduced voltage or rated current.
- Natural fit for a **Frequency2D** study (sinusoidal steady state at one frequency, eddy
  currents in bars included) - fast. A Transient2D run must cover several electrical periods.
- Measure: impedance `Z = V / I`, `R_lr` from input power, `X_lr`; bar current density
  distribution (deep-bar effect: current crowds to the bar top).
- Derive: `R2'` and `X1 + X2'`. Expect: starting current 5-8 x rated, power factor 0.3-0.5.

## 4. Slip sweep / torque-speed

- Cases over slip (speed = (1 - s) n_s), constant voltage or current; `jmag_case_table` +
  `jmag_run_cases`; read mean torque over the last supply period after the rotor transient.
- Whether a Frequency2D study can impose slip (rotor quantities at slip frequency) through
  study or condition properties is **unverified on 23.0**: list the properties with
  `jmag_get_properties(with_help=true)` and `jmag_docs_search("slip")` before relying on it.
- Expect: breakdown torque 2-3 x rated at slip 0.1-0.3 (small machines higher slip), linear
  region near s = 0 with `T ~ s`.

## 5. Checks

- Torque from the air-gap power: `T = P_gap / omega_s`, `P_gap = P_rotor_cu / s` (bar Joule
  loss from the `Joule Loss` table). Agreement within a few % proves bars and rings.
- Power balance: stator input = gap power + stator copper + iron loss.
- Bar currents sum to zero per pole pair (balanced cage); ring current `I_ring = I_bar /
  (2 sin(pi p / Q_r))`.
