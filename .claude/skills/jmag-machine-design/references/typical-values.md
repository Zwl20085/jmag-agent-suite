# Typical values (orders of magnitude for plausibility)

Contents: 1 Loading and shear stress - 2 Current density and slot fill - 3 Flux densities per
steel - 4 Gap flux density per machine type - 5 Magnets - 6 Conductors and insulation -
7 Iron loss reference points - 8 Cooling

These are engineering ranges from the machine-design literature, for judging plausibility -
not limits. A value outside the range is a question to ask, not automatically an error.

## 1. Loading and shear stress

| Machine / cooling | Shear stress sigma [kPa] | Electric loading A [kA/m rms] |
|---|---|---|
| Small totally enclosed, natural cooling | 0.7-2 | 5-15 |
| Industrial integral-kW, fan cooled | 2-7 | 15-40 |
| High-performance servo | 7-15 | 30-60 |
| Traction, liquid cooled (EV) | 15-35 (peak) | 50-100+ (peak) |
| Aerospace / large direct-liquid cooled | 30-100 | 100-200 |

TRV (torque per rotor volume) = 2 sigma, e.g. 14-30 kNm/m^3 for servo motors.

## 2. Current density and slot fill

| Cooling | J continuous [A/mm^2 rms] | Short-term peak |
|---|---|---|
| Natural convection, enclosed | 3-5 | 2-3 x for seconds |
| Fan / forced air | 5-10 | |
| Water jacket | 10-15 | 25-30 for < 30 s |
| Direct oil / in-slot cooling | 15-30 | |

| Winding technology | Slot fill (copper / slot area) |
|---|---|
| Random-wound round wire, distributed | 0.35-0.45 |
| Needle- / linear-wound tooth coils | 0.45-0.60 |
| Segmented stator, pre-formed tooth coils | 0.55-0.70 |
| Hairpin / rectangular wire | 0.60-0.75 |
| Litz wire | 0.25-0.40 (copper only) |

## 3. Flux densities per steel (at rated load, peak)

| Material | Tooth [T] | Yoke [T] | Saturation knee [T] |
|---|---|---|---|
| Non-oriented Si steel 0.2-0.5 mm (e.g. M270-35A, 35JN210, 50JN400) | 1.4-1.8 | 1.2-1.6 | ~1.6-1.7 |
| Cobalt-iron (49 % Co) | 1.8-2.1 | 1.6-1.9 | ~2.1-2.2 |
| Soft magnetic composite (SMC) | 1.2-1.5 | 1.0-1.4 | ~1.5 (low mu_r 300-800) |
| Ferrite (transformer/inductor cores, MnZn) | - | 0.2-0.35 | ~0.4-0.5 (drops with T) |
| Powder cores (iron, MPP, sendust) | - | 0.3-1.0 (DC bias) | gradual |
| Grain-oriented steel (transformers) | - | 1.5-1.75 | ~1.9 |

The tooth/yoke ranges for silicon steel match the visual check in `jmag-simulation`
(`references/feedback.md`). Peaks at sharp re-entrant corners are mesh singularities.
IPM bridges are meant to saturate (> 2 T is normal there).

## 4. Air-gap flux density (fundamental peak or flat-top)

| Machine | B_g [T] |
|---|---|
| SPM with NdFeB | 0.7-1.0 (flat-top) |
| SPM with ferrite | 0.3-0.45 |
| IPM with NdFeB (no-load fundamental) | 0.6-0.9 |
| Induction machine (magnetising, peak) | 0.6-0.9 |
| Wound-field synchronous | 0.8-1.05 |
| SynRM (d-axis at rated current) | 0.6-0.9 |

## 5. Magnets (typical, check the grade datasheet)

| Property | Sintered NdFeB | SmCo (2:17) | Ferrite |
|---|---|---|---|
| Br at 20 C [T] | 1.15-1.45 | 1.0-1.15 | 0.38-0.46 |
| mu_rec | 1.03-1.10 | 1.02-1.05 | 1.05-1.2 |
| Br temp. coefficient [%/K] | -0.09 to -0.12 | -0.03 to -0.04 | -0.18 to -0.20 |
| Hcj temp. coefficient [%/K] | -0.4 to -0.6 | -0.15 to -0.3 | +0.2 to +0.4 |
| Resistivity [uOhm m] | 1.3-1.6 (sigma ~ 0.6-0.7 MS/m) | 0.5-0.9 | > 1e4 (insulator) |
| Max working temp. [C] | 80-220 (grade suffix H, SH, UH, EH...) | 250-350 | 100-250 |

NdFeB demagnetisation risk is highest at high temperature (Hcj falls); ferrite is worst at
low temperature (Hcj rises with T).

## 6. Conductors and insulation

| Material | Resistivity at 20 C [Ohm m] | Temp. coefficient [1/K] | sigma [MS/m] |
|---|---|---|---|
| Copper | 1.72e-8 | 0.00393 | 58 |
| Aluminium (pure, EC) | 2.65e-8 | 0.0040 | 37.7 |
| Die-cast aluminium (cage) | 3.0e-8 to 4.0e-8 | ~0.004 | 25-33 |

Insulation thermal classes (hot-spot limits): B 130 C, F 155 C, H 180 C, N 200 C.
Design practice: run class F at class B temperature rise for life margin.

## 7. Iron loss reference points

- EN 10106 / JIS non-oriented grade names encode the guaranteed maximum loss at 1.5 T, 50 Hz:
  `M270-35A` = 2.70 W/kg, 0.35 mm; `35A300` = 3.00 W/kg, 0.35 mm. Vendor names (e.g. 35JN210)
  follow a similar pattern - check the datasheet before relying on it.
- Loss scales roughly as `f^1.3-1.6` and `B^1.8-2.2` for sinusoidal flux; at 400 Hz / 1 T a
  0.35 mm grade is typically 15-25 W/kg, a 0.2 mm grade 10-15 W/kg.
- Rotational/harmonic flux and PWM add 10-50 % to sinusoidal-test values; punching damage adds
  more in narrow teeth. JMAG's loss study can include some of this (see `jmag-losses-efficiency`).

## 8. Cooling (for lumped thermal estimates)

| Mechanism | Heat-transfer coefficient h [W/(m^2 K)] |
|---|---|
| Natural convection, housing in air | 5-12 (+ radiation ~5) |
| Forced air over finned housing | 20-100 |
| Water/glycol jacket (equivalent at the stator back) | 500-3000 |
| Oil spray / direct oil on end windings | 500-5000 |

| Path | Thermal conductivity [W/(m K)] |
|---|---|
| Laminated stack, radial / axial | 25-40 / 1-3 |
| Copper | 390 |
| Impregnated slot (equivalent, wire + resin) | 0.5-1.5 (random wound), 2-4 (hairpin) |
| Slot liner (Nomex-type) | 0.1-0.2 |
| Contact stator-housing (interface gap) | equivalent 0.02-0.1 mm of air |
