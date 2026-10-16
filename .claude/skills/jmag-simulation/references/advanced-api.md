# Advanced JMAG API through the bridge (JMAG-Designer 23.0)

Status of the tools that wrap parametric cases, loss studies, efficiency maps, windings, JMAG's own
exporters and the optimisation table. **VERIFIED** = exercised on JMAG-Designer 23.0.01zv with
`tests/live/advanced_flow.py` (12-slot/4-pole SPM, 30 steps per period) and follow-up live calls;
**UNVERIFIED** = written from the 23.0 API signatures only. Property names below were read from a
live JMAG, not guessed.

## Summary

| Tool / mode | Worker op | Status on 23.0 |
|---|---|---|
| `jmag_capabilities` | `capabilities` | VERIFIED |
| `jmag_case_table` get / set | `case_table_get` / `case_table_set` | VERIFIED (equation + circuit-source parameters) |
| `jmag_run_cases` method `loop` | `run_cases` | VERIFIED (2 cases, per-case progress file) |
| `jmag_run_cases` method `all_cases` | `run_cases` | UNVERIFIED |
| `jmag_run_cases` method `optimization` | `run_cases` | UNVERIFIED (needs optimiser licence) |
| `jmag_get_result` / `jmag_list_results` `case=k` | `get_result` / `list_results` | VERIFIED |
| `jmag_loss_study` create / results | `create_loss_study` / `loss_results` | VERIFIED |
| `jmag_efficiency_map` create | `efficiency_map_create` | VERIFIED refusal on a Transient2D study; creation in a map study UNVERIFIED |
| `jmag_efficiency_map` results | `efficiency_map_results` | UNVERIFIED |
| `jmag_winding` mode `fem_coil` | `winding_apply` | VERIFIED |
| `jmag_winding` mode `jmag_winding` | `winding_apply` | CRASHED Designer -> refused unless `force=true` |
| `jmag_export_table` kinds `all_tables`, `all_cases`, `table` | `export_table` | VERIFIED |
| `jmag_export_table` kind `field` | `export_table` | UNVERIFIED (returns False: needs a GUI selection) |
| `jmag_export_table` kinds `field_values`, `field_minmax` | `export_table` | UNVERIFIED, suspected crash -> refused unless `force=true` |
| `jmag_case_report` | `write_case_report` | VERIFIED |
| `jmag_optimization_table` get | `optimization_get` | VERIFIED |
| `jmag_optimization_table` set | `optimization_set` | UNVERIFIED |
| `jmag_experiment` `back_emf`, `cogging`, `load_point` | `experiment_setup` + run + get_result | VERIFIED |
| `jmag_experiment` `inductance_static` (L and Ld/Lq) | set_properties + run + get_result | VERIFIED |

## Capabilities

- `Model.GetStudyTypeNames()` on a 2D model of a full licence lists: Static2D, StaticAX, Transient2D,
  TransientAX, Transient2DResponse, Frequency2D, FrequencyAX, **HeatSteadyPseudo2D,
  HeatTransientPseudo2D** (thermal), StructuralStatic2D, StructuralEigenMode2D, StructuralFrequency2D,
  StructuralTransient2D, StructuralDirectTransient2D, SpeedPriorityModeStructuralStatic2D,
  ElecStatic2D, SpeedPriorityModeElecStatic2D, ElecFrequency2D, ElecStaticAX, ElecFrequencyAX.
  Thermal study types therefore exist for `jmag_create_study`; the bridge has no thermal-specific
  tools yet (conditions/properties of those studies are not verified).
- There is no licence query in the Python API (`Application.SwitchLicense` only). API presence is
  checked with `hasattr` on the `jmag.designer` classes (no JMAG call, no error log).

## Design table (parametric cases)

- `Study.GetDesignTable()`. Cases are 0-based, case 0 is the base case; `GetLabel(k)` is "k+1".
- `DesignTable.AllParameterNames()` lists every addable parameter (about 1200 on the SPM) as
  `'<Owner> (<Type>): <Property>'`, e.g. `'Motion (RotationMotion2D): AngularVelocity'`,
  `'Source3Ph (3PhaseCurrentSource): Amplitude'`, `'CoilU (Coil): Turn'`,
  `'PhaseU (FEMCoil2D)-PhaseU+1: ...'`, `'Study Properties: ModelThickness'`, `'Mesh Properties: ...'`,
  `'<Part>: Material'`. `AddParameter(name)` needs exactly that string; a short or unknown name logs
  `No such parameter as "..." (DesignTable::AddParameter)`. The bridge accepts the short form
  `'Owner: Property'` and resolves it.
- An equation created with `AddEquation` joins the table by itself as
  `'Equation parameters: <name>'` (type `Equation`, variable `<name>`); `AddParameter(<equation>)`
  then fails. Equation value per case: `GetEquation(name).GetValue(case)`.
