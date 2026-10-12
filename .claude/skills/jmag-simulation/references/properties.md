# Verified property names (JMAG-Designer 23.0)

Use with `jmag_set_properties` / `jmag_get_properties`. Values are in model units (mm, s, deg, rpm,
A, V) unless you pass `{"value": x, "unit": "..."}`. Strings are taken as equation names/expressions.
For anything not listed: `jmag_get_properties(with_help=true)` and `jmag_docs_search`.

## kind = "study" (analysis control)
| Name | Meaning |
|---|---|
| ModelThickness | 2D stack length [mm] (default 1!) |
| ConversionFactor / FullModelConversion | partial-model scaling |
| NonlinearMaxIteration (15), NonlinearTolerance (1e-3) | Newton settings |
| DirectSolverType, IccgTolerance, IccgMaxIteration | linear solver |
| UseMultiCPU, MultiCPU | parallel solve |
| Energy | 1 = output stored-energy table (needed for `check='energy'`) |
| CsvOutputPath, CsvResultTypes | JMAG's own CSV output |
| TimePeriodicEEC, TimePeriodicType | time-periodic (TP-EEC) fast steady state |
| Adaptive, MaxAdaptiveIteration, ErrorThreshold | adaptive mesh |

## kind = "step" (transient)
StepType (0 simple: Step + TimeStep; 1 uniform: Step + EndPoint; 2 time table), Step, TimeStep [s],
EndPoint [s], StepDivision.

## kind = "mesh"
| Name | Meaning |
|---|---|
| MeshType | 0 normal, 1 slide (rotating machines), 2 patch |
| AutoSlide | 1 = slide follows the motion condition |
| CircumferentialDivision, RadialDivision | slide-mesh resolution |
| AutoMeshSize / MeshSize | global size |
| AirRegionScale | outer air box scale (2.5) |
| 2dMeshingMethod, PartMeshDensityGradient | mesher details |
Local sizes: `jmag_mesh_size(parts, size)` (mesh condition type `Part`, property `Size`).

## Conditions
| Type | Key properties |
|---|---|
| Current (static coil) | Turn, Current, Direction2D (0 up / 1 down) |
| FEMCoil (+ sub-condition FEMCoilData) | sub: Direction2D; link -> circuit `Coil` (Turn, Resistance, LeakageInductance) |
| FEMConductor (+ FEMConductorData) | sub: Direction2D; link -> circuit `FEMConductor` |
| RotationMotion | AngularVelocity [rpm], InitialRotationAngle [deg], LoadType, Periodic |
| Torque / Torque_Surface / LorentzTorque | Axis2D, Origin, ExpandTarget |
| Force / Force_Surface / LorentzForce | CoordinateSystem |
| RotationPeriodicBoundary, SymmetryBoundary, EddyCurrent, Slide, Ironloss | see `jmag_get_properties` |

## Circuit components
| Type | Key properties |
|---|---|
| Coil | Turn, Resistance, LeakageInductance |
| CurrentSource / VoltageSource (1 terminal) | function via `source`/`function` (sin, constant) |
| 3PhaseCurrentSource / 3PhaseVoltageSource | Amplitude, Frequency, PhaseU, CommutatingSequence |
| Resistor, Inductor, Capacitor | value properties (list with `jmag_get_properties(kind='component')`) |
| VoltageProbe, CurrentProbe, Ground | - |

## Materials (`kind = "material", name=<part>`)
EddyCurrentCalculation (0/1), Laminated, LaminationFactor [%], LaminatedType, TemperatureType,
ConstantTemperature, BrCorrection, MagnetizationCorrection, UseAnisotropicMagnet.
Magnet orientation patterns: Parallel, Radial, Circular, ParallelCircular, RadialCircular,
PolarCircular, RadialSineCircular, ParallelSineCircular, ... (+ `outward`).

## Custom materials (`jmag_create_material`, verified on 23.0)
Two script views of one custom material: `lib.CreateCustomMaterial(name, folder)` returns a
MaterialObject (`MagneticType`, `MagneticPermON_BH`, `Physical_MassDensity`, `Loss_*` ...);
`lib.GetUserMaterial(name)` returns the Material-Editor view (scriptMaterialEditor page).
Use the Material-Editor view for the magnetic definition:
`MagneticPropertyType` 0 = MagneticSteel (1 = Magnet, 2 = MagnetizationMaterial),
`MagneticSteelPermeabilityType` 2 = BHCurve, table `BhTable` = [[H A/m, B T], ...],
`Density` plain value in g/cm^3 (use `SetValueWithUnit("Density", 7650, "kg/m^3")`),
`ConductivityType` 0 + `Conductivity` [S/m]. Protected library steels report MaterialObject
`MagneticType` 0 ("PermON_BH") and an empty B-H table; copying that flag makes a MAGNET with a
B-H demagnetisation table (`IsMagnet()` True, solver: "Check the point array setting in
material ..."). The iron-loss constants are written to the MaterialObject `Loss_Constant*`
properties - their unit convention is not verified yet.

## Result tables (display name -> internal key)
Torque, Circuit Current (LineCurrent), Circuit Voltage (TerminalVoltage), Coil Flux-Linkage
(FEMCoilFlux), FEM Coil Inductance (FEMCoilInductance), Stored Energy (StoredEnergy), Joule Loss,
Iron Loss, Hysteresis Loss, Circuit Electric Power, Total Rotational Displacement,
Magnetic Flux of Current Condition / Current of Current Condition (static Current conditions).
