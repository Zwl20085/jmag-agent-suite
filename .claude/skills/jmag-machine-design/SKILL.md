---
name: jmag-machine-design
description: Analytic sizing and plausibility checks for electrical machines and magnetic devices before and after JMAG FEA - air-gap flux density, electric loading and shear stress, winding factor, Kt/Ke, Ld/Lq, slot fill, current density limits, typical tooth/yoke flux densities per steel, and slot/pole combination choice (winding factor, cogging order, periodicity, unbalanced magnetic pull). Use when the user asks to size a motor, choose slots/poles or a winding, check whether a design or an FEA result is plausible, or needs a hand estimate to validate JMAG numbers; pairs with jmag_estimate and jmag_winding_plan.
---

# Machine design and plausibility

FEA answers "what does this geometry do"; it does not say whether the answer is plausible.
This skill gives the hand numbers that make rung 5 ("Engineering") of the feedback ladder in
`jmag-simulation` concrete, and the design rules that keep a model from being nonsense before
it is solved.

## When to use which tool

| Need | Tool | Notes |
|---|---|---|
| Unit conversion (rpm <-> Hz electrical, mm, T/G, deg <-> deg_e, AWG, per-unit) | `jmag_units(convert, value, from_unit, to_unit, poles?, base?)` | pure |
| A hand estimate with formula, assumptions and expected FEA tolerance | `jmag_estimate(kind, params)` | `kind='list'` lists kinds and parameters (gap B, Carter, kw, EMF/torque constant, dq torque, inductances, gapped core, tooth/yoke B, slot fill, copper loss, electrical frequency, `step_plan`, Steinmetz loss); inputs in mm, `poles` = 2p; formulas in `references/formulas.md` |
| Slot-phase layout, winding factor, periodicity, balance | `jmag_winding_plan(slots, poles, phases, layers, coil_pitch, turns, parallel_paths)` | pure; run before drawing the stator; returns part names and `labels_coils` |
| Build that winding in JMAG | `jmag_winding(study, coils, mode='fem_coil', ...)` | after the plan is accepted; `fem_coil` mode verified |

If a tool is missing or disagrees with a formula here, compute by hand (show the formula) and
say so.

## Sizing order of operations

1. **Requirements**: torque T, speed n (base and max), DC-link voltage, current limit, cooling,
   envelope (D_out, L_stack).
2. **Electric and magnetic loading**: pick the shear stress for the cooling class
   (`references/typical-values.md`), then rotor volume `D^2 L = 2 T / (pi * sigma)`.
3. **Slots/poles/winding**: `references/slot-pole-table.md`; check `kw1`, periodicity, cogging
   order, unbalanced magnetic pull. Confirm with `jmag_winding_plan`.
4. **Magnetic circuit**: gap B from magnet (or magnetising current), then tooth and yoke widths
   from the flux-density targets of the chosen steel.
5. **Turns**: from back-EMF at base speed vs available voltage (leave 10-20 % headroom), then
   conductor area from current density and slot fill.
6. **Check list** below, then draw (`jmag-geometry`) and simulate.

## Plausibility checks (do these on every design and every FEA result)

| Quantity | Formula | Normal range |
|---|---|---|
| Gap flux density (SPM, NdFeB) | `B_g ~ Br * hm / (hm + mu_rec * k_c * g)` | 0.7-1.0 T |
| Fundamental of a square-ish gap B | `B_1 = (4/pi) B_g sin(alpha pi/2)` | alpha = magnet arc / pole pitch |
| Phase flux linkage | `psi_1 = kw1 * N_ph * 2 B_1 r L / p` | compare with `Coil Flux-Linkage` fundamental |
| Back-EMF | `E_peak = omega_e * psi_1` | same within 5-10 % in FEA |
| Torque (dq, peak amps) | `T = 3/2 p (psi_m i_q + (L_d - L_q) i_d i_q)` | SPM: `L_d ~ L_q` |
| Kt vs Ke | `Kt [Nm/A_pk] = 3/2 * Ke [V_pk,ph per rad/s mech]` | mismatch = wrong turns, units or phase |
| Tooth / yoke B | `B_t ~ B_g tau_s / (b_t k_fe)`, `B_y ~ Phi / (2 h_y L k_fe)` | steel table in `references/typical-values.md` |
| Current density | `J = I_rms / (a_cond)` | by cooling class |
| Slot fill (copper / slot area) | `k_fill = N_slot a_cond / A_slot` | 0.35-0.45 random round wire |
| Gapped inductor | `L = N^2 mu0 A / g` | 2D FEA +10-20 % (fringing) |

Full derivations, Carter factor, Ld/Lq, saliency and voltage limits: `references/formulas.md`.

## Turning estimates into the Engineering rung

1. Before the run: write down the estimate, its formula and its expected error (ideal gap,
   no saturation, no leakage -> FEA usually differs by 5-20 %).
2. After the run: compare with the FEA number taken from a named table/column
   (e.g. `Coil Flux-Linkage` / `CoilU`, last-period fundamental).
3. Classify: within the expected error -> "consistent"; outside but explainable (saturation,
   fringing, leakage, end effects not in 2D) -> say which and why; unexplained -> treat as an
   abnormal result and go to `jmag-troubleshooting` (stack length, turns, phase, orientation).
4. Report both numbers, the ratio, and the explanation. Never tune the estimate to match.

`jmag_estimate` returns `expected_fea_tolerance_pct` with each value, and
`jmag_experiment(..., params={..., "estimate": {"kind": ..., "params": {...}}})` attaches the
expectation to a test result, so the comparison lands in the same output.

## Red flags in a design (raise them before simulating)

- `gcd(Q, 2p) = 1` (e.g. 9s8p, 9s10p, 15s14p): unbalanced magnetic pull; needs the full model
  and a bearing-load comment.
- `Q / (3 * gcd(Q, p))` not an integer: the 3-phase winding cannot be balanced.
- Tooth B above ~1.9 T in silicon steel at rated load: saturation, torque will fall below the
  linear estimate and iron loss rises sharply.
- `J` above the cooling-class limit, slot fill above what the winding technology can achieve.
- Back-EMF at max speed above the inverter voltage with no field-weakening plan (`E_ll,pk > V_dc`
  means uncontrolled generation if the inverter trips).
- Magnet working point below the knee at max temperature and max negative d-current
  (demagnetisation; no dedicated tool - check B in the magnets on a contour).