- `AddCases(n)`, `SetValue(case, parameterIndex, value)`, `GetValue(case, index)` (returns the base
  expression string, e.g. `"2"`, for case 0 of an equation, numbers otherwise), `RemoveCase(k)`,
  `SetActive(k, 0|1)`, `IsActive(k)`, `ParameterName(i)`, `ParameterTypeName(i)` ('Equation',
  'Real', ...), `GetVariableName(i)` ('' for non-equation parameters, no error).
- `SetValue` with an out-of-range parameter index logs `Parameter index out of range.`
- The optimisation table mirrors the design-table parameters; the source amplitude shows there as
  `'Source3Ph (Three-phase Current Source): Amplitude Value / RMS Value'`.

## Running cases

- `Study.SetCurrentCase(k)` + `Study.Run()` solves case k; each case gets its own calculation folder
  `<project>.jfiles/<Model>~1/<Study>~1/Case<k+1>`. Two 31-step SPM cases took 5.6 s each.
  Torque scaled with the case current as expected (10 A: 1.804 Nm, 5 A: 0.910 Nm, ratio 0.504).
- `Study.CaseHasResult(k)` per case; `Study.DeleteResultCurrentCase()` before re-running a case.
- `SetCurrentCase` switches what `GetResultTable()` returns: this is how `case=k` reads results.
- `Study.RunAllCases()` / `RunOptimization()`: not exercised.

## Iron-loss study

- `Study.CreateLossStudy(name)` on a solved Transient2D study returns a study whose
  `GetScriptTypeName()` is `loss`; it comes with one condition `untitled 1` of type `Ironloss`
  (no parts). Condition types of the loss study: StressDistribution, ResidualStrain, Ironloss.
- Ironloss condition properties: BasicFrequencyType, Poles, RevolutionSpeed, BasicFrequency,
  HysteresisLossCalcType, JouleLossCalcType, StartReferenceStep, EndReferenceStep, Cyclicity,
  Use1stStepAsSteady, MultipleFrequency, UseFrequencyOrder, SetFrequencyType, MaximumFrequency,
  UseStartReferenceStep, UseEndReferenceStep, UseMultipleFrequency, FrequencyOrder (string),
  LimitFFTOutputFreq, FFTOutputFreqRatio, UseExcessEddyCurrentLossSeparate,
  UseExcessEddyCurrentLossAdjust, UseFrequencyForAnomalousEddyCurrent,
  ExcessEddyCurrentLossAdjustFrequency, LaminationThickness, LaminationLossNumDivision,
  UseLaminationLossFrequency, LaminationLossFrequency, Use1DHysteresisModel, UsrsubName (string),
  PresetType, Component1..3, CalcType, StressType, UsePartCoordinates, CoordinateSystem.
  Defaults seen: BasicFrequency 0, BasicFrequencyType 1, Poles 4 (taken from the model),
  Start/EndReferenceStep -1, HysteresisLossCalcType 2, JouleLossCalcType 1.
- With BasicFrequency 0 the run fails: `Frequency is 0[Hz]. Confirm the setting of basic frequency`
  and `The solver module ironloss was aborted` (solver `Tools/Loss/mod/ironloss.exe`). Setting
  `BasicFrequency` switched `BasicFrequencyType` from 1 to 2 by itself.
- Loss-study properties (all Flag unless noted): CaseIndex, OnlyTableResults,
  IronRefMagneticFluxDensity, IronIronLossDensity, IronHysteresisLossDensity,
  IronExcessEddyCurrentDensity, IronClassicalEddyCurrentDensity, IronJouleLossDensity, IronStress,
  IronMagneticFluxDensityLamination, IronMagneticFieldStrengthLamination,
  IronEddyCurrentDensityLamination, IronCurrentLossDensityLamination,
  IronHysteresisLossDensityLamination, CsvOutputPath (String), CsvResultTypes (String),
  CsvOutputResponseData, CsvOutputInductanceData, CsvOutputProbe, CsvOutputSection,
  CsvOutputCalculation, CsvOutputObjectiveData, DeleteResultFiles, CsvComponentType,
  CsvDivisionType, CsvDivisionCase, UseMultiCPU, MultiCPU.
- Run (Study.Run on the loss study): ~3-5 s for the SPM. Result tables: `Joule Loss`,
  `Joule Loss (Each Component)`, `Iron Loss`, `Iron Loss (Each Component)`, `Hysteresis Loss`,
  `Hysteresis Loss (Each Component)`, unit W, one column per part (plus `Total` in the tables without
  "(Each Component)"). `GetTimeUnit()` is `Hz`: rows are frequency components. **Row 0 ('Step 0',
  0 Hz) holds the total**; rows 1.. are the spectrum (Joule rows 1.. sum to row 0; hysteresis sits in
  row 0 only). Check on the SPM at 50 Hz, 10 A: stator Hysteresis 1.998 W + Joule 2.047 W = Iron 4.045 W.
