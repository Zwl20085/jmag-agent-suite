# Efficiency maps: definition, API members, validation

Contents: 1 What a map is and how to validate it - 2 EfficiencyMapDefinition members (23.0) -
3 Related classes - 4 Hand-built maps

Every member below is copied from the JMAG-Designer 23.0 script reference (signatures as
documented, including the API's own spelling such as `SetMechancialLossFactor`). None has been
exercised end-to-end through the bridge yet: **the whole map workflow is unverified on 23.0**.
JMAG evaluates maps in an efficiency-map (response) study, normally created with its Efficiency
Map wizard; `CreateEfficiencyMapDefinition` on an ordinary Transient study may return an invalid
object. Prefer `jmag_efficiency_map` (it reads every setting back); use these members via
`jmag_exec_python` only when the tool lacks an option.

## 1. What a map is and how to validate it

An efficiency map is a grid over (speed, torque) with, at each point, the current vector chosen
by a control strategy within current and voltage limits, and the resulting losses. JMAG builds
it from a response table of flux linkage/torque/loss vs current computed by a set of FEA cases,
then interpolates (as the `ResponseTableDefinition` / `ResponseCreateInfo` classes in the 23.0
reference indicate; the exact case set JMAG generates is unverified).

Validation (always, before reporting):
1. Pick 3+ points: low speed/high torque, base speed/rated torque, high speed/field weakening.
2. Run each as a full transient load point (same current vector the map chose) + loss study.
3. Compare torque (< 2-3 %), losses (< 10-15 %) and efficiency (< 0.5-1 percentage point).
4. Check the envelope: max torque at low speed = MTPA at `I_max`; the corner speed matches the
   voltage limit with the map's `V_dc` (`jmag-experiments` pmsm section 7).
5. Report control scheme, current/voltage limits, temperatures, which losses are included and
   which are approximations (PWM, AC copper, magnet, mechanical).

## 2. EfficiencyMapDefinition members (23.0 reference)

Created/obtained from the study:
- `EfficiencyMapDefinition * Study::CreateEfficiencyMapDefinition(String & name)`
- `EfficiencyMapDefinition * Study::GetEfficiencyMapDefinition(Variant & val)`

Loss options:
- `bool AddACLoss()` - whether approximate AC copper loss is added.
- `bool AddPWMIronLoss()` - whether approximate PWM iron loss is added.
- `void SetAddMagnetLoss(bool addMagnetLoss)` - add approximate PWM magnet loss.
- `void SetMagnetConductivity(int index, Variant & conductivity)` - magnet conductivity for it.
- `double CarrierFrequency()` - carrier frequency used for the approximate PWM losses.
- `void SetLossCorrection(bool correction)`, `void SetLossCorrectionTable(String array & array)`,
  `String CorrectionType()` ("none", "efficiency" or "loss").
- `void SetMechancialLossFactor(Variant & factor)` - mechanical loss coefficient.

Control and torque:
- `String ControlName()` - basic control scheme: "mtpa", "mtpa+fw", "unitypf" or "id=0".
- `void SetTorqueType(Variant & type)` - "shaft" or "electromagnetic".

Temperature corrections:
- `void SetCoilTemperatureCorrection(bool use)`, `void SetRotorConductorTemperatureCorrection(bool use)`,
  `void SetFieldCoilTemperatureCorrection(bool use)`, `void SetMagnetTemperatureCorrection(bool use)`
- `void SetMagnetCorrection(double initialValue, double endValue, int division)`
- `void SetMagnetCorrectionBaseTemperature(Variant & value)`,
  `void SetMagnetCorrectionTemperature(int index, Variant & temperatureValue)`,
  `void SetMagnetCorrectionCoefficient(Variant & value)`, `int GetMagnetCorrectionType()`
- `double GetInitialMagnetTemperature()`, `double GetEndMagnetTemperature()`

## 3. Related classes (23.0 reference)

- Export: `void Study::ExportResponseTable(String & filename, Variant & nameOrIndex, int caseIndex)`.
- Map results: `EfficiencyMapPlot::ExportTorqueTable(filename, start, end, division, motorMode,
  temp1 = 0.0, temp2 = 0.0)`, `EfficiencyMapPlot::GetTorqueTableResult(type, speed, temp1, temp2)`.
- NT curve graph: `DataManager::CreateNTCurveGraphModel(EfficiencyMapDefinition * data, int type,
  String & title)`.
- Comparing two maps: `Study::CreateDifferenceEfficiencyMapDefinition(name)` with
  `SetBaseEfficiencyMap`, `SetOtherEfficiencyMap`, `SetCalculationType`, `SetRelationTable`.
- Response values at a torque/temperature: `Application::CreateEfficiencyMapResponseDataParameter(name)`
  (`SetMagnetTemperature`, `SetCoilTemperature`, `SetTorqueValue`) and
  `Study::CreateEfficiencyMapParametricData(nameOrIndex, parameter)`.
- Loss items of the response table: `ResponseTableDefinition::SetLossItem(index, title, name,
  subtractTorque)`, `SetTotalLossResult(name, subtractTorque)`, `SetCustomizeLoss(bool)`;
  `ResponseCreateInfo::SetUseStrayLoss`, `SetStrayLoss`, `SetTorqueCondition`,
  `SetMaximumCurrent`, `SetMaximumSpeed`, `SetBaseVoltage`, `SetPole`.

## 4. Hand-built maps (when the JMAG map is unavailable or in doubt)

1. Flux map: `jmag_case_table` grid over (i_d, i_q) (e.g. 8 x 8 within the current circle),
   `jmag_run_cases`, read `psi_d, psi_q, T` and the loss study per case.
2. For each (speed, torque) target: choose the minimum-loss (or MTPA/MTPV) current vector that
   satisfies the current and voltage limits (`jmag-machine-design` formulas, section 6).
3. Iron loss scaling between speeds: hysteresis ~ f, eddy ~ f^2 at the same flux pattern
   (better: run the loss study at 2-3 speeds and fit).
4. `eta = P_out / (P_out + P_cu + P_fe + P_mag + P_mech)`; plot with `jmag_plot`, validate as in
   section 1.
