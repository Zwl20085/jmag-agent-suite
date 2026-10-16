# Loss models, material data, PWM and AC copper loss

Contents
1. Iron loss models
2. Material loss data in JMAG
3. The loss study
4. Copper loss (DC and AC)
5. Magnet eddy loss
6. PWM harmonics
7. Reading loss tables and sanity checks

Verification status: result-table display names `Joule Loss`, `Iron Loss`, `Hysteresis Loss`
and the condition type name `Ironloss` are listed in `jmag-simulation/references/properties.md`.
API members cited below are from the JMAG 23.0 script reference. The iron-loss study via
`jmag_loss_study` is **verified on 23.0** (creation, run, per-part totals); the loss models,
PWM and magnet-loss options below are not. A verified workflow still needs a hand check of its
numbers (loss data units, reference window, stack length).

---

## 1. Iron loss models

- Steinmetz split (sinusoidal flux): `p = k_h f B^alpha + k_e f^2 B^2 (+ k_ex f^1.5 B^1.5)` W/kg;
  `alpha` 1.6-2.2. Hysteresis dominates at 50 Hz, eddy above a few hundred Hz.
- Non-sinusoidal flux (real teeth, PWM): eddy loss follows `(dB/dt)^2`, so harmonics add far
  more than their amplitude suggests; minor hysteresis loops add hysteresis loss. JMAG's
  loss study can account for harmonics by FFT of each element's B waveform (manual: "Iron Loss
  Formulas", `<JMAG>/Manual/en/DOC/IronLossFormulas.pdf` on this machine).
- Datasheet sanity: grade name gives W/kg at 1.5 T 50 Hz (`jmag-machine-design`,
  typical-values section 7). Build-factor 1.2-1.8 for punching stress, rotational flux and
  harmonics when comparing with tests.

## 2. Material loss data in JMAG

- Library steels normally carry loss data (W/kg vs B and f tables). Look with
  `jmag_get_properties(kind='material', name=<part>)` and `jmag_find_materials`.
- Custom data: `Material.LoadLossData(filename)` / `MaterialObject.LoadLossData(filename)`
  load a `*.iron` file; `Material.SetLossCoordinatePattern(pattern)` sets the coordinate
  pattern used for the loss calculation (API members in the 23.0 reference; **unverified on
  23.0**).
- `jmag_create_material(loss={"Kh", "Ke", "alpha", "beta"})` writes formula constants; JMAG's
  unit convention for them is **not verified** - cross-check one element/part by hand.
- Magnets: conductivity (NdFeB ~0.6-0.7 MS/m) matters for magnet loss; laminated cores:
  `Laminated`, `LaminationFactor` (verified property names) and eddy currents **off** in the
  core (lamination eddy loss belongs in the loss study's material data; a solid core with eddy
  currents on would count it twice - standard practice, **unverified on 23.0** for JMAG's exact
  handling).

## 3. The loss study

- API: `Study.CreateLossStudy(name)` "creates a Study object of iron loss analysis" from the
  solved magnetic study (23.0 reference). `jmag_loss_study` wraps it (verified on 23.0):
  `action='create'` with `parts`, `frequency_hz` (= f_e; 0 aborts with "Frequency is 0[Hz]"),
  `reference_steps=[first, last]` = the last electrical period of the source study (e.g.
  [70, 129] for 60 steps/period over 2 periods after JMAG's motion step update), `properties`
  for the `Ironloss` condition (`HysteresisLossCalcType`, `JouleLossCalcType`,
  `LaminationThickness`, `Cyclicity` ...) and `study_properties` for the loss study. The response
  lists all property names and the parts' library loss settings (`Loss_Type`,
  `Loss_ConstantKh/Ke/Alpha/Beta`) so you can see whether loss data exists.
- `action='results'`: every table whose name contains "Loss" with totals [W] per part; rows are
  frequency components (x in Hz), totals sum over rows; `include_spectrum=true` adds the rows.
