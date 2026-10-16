---
name: jmag-losses-efficiency
description: Loss, efficiency and temperature work with JMAG-Designer through the jmag MCP tools - iron loss (hysteresis, eddy, excess) via a loss study, copper loss including AC/proximity effects, magnet eddy loss, PWM harmonic losses, material loss data, efficiency maps (EfficiencyMapDefinition), reading loss tables and checking efficiency against the power balance; plus an honest thermal workflow (the bridge has no thermal tooling and JMAG 23 scripting no loss-to-heat API, so losses are exported to Motor-CAD / Icepak / a lumped model). Use when the user asks about losses, efficiency, efficiency maps, heat, temperature rise, hot spots or cooling of a JMAG model.
---

# Losses, efficiency and temperature

Losses are small differences of large powers; a 1 % torque error is a 50 % error in a 98 %
efficient machine's loss. Every loss number needs its source table, its method, and a check
against the power balance or a hand estimate.

## Loss components and where they come from

| Component | Source in JMAG | Tool route | Hand check |
|---|---|---|---|
| Stator/rotor iron loss | loss study from a solved transient study (`Study.CreateLossStudy` + `Ironloss` condition); tables `Iron Loss`, `Hysteresis Loss`, `Joule Loss` (+ "(Each Component)") | `jmag_loss_study` (verified on 23.0) | Steinmetz with datasheet W/kg (`references/losses.md`) |
| DC copper loss | not a field result for stranded coils: `m I_rms^2 R_ph` | hand / `jmag_estimate` | `R_ph` at temperature |
| AC copper loss (skin, proximity) | solid conductors as FEM conductors -> `Joule Loss` table | `jmag_add_fem_conductor` | skin depth vs conductor size |
| Magnet eddy loss | magnets with `EddyCurrentCalculation: 1` -> `Joule Loss` | `jmag_set_material` | sub-harmonic MMF, PWM |
| Cage / bar loss | FEM conductors -> `Joule Loss` | `jmag_add_fem_conductor` | `s P_gap` |
| PWM harmonic losses | PWM circuit (very fine steps) or efficiency-map approximations (`AddPWMIronLoss`, `AddACLoss`, `SetAddMagnetLoss`) | `jmag_efficiency_map` | carrier sidebands |
| Mechanical (friction, windage) | not FEA: `SetMechancialLossFactor` in the efficiency map, or a hand model | `jmag_efficiency_map` | bearing data |

Details, material data and PWM rules: `references/losses.md`.

## Workflow: one operating point

1. A validated transient load point (`jmag-simulation`, `jmag-experiments`) with >= 2 electrical
   periods, steady state, power balance < 1-5 %.
2. Material loss data present for every steel (library grades usually have it; custom grades
   need `Material.LoadLossData(*.iron)` or `jmag_create_material(loss=...)` - its unit
   convention is unverified, so cross-check one result by hand).
3. `jmag_loss_study(action='create', study=<solved>, name=..., parts=[cores], frequency_hz=f_e,
   reference_steps=[first, last step of the last electrical period])` -> runs in seconds ->
   per-part totals; `action='results'` re-reads them. `frequency_hz` is required in practice
   (with 0 the solver aborts "Frequency is 0[Hz]"). Rows of loss tables are frequency
   components; totals sum over rows. Divide by part mass for W/kg.
4. Copper loss by hand at the winding temperature; magnet/bar/AC loss from `Joule Loss`.
5. Efficiency `eta = P_out / (P_out + P_loss)` (motoring), `P_out = <T> omega_m` with `<T>`
   from the Torque table over the last period.
6. Power-balance check: `P_in` (`<sum v i>` from circuit tables) `= P_out + P_cu + P_fe + P_mag
   (+ P_mech)` within the accuracy of the loss models (a few % of the losses). The
   electromagnetic part `<sum i dpsi/dt> = <T> omega` (`jmag_physics_check(check='power_balance')`)
   must hold first; its residual includes the eddy losses of conducting parts.

## Workflow: efficiency map

`jmag_efficiency_map` wraps JMAG's `EfficiencyMapDefinition` (limits, control scheme, loss
options, temperature corrections, torque type; every value read back). **Unverified on 23.0**
beyond the API signatures: JMAG evaluates maps in an efficiency-map (response) study normally
made with its Efficiency Map wizard, and `create` on an ordinary Transient study may return an
invalid object (the error says so). API members: `references/efficiency-map.md`. Validate 3+
map points with full transient load points + loss study, and check the map's efficiency at
those points against `P_out / (P_out + P_loss)` from the point runs. Sweeps of your own:
`jmag-parametric-optimisation`.

## Temperature (honest version)

There is **no thermal workflow in the bridge**, and the JMAG 23.0 script reference has no
thermal-study class or loss-to-heat mapping API; the manual describes "Magnetic Field, Iron
Loss -> Thermal" coupling as a GUI workflow. `jmag_capabilities` may still list thermal study
types (e.g. `HeatSteadyPseudo2D`) and the `jheat.exe` solver, so `jmag_create_study` might create
an empty thermal study - but heat sources, boundaries and a run are **unverified on 23.0** and
need the thermal licence. So, by default:

1. Export per-part losses (`jmag_export_results`, `jmag_export_table`) with the operating point.
2. Map them to a thermal tool: Motor-CAD (loss per component), Icepak/Fluent (volumetric heat in
   W/m^3 per part), or the lumped hand model in `references/thermal.md`.
3. Feed temperatures back: copper resistance `R(T)`, magnet `Br(T)` (efficiency-map temperature
   corrections exist in the API), rerun, iterate until temperatures change < 2-5 K.
4. Report hot-spot estimates with the model used and its uncertainty (lumped: +/- 15-25 K).

Never present a temperature as an FEA result when it came from a lumped estimate.

## Common mistakes

- Loss study on a non-steady window (first electrical period included) -> losses too high.
- `ModelThickness` left at 1 mm -> every loss 1/L of the truth (2D losses scale with stack).
- Eddy currents on in laminated cores **and** lamination loss in the loss study -> double count.
- Copper loss at 20 C while the winding runs at 120 C -> R is 39 % higher, the loss ~28 % too low.
- Efficiency from electromagnetic torque without mechanical loss, or "efficiency > 99 %" for a
  small machine: re-check which losses are included before reporting.