- A warning about missing iron-loss table data in some frequency domain is normal for library steels at
  harmonic frequencies.
- Library material loss data cannot be read through the API: `GetValue` on `Loss_Type`,
  `Loss_ConstantKh` ... of a library material (`MaterialLibrary.GetMaterial(name)`) logs
  `The property should be a flag or real property.` although `GetPropertyType` says Flag/Double.
  A core part with exactly 0 W after the run is the sign of missing loss data.

## Efficiency map

- `Study.CreateEfficiencyMapDefinition(name)` on a Transient2D study returns an invalid object and logs
  `This study does not support response table definitions (Study::CreateEfficiencyMapDefinition)`.
  Maps belong to an efficiency-map (response) study, normally created with JMAG's Efficiency Map
  wizard; the setters/getters used by `jmag_efficiency_map` (SetMaxVoltage/MaxVoltage, ...) are
  UNVERIFIED.

## Winding

- `mode='fem_coil'` creates one FEMCoil condition per phase via the verified `jmag_add_fem_coil`
  path; read-back shows the sub-conditions `PhaseU+1` (Direction2D 0) and `PhaseU-2` (Direction2D 1)
  with the slot parts, link `CoilU`, Turn written to the circuit component. Pre-check PASS.
- `mode='jmag_winding'` (CreateWindingRegion(name, 0) + SetSlots/SetPoles + AddPart(1, part) +
  CreateWinding('ThreePhase', name) + SetRegion + SetWindingScheme('Manual') + SetLayers +
  SetCoilTable) **crashed Designer** within 6 s on the SPM model. Which call crashed was not isolated.

## Exporters and report

- `ResultTable.WriteAllTables(path, 'Time')`: CSV with `"Study title: Load"`, then per table
  `"Torque: Nm <Value> "`, `"Time s","Torque"`, rows. `WriteAllCaseTables` adds a `"Case Range"` row
  with case labels; `ResultTableData.WriteTable(path, 'Time')` writes one table. All return None.
- `Study.CreateTableDefinition()` + `SetResultType('MagneticFluxDensity', '')` + `SetComponent('Absolute')`
  + `Study.ExportTable(def, path, 0)` returned False and logged `There is no selected item.
  (Study::ExportTable)`: field tables need a selection the API call does not make.
- A first probe sequence `ExportTable -> ExportResultValuesTable -> WriteCaseReport -> WriteAllTables`
  crashed Designer; WriteCaseReport and WriteAllTables later worked alone, so the suspect is
  `ExportResultValuesTable` (refused by the bridge unless `force=true`).
- `Study.WriteCaseReport('<path>.html')` returns True and writes the HTML plus `<name>_images/` (2 s).

## Optimisation table

- `Study.GetOptimizationTable()`: `NumParameters/NumObjectives/NumExpressions`,
  `GetParametricItem(i)` -> ParametricRange (`GetParameterName`, `GetItemName`, `GetMin`, `GetMax`),
  getters `CSVOutputPath()`, `IsImmediateCSVOutput()`, `StopOnFailedCase()`, `NumJobs()`,
  `NumSimultaneousStudies()`, `CreateInitialCases()` all read without errors. Adding objectives and
  running the optimiser: UNVERIFIED.

## Experiments (jmag_experiment) - measured on the 12s/4p SPM, 30 steps per period

- Circuit components report display names from `GetType()` (`'Three-phase Current Source'`,
  `'FEM Coil'`, `'Ground'`); `experiment_setup` finds sources by that text.
- `back_emf` at 1500 rpm: psi1 = 0.0586 Wb, E1 = 18.4 V peak (13.0 V rms) per phase, phase shift
  119.9/120.6 deg, imbalance 1.6 %, THD 37 % (strong 3rd harmonic of the radial 80-degree magnets).
- `cogging` at 60 rpm over 30 mech-deg (one cogging period, 30 steps): 6.06 Nm pk-pk, dominant order
  12 per revolution (= lcm(12, 4)) as expected.
- `load_point` 10 A, PhaseU -90 deg, 1500 rpm: mean 1.793 Nm; power balance rel. diff 0.9 %; consistent
  with 1.5 p psi1 I = 1.758 Nm.
- `inductance_static` (Static2D, six Current conditions, 40 turns, 10 A): d-axis at
  `d_axis_elec_deg=90` for this geometry (magnet 1 centred at 0 deg, phase-U coil axis at 45 mech-deg):
  psi_pm,d = 0.0550 Wb with psi_pm,q ~ 1.6e-6 Wb; Ld = 0.367 mH, Lq = 0.366 mH (SPM: saliency 1.00);
  phase-U self inductance (magnet flux removed) 0.286 mH. With -90 deg the sign of psi_pm,d flips
  (the tool now warns).