- Settings to decide (and report): which steps/period are used (the last full electrical
  period of a steady run), which parts, frequency resolution (harmonics up to the step limit),
  hysteresis method, and whether the result is scaled to the full model and stack length.
- Requirements: the source study must have >= 1 complete steady electrical period, enough steps
  per period for the harmonics you care about (60 steps -> up to ~ the 30th harmonic), and
  `ModelThickness` set.
- Expect (rated point, silicon steel, 50-400 Hz): stator iron loss 1-3 % of rated power for
  small industrial machines; teeth have the highest W/kg; rotor iron loss of synchronous
  machines small unless slot/PWM harmonics are strong.

## 4. Copper loss

- DC: `P_cu = m I_rms^2 R_ph(T)`, `R(T) = R_20 (1 + 0.00393 (T - 20))`; include end windings
  (`l_turn ~ 2 (L + l_end)`). Stranded FEM coils do **not** produce this as a field loss; the
  circuit `Coil` component's `Resistance` is used in the circuit equations (so it appears in
  the KVL/electrical power), so compute the loss from current and resistance.
- AC (skin and proximity): only conductors modelled as solid regions see eddy currents. Model
  individual strands/hairpins as FEM conductors (`jmag_add_fem_conductor`, eddy currents on)
  in the slot region of interest; read `Joule Loss`. Ratio `R_ac / R_dc`:
  significant when the conductor height exceeds the skin depth (`delta ~ 66/sqrt(f) mm` for
  copper at 20 C). Hairpins at 500-1000 Hz typically 1.5-3.
- Efficiency-map approximation: `EfficiencyMapDefinition.AddACLoss()` (returns whether
  approximate AC copper loss is added; 23.0 reference).

## 5. Magnet eddy loss

- Turn on eddy currents in magnet parts (`EddyCurrentCalculation: 1`), run the transient, read
  the magnet columns of `Joule Loss`.
- 2D assumes infinitely long magnets: eddy currents close at the ends without resistance, so
  2D overestimates loss for axially segmented magnets; the net current in each magnet must be
  zero (insulated magnets) - how JMAG 23 enforces this in 2D (eddy-current condition
  properties) is **unverified on 23.0**; check with `jmag_get_properties(kind='condition',
  with_help=true)` on an `EddyCurrent` condition and `jmag_docs_search("eddy current magnet")`.
- Sources: slot harmonics, MMF sub-harmonics (fractional-slot windings), PWM ripple.

## 6. PWM harmonics

- Carrier `f_sw`; current ripple at `k f_sw +/- n f_e` sidebands. A PWM-fed transient needs
  >= 20 steps per carrier period (`dt <= 1 / (20 f_sw)`) - expensive; run 1-2 electrical
  periods after a sinusoidal pre-run.
- PWM circuits: build with `jmag_circuit_add` (switches/sources) or `jmag_exec_python`; no
  verified PWM recipe yet.
- Efficiency maps can add approximate PWM iron and magnet loss:
  `EfficiencyMapDefinition.AddPWMIronLoss()`, `SetAddMagnetLoss(bool)`,
  `SetMagnetConductivity(index, conductivity)`, `CarrierFrequency()` (23.0 reference;
  **unverified on 23.0**).
- Expect PWM to add 10-30 % to iron loss and dominate magnet loss in many PM machines.

## 7. Reading loss tables and sanity checks

- `jmag_list_results` shows the loss tables and their columns (parts); `jmag_get_result` gives
  the time series; use the mean over the last full period (`last_period.mean`).
- Units: W for the modelled stack in 2D (check that `ModelThickness` is the real stack; a
  1 mm default gives losses 50-200 x too small).
- Sanity: W/kg per part vs datasheet at the part's peak B and f_e; total losses vs
  `P_in - P_out`; the loss distribution (teeth > yoke in W/kg).
