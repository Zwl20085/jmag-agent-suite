# Formulas for sizing and checking (standard machine theory)

Contents
1. Symbols and conventions
2. Frequencies, speeds, steps
3. Air gap: Carter factor, gap flux density, flux per pole
4. Winding: winding factor, flux linkage, back-EMF
5. Loading, shear stress, torque
6. dq model: torque, Ld/Lq, Park transform, voltage limit
7. Inductances
8. Tooth, yoke, slot, current density
9. Induction machine
10. Transformer and inductor
11. Actuators and coupled coils

Everything here is textbook machine theory (Hendershot & Miller, Pyrhonen et al., Hanselman).
It describes the device, not JMAG; JMAG-specific behaviour is in the `jmag-simulation`
references.

---

## 1. Symbols and conventions

`Q` stator slots, `p` pole pairs (`2p` poles), `m` phases, `N_ph` series turns per phase,
`kw1` fundamental winding factor, `D` = gap diameter (`r = D/2`), `L` stack length, `g`
mechanical gap, `hm` magnet thickness, `Br` remanence, `mu_rec` recoil permeability (~1.05),
`k_fe` stacking factor (0.95-0.98), `omega_m` mechanical rad/s, `omega_e = p omega_m`.
Currents and flux linkages are **peak** values unless written `_rms`. dq quantities use the
amplitude-invariant Park transform (section 6).

## 2. Frequencies, speeds, steps

- `f_e = n_rpm / 60 * p`; `omega_e = 2 pi f_e`.
- Induction: `n_s = 60 f / p`, slip `s = (n_s - n) / n_s`, rotor frequency `s f`.
- Transient step: `dt = 1 / (f_e * N_per_period)` with `N_per_period >= 30-60` (EMF, torque);
  >= 20 per highest harmonic of interest; PWM: >= 20 per carrier period.
- Mechanical angle per step = `360 / (p * N_per_period)` degrees; choose the slide-mesh
  `CircumferentialDivision` so one step moves an integer number of elements
  (`CircumferentialDivision = k * 360 / angle_per_step`).

## 3. Air gap

- Carter factor: `k_c = tau_s / (tau_s - gamma g)`,
  `gamma = (4/pi) [ (b0/(2g)) atan(b0/(2g)) - ln sqrt(1 + (b0/(2g))^2) ]`,
  `tau_s = pi D / Q` slot pitch, `b0` slot opening.
- Effective gap: SPM `g_e = k_c g + hm / mu_rec`; IPM / IM / SynRM `g_e = k_c g`.
- SPM gap flux density (no leakage, no saturation): `B_g = Br hm / (hm + mu_rec k_c g)`;
  multiply by a leakage factor 0.85-0.95.
- Fundamental of a rectangular gap wave of pole-arc ratio `alpha`:
  `B_1 = (4/pi) B_g sin(alpha pi / 2)`.
- Fundamental flux per pole: `Phi_1 = (2/pi) B_1 tau_p L = 2 B_1 r L / p`, `tau_p = pi D / (2p)`.

## 4. Winding

- Distribution factor (integer slot, q slots/pole/phase, slot angle `a = 2 pi p / Q`):
  `kd = sin(q a / 2) / (q sin(a / 2))`.
- Pitch factor (coil pitch y slots, pole pitch `Q/(2p)` slots): `kp = sin(pi/2 * y / (Q/(2p)))`.
- `kw1 = kd kp` (concentrated / fractional-slot windings: star-of-slots, or
  `jmag_winding_plan`; values in `slot-pole-table.md`).
- Series turns per phase `N_ph = (coils per phase * turns per coil) / parallel_paths`.
- Fundamental phase flux linkage: `psi_1 = kw1 N_ph Phi_1`.
- Back-EMF: `E_pk = omega_e psi_1`, `E_rms = 4.44 f_e kw1 N_ph Phi_1`; line-line = sqrt(3) x phase
  (balanced, star).
- FEM coil turns in JMAG: a coil side made of k regions spreads `Turn` over them (verified, see
  jmag-gotchas); for k series tooth coils of N turns per phase side use `Turn = k N`.

## 5. Loading, shear stress, torque

- Electric loading (rms): `A = 2 m N_ph I_rms / (pi D)` [A/m].
- Air-gap shear stress: `sigma = (1/sqrt(2)) kw1 B_1 A cos(gamma)` [Pa], gamma = angle between
  current and EMF.
- Torque: `T = sigma * 2 pi r^2 L = (pi / (2 sqrt(2))) kw1 B_1 A D^2 L cos(gamma)`.
- Sizing: rotor volume `D^2 L = 2 T / (pi sigma)`; TRV (torque per rotor volume) `= 2 sigma`.
- Power balance (motoring): `P_in = P_mech + P_cu + P_fe + P_magnet + P_mech_loss`,
  `P_mech = T omega_m`; electromagnetic power `P_em = sum_k <i_k dpsi_k/dt> = <T_em> omega_m`
  (what `jmag_physics_check(check='power_balance')` verifies).

## 6. dq model

Park transform (amplitude-invariant, theta_e = electrical rotor angle, d-axis on the magnet):
- `psi_d = 2/3 [psi_a cos(th) + psi_b cos(th - 2pi/3) + psi_c cos(th + 2pi/3)]`
- `psi_q = -2/3 [psi_a sin(th) + psi_b sin(th - 2pi/3) + psi_c sin(th + 2pi/3)]`
- same for currents. Check: `psi_d` and `psi_q` are nearly constant at a steady load point.

