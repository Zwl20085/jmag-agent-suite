# Thermal: what JMAG 23 scripting can and cannot do, and a lumped estimate

Contents: 1 Facts - 2 Export workflow - 3 Lumped thermal network - 4 Worked example -
5 Coupling back to the EM model

## 1. Facts

- The JMAG-Designer 23.0 Python script reference has **no thermal-study class** and no
  loss-to-heat mapping API; the bridge has no thermal tool. Temperature appears in the API as
  inputs: efficiency-map temperature corrections, `CalculationDefinition::SetTemperature`,
  `ProbeDefinition::SetTemperature`, material `TemperatureType` / `ConstantTemperature`.
- `jmag_capabilities` reports the study types JMAG offers; on 23.0 these include thermal types
  (e.g. `HeatSteadyPseudo2D`; it also reports whether the thermal solver `jheat.exe` is
  installed). Creating one with
  `jmag_create_study` and setting heat sources/boundaries through generic conditions is
  **unverified on 23.0** and licence-dependent - try it only on a copy, and say so.
- The user manual describes "Magnetic Field, Iron Loss -> Thermal" coupling (and Thermal <->
  Structural) as GUI workflows with JMAG's thermal module; a user with that module can run it in
  the GUI from the saved project.
- Therefore, by default every temperature the agent reports comes from an external model or a
  hand estimate, and must be labelled as such.

## 2. Export workflow

1. Steady operating point, losses per part from the loss study and `Joule Loss`
   (`jmag-losses-efficiency`), copper loss by hand at an assumed winding temperature.
2. `jmag_export_results` / `jmag_export_table` to CSV; add a summary table:
   part, material, volume (from `jmag_list_parts` area x stack), loss [W], loss density [W/m^3].
3. Target tools: Motor-CAD (component losses, plus end-winding share of copper loss
   `~ l_end / (L + l_end)`), Icepak / Fluent / COMSOL (volumetric heat sources per part), or
   the lumped network below.
4. Iterate: update `R(T)` and `Br(T)`, rerun EM + loss, until temperature changes < 2-5 K.

## 3. Lumped thermal network (steady state, hand estimate)

Resistances in K/W:
- Conduction: `R = l / (k A)`; cylinder shell `R = ln(r2/r1) / (2 pi k L)`.
- Convection: `R = 1 / (h A)`; contact: `R = l_gap_equiv / (k_air A)` with
  `l_gap_equiv` 0.02-0.1 mm, `k_air` 0.026 W/(m K).
- Values for `k` and `h`: `jmag-machine-design` typical-values section 8.

Minimal network for a radial machine (heat flows radially to a housing):
```
P_cu (slot) -> R_slot (winding -> tooth/yoke via liner)   \
P_fe,teeth  ------------------------------------------------> node yoke -> R_contact -> R_housing -> R_conv -> T_coolant
P_fe,yoke   -----------------------------------------------/
P_cu,end -> R_end (end winding -> housing air, weak)
P_rotor (magnets/rotor iron) -> R_gap (air-gap convection) -> stator
```
- `R_slot ~ (w_slot / 2) / (k_eq A_slot_wall) + t_liner / (k_liner A_slot_wall)`, with
  `A_slot_wall` = slot perimeter x stack x number of slots.
- Hot spot ~ slot centre: `T_hot ~ T_winding_avg + P_cu,slot R_slot_internal / 2`.
- Uncertainty of such a network: +/- 15-25 K; say so.

## 4. Worked example (order of magnitude)

Water-jacket stator, `P_cu` = 600 W, `P_fe` = 300 W, coolant 65 C, jacket `h A` = 300 W/K
(`R_conv` 0.0033), contact + housing 0.01 K/W, slot-to-yoke 0.04 K/W:
- yoke: `65 + 900 x (0.0033 + 0.01) = 77 C`
- winding average: `77 + 600 x 0.04 = 101 C`; hot spot ~ 110-120 C -> class F with margin.

## 5. Coupling back to the EM model

- Copper: `R_ph(T)` in the circuit `Coil` `Resistance` (`jmag_add_fem_coil(resistance=...)`).
- Magnets: material temperature properties (`TemperatureType`, `ConstantTemperature`,
  `BrCorrection` are verified property names on magnet materials; their effect on Br is
  **unverified on 23.0** - check open-circuit psi at two temperatures against the datasheet
  coefficient, e.g. NdFeB -0.1 %/K).
- Efficiency maps: the temperature-correction members in `efficiency-map.md`.