Torque and constants:
- `T = 3/2 p (psi_d i_q - psi_q i_d) = 3/2 p (psi_m i_q + (L_d - L_q) i_d i_q)`.
- `Kt = 3/2 p psi_m` [Nm per A peak]; `Ke = p psi_m` [V peak phase per rad/s mech];
  `Kt = 3/2 Ke`. In rms terms: `T = 3 E_rms I_rms cos(gamma) / omega_m`.
- Current angle `beta` from the q-axis: `i_d = -I sin(beta)`, `i_q = I cos(beta)`.
  MTPA for `L_d < L_q`: `i_d = (psi_m - sqrt(psi_m^2 + 8 (L_q - L_d)^2 I^2)) / (4 (L_q - L_d))`.
- Apparent inductances at one operating point: `L_d = (psi_d - psi_m) / i_d`, `L_q = psi_q / i_q`;
  saturated machines need frozen permeability or a flux map psi_dq(i_d, i_q) for an exact split.
- Voltage limit (steady state, R neglected): `omega_e sqrt((L_q i_q)^2 + (psi_m + L_d i_d)^2) <= V_max`,
  `V_max ~ V_dc / sqrt(3)` (SVPWM, phase peak). Characteristic current `I_ch = psi_m / L_d`:
  infinite constant-power speed range if `I_ch <= I_max`.
- Base speed: `omega_e,base = V_max / sqrt((L_q I_q)^2 + (psi_m + L_d I_d)^2)` at rated current.

## 7. Inductances

- Magnetising (synchronous, m = 3): `L_m = (3 mu0 D L / (pi g_e)) (kw1 N_ph / p)^2`.
  SPM: `L_d ~ L_q ~ L_m + L_leak` (slot, tooth-tip, end-winding leakage add 20-50 % for
  concentrated windings).
- Single coil, gap-dominated: `L = N^2 mu0 A_gap / g`; with core: `L = N^2 / (R_gap + R_core)`,
  `R = l / (mu0 mu_r A)`.
- Energy: `W = 1/2 L I^2 = 1/2 sum i psi` (linear); co-energy for saturated cases.
- 2D FEA lacks end-winding leakage; add `L_end` from a formula or 3D when comparing with tests.

## 8. Tooth, yoke, slot, current density

- Tooth: `B_t ~ B_g tau_s / (b_t k_fe)` (b_t tooth width at the narrowest point).
- Yoke: `B_y ~ Phi_1 / (2 h_y L k_fe)` (h_y yoke depth; half the pole flux goes each way).
- Slot fill: `k_fill = n_cond a_cond / A_slot` (bare copper / slot area incl. liner).
- Current density: `J = I_rms / (a_strand * n_parallel_strands)`.
- Copper loss: `P_cu = m I_rms^2 R_ph`, `R_ph = rho_T N_ph l_turn / (a_cond * a_paths)` (with
  parallel paths `a_paths`, `N_ph` series turns), `rho_T = rho_20 (1 + 0.00393 (T - 20))` for copper,
  `rho_20 = 1.72e-8 Ohm m`; `l_turn ~ 2 (L + l_end)`.
- Skin depth: `delta = sqrt(2 rho / (omega mu))`; copper at 20 C: `delta ~ 66 / sqrt(f) mm`
  (9.3 mm at 50 Hz, 2.1 mm at 1 kHz). Conductors thicker than ~delta need AC-loss modelling.

## 9. Induction machine

- Per-phase equivalent circuit: `R1, X1, Xm, R2'/s, X2'`; torque
  `T = (3 / omega_s) I2'^2 R2' / s`, `omega_s = 2 pi f / p`.
- Breakdown torque (R1 neglected): `T_max ~ 3 V1^2 / (2 omega_s (X1 + X2'))` at slip
  `s_max ~ R2' / (X1 + X2')`.
- No-load test (s ~ 0): `I0` mostly magnetising -> `Xm ~ V1 / I0`; core loss from input power.
- Locked rotor (s = 1): `Z_lr ~ (R1 + R2') + j (X1 + X2')` at rated frequency (deep-bar effect:
  R2' rises, X2' falls with frequency).
- End ring equivalent resistance per bar: `R_ring_eq = R_seg / (2 sin^2(pi p / Q_r))`.

## 10. Transformer and inductor

- Voltage-flux: `V_rms = 4.44 f N A_c B_pk` (sinusoidal); size `A_c` for `B_pk` below the knee.
- Open-circuit test: magnetising current and core loss at rated voltage.
- Short-circuit test: leakage inductance and copper loss at rated current; static FEA
  equivalent: ampere-turn-balanced currents `N1 I1 = N2 I2` (opposite directions), then
  `L_sigma,1 = 2 W / I1^2` referred to the primary.
- Turns ratio check: `psi2 / psi1 ~ N2 / N1` with secondary open (minus leakage).

## 11. Actuators and coupled coils

- Force from co-energy: `F = dW'/dx |_i`; linear case `F = 1/2 i^2 dL/dx`.
- Maxwell pull (gap face, uniform B): `F = B^2 A / (2 mu0)` (about 398 kN/m^2 per T^2).
- Coupling: `M = psi_2 / i_1` (coil 2 open / zero current), `k = M / sqrt(L1 L2)`;
  for a resonant link the efficiency limit is set by `k Q` with `Q = omega L / R`.
