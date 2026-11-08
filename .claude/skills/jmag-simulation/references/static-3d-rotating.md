# 3D Static: rotating machines (section 8)

Part 2 of 3 of the 3D Static reference (index: `static-3d.md`). Basics: `static-3d-basics.md`; nonlinear steel and radial machines: `static-3d-nonlinear.md`.

Contents: 8.1 What a 3D Static study offers - 8.2 Outer and periodic boundaries - 8.3 Magnets - 8.4 Coils: slit tooth coils with leads - 8.5 Torque - 8.6 Rotor positions - 8.7 Cogging is mesh-noise limited - 8.8 Cross-validation numbers and traps - 8.9 Arbitrary rotor angles

## 8. Rotating machines in 3D Static

**VERIFIED (23.0, 2026-10-20)** on an axial-flux surface-PM machine, 12 slots / 10 poles, linear
materials (steel mu_r 2000, magnets B_r 1.2 T / mu_r 1.05, axial and alternating), 30-turn tooth
coils; full 360 deg model, 622 k elements / 111 k nodes; 13 rotor-position cases (no load at 10
angles, load at 3) and one 180 deg periodic sector. Compared with an independent 3D FE solver on the
same geometry and air box.

### 8.1 What a 3D Static study offers

`GetConditionTypes()` (29, snippet S-3D-9): SymmetryBoundary, NaturalBoundary,
RotationPeriodicBoundary, TranslationPeriodicBoundary, Force, LorentzForce, Force_Surface, Torque,
LorentzTorque, Torque_Surface, EddyCurrent, StressDistribution, MagneticFlux, PartialModel,
ExternalField, LineCurrent, Gap, Current, CurrentDensity, Ironloss, Magnetostriction,
ResidualStrain, FrozenPermeability, TemperatureDistribution, Displacement, ReuseDemagnetization,
VectorPotentialBoundary, TopologyOptimization, SensitivityAnalysis. **No `RotationMotion`**: a Static
study is one rotor position, so a cogging or torque-vs-angle curve is a set of cases (8.6).

Property names read with `GetPropertyNames()` / `GetPropertyTable()`:

| Condition | Properties |
|---|---|
| `RotationPeriodicBoundary` | `Axis`, `Origin`, `BoundaryType` (keys `Normal` / `Reverse` / `AxialReverse`), `AutoPeriodicFromWinding`, `Angle` |
| `Torque` | `Origin`, `Axis`, `ExpandTarget` |
| `Torque_Surface` | `Origin`, `Axis` |
| `SymmetryBoundary` | none (always flux parallel) |
| `Current` | `Turn`, `Current`, `Direction`, `Origin`, `UniformCurrent`, `DirectionType` |

`c.GetValue(name)` of a property the condition does not have logs "No property such as ..." and
fails the worker call even inside `try` (seen with `TargetType` on `Torque`): read
`GetPropertyNames()` first.

### 8.2 Outer and periodic boundaries

| Boundary | Set-up | Status |
|---|---|---|
| Outer box, flux parallel (= `A_t = 0`) | `SymmetryBoundary` on **every** outer face of the air box, lead end faces included | VERIFIED (23.0, 2026-10-20) |
| A face with no condition | JMAG's natural boundary = flux normal; differs from `A_t = 0` | documented (manual "Symmetry Boundary Condition and Natural Boundary Condition"), avoided |
| Periodic sector | `RotationPeriodicBoundary` with `Origin` (0,0,0), `Axis` (0,0,1), `Angle` = sector angle, `AutoPeriodicFromWinding` 0, `BoundaryType` 1, on the faces of **one** cut plane only (nothing on the other); snippet S-3D-6 | VERIFIED (23.0, 2026-10-20) |
| `BoundaryType` | integer in key order: 0 `Normal`, 1 `Reverse`, 2 `AxialReverse`. 1 Reverse = anti-periodic, the choice for a sector spanning an odd number of poles; Normal (periodic, even number of poles) by name | `BoundaryType` 1 = anti-periodic VERIFIED; 0 and 2 UNVERIFIED |
| Sector results | `CoilFlux`, `StoredEnergy` and the nodal `Torque` of the sector come out already converted to the full model: **do not multiply by the symmetry factor** (factor 1; unlike the half model of `static-3d-basics.md` sections 2-3, which needed x2) | VERIFIED (23.0, 2026-10-20; torque at a loaded position, section 8.5) |
| `FullModelConversion` / `ConversionFactor` | defaults read on the study: `FullModelConversion` 1, `ConversionFactor` 1; all sector results above were taken with these defaults, left unchanged | defaults VERIFIED; non-default values UNVERIFIED |

Check: 180 deg sector (6 slots / 5 poles, `Reverse`), theta = 0, no load: psi_A 50.413 / psi_B
-25.414 mWb vs the full model 50.400 / -25.428 (+0.03 % / -0.06 %), energy 3.8257 vs 3.8245 J,
311 k elements, solve 15 s. Torque at that position is cogging-zero (sector 0.0009, full -0.0054
N m, both mesh noise), so a second, loaded sector case tests the torque: theta = 3 deg, I_q = 5 A,
nonlinear steel (`static-3d-nonlinear.md` section 9), same 180 deg `Reverse` set-up: rotor `Torque` 1.8626 N m vs the full
model 1.8822 (ratio 0.990, the per-mesh nodal-torque noise of 8.7), psi_A 47.488 vs 47.474 mWb
(+0.03 %), 311 k elements, solve 48 s. A raw half-model torque would be about 0.94 N m, so the
reported value is the full-model one: **factor 1 for torque too**.

### 8.3 Magnets

- Custom linear magnet (snippet S-3D-7): `CreateCustomMaterial`, `GetUserMaterial`,
  `MagneticPropertyType` 1 (magnet), `MagnetPermeabilityType` 6 (Linear(Br)),
  `ResidualMagneticFluxDensity` B_r [T], `MagnetPermeability` mu_r; read back, `IsMagnet()` True.
  VERIFIED (23.0, 2026-10-20).
- The library `NdFeB_Br=1.2(T)` gave the same flux linkage as the custom B_r 1.2 T, mu_r 1.05 magnet
  to 1e-8. Recoil mu_r matters: 1.00 instead of 1.05 moved psi by +1.2 %.
- Direction per part: `s.SetMaterialByName(part, mat)`, then `mt = s.GetMaterial(part)`,
  `mt.SetPattern("Parallel")`, `mt.SetDirectionXYZ(0, 0, +-1)`, read back with `mt.GetDirection()`.
  Reversing every magnet negated psi exactly. VERIFIED (23.0, 2026-10-20).

### 8.4 Coils: slit tooth coils with leads

A closed tooth coil has no face on the model's outer surface, so InflowFace cannot find an outflow
face (`static-3d-basics.md` section 2). The verified route:

1. In the CAD, slit each coil (0.5 mm) at its outer end turn and run two radial leads from the slit
   to the outer wall of the air box (`jmag-geometry`); label the two lead end faces in the labels
   JSON.
2. `SymmetryBoundary` on every outer face of the box, the lead end faces included (the solver finds
   the outflow face there).
3. `Current` per coil (snippet S-3D-2): `Turn` N, `DirectionType` 0, part = the coil, secondary
   face = the "in" lead end face. Which way the current then circulates follows from the lead
   geometry: check the sign of one `CoilFlux` against a hand estimate.
4. Phase quantities: `i_coil = polarity x i_phase` from the winding table, `psi_phase = sum polarity
   x CoilFlux` over the coils of the phase (`CoilFlux` includes the turns).

Verified details: a negative `Current` reverses the current while `CoilFlux` keeps its orientation
(superposition held to 8e-9); `UniformCurrent` 0 and 1 both conserve the terminal current through
every cross-section and differ by < 0.3 % in psi. This is the stage-1 rule "inflow and outflow faces
on the model's outer surface" confirmed for faces on the outer wall instead of a cut plane.

### 8.5 Torque

- Use the **nodal `Torque` condition** on the rotor parts (iron + magnets): `SetXYZPoint("Origin",
  0, 0, 0)`, `SetXYZPoint("Axis", 0, 0, 1)`, part selection (snippet S-3D-8). Result table key
  `Torque`, one column per condition, torque about +axis in N m. `ExpandTarget` 1 changed nothing (to
  all digits). A second condition on the stator parts gives about minus the rotor torque (load:
  rotor 1.906, stator -1.933 N m). VERIFIED (23.0, 2026-10-20).
- **Periodic sector: the reported `Torque` is already the full-model value (factor 1)** with the
  defaults `FullModelConversion` 1 / `ConversionFactor` 1: a 180 deg `Reverse` sector at a loaded
  position gave 1.863 N m on the rotor vs 1.882 N m from the full model (8.2). The stator condition
  reports the opposite sign (sector -1.925, full -1.894 N m): it is the reaction torque, not a
  second copy of the rotor torque. Do not multiply by 360 / sector angle. VERIFIED (23.0,
  2026-10-20).
- **`Torque_Surface` does not work for this**: a part selection is not accepted (`NumParts` reads 0)
  and Run aborts with "The target is not specified for the electromagnetic force or torque
  calculation condition"; selecting the internal mid-gap face by position picks both sides, whose
  contributions cancel (~1e-14 N m). VERIFIED negative (23.0, 2026-10-20).

### 8.6 Rotor positions: one STEP per angle

No `RotationMotion` in Static, so each rotor angle is its own model: write one merged STEP per angle
(stator + rotor rotated by theta + air, with the gap split into a stator-side and a rotor-side slab
for a gap mesh size), import, rename, set up and run it, then the next angle. Re-import per case is
the cheap verified route (~15 s overhead per case). Load cases: duplicate the no-load study
(`jmag_duplicate_study`) and set the coil currents from the dq currents at that angle.

Timing (Ryzen 7 3800X, 622 k elements, other load on the machine): ~80 s per no-load case (import +
set-up ~15 s, mesh ~30 s, solve ~40 s), ~160 s with a load study added; 13 cases ~20 min. The 311 k
element 180 deg sector solved in 15 s.

Mesh used: `AirRegion` 0, `AutoMeshSize` 0, air `MeshSize` 10 mm, `Part` sizes gap slabs 0.6,
magnets 1.5, steel 3, coils 3 mm. Going from 302 k to 622 k elements moved psi by +0.3 %.

### 8.7 Cogging is mesh-noise limited

Every angle gets a new mesh and nodal torque is a small difference of large forces: per angle JMAG
scattered by up to 0.035 N m against a sliding-mesh reference (0.33 N m peak-to-peak), and theta = 0
and 6 deg, which are the same position of this 12/10 machine (cogging period 6 deg), gave -0.005 and
+0.006 N m. Report peak-to-peak over a period, not single points, and refine the gap until
peak-to-peak settles (`jmag-experiments` -> `references/pmsm.md` section 2b).

### 8.8 Cross-validation numbers and traps

JMAG 23.0 vs an independent 3D FE solver (same geometry, same flux-parallel box): psi_A -0.2 to
-0.6 % over all angles; load torque within 0.3 %; cogging peak-to-peak 0.331 vs 0.332 N m (-0.3 %).

1. `StoredEnergy` in a model with magnets is not 1/2 sum(nu |B|^2 V): JMAG 3.82 J vs 9.27 J from the
   other solver, both constant to 0.1 % over the angles. Its definition with magnets is not in the
   help we have: **do not compare energies across tools when magnets are present**; compare psi and
   torque.
2. The reference's coil model matters: imposing a uniform current density |J| = N I / A_mean in a
   coil whose cross-section grows with radius over-stated psi and torque by ~10 %; JMAG's InflowFace
   current conserves N I through every section (as an electrokinetic coil-current solution does,
   which then agreed). Check the reference's coil model before blaming JMAG.
3. Leaving outer faces without a condition gives the natural (flux-normal) boundary, not `A_t = 0`.
4. Multiplying a periodic sector's `CoilFlux` or `Torque` by the symmetry factor double-counts (8.2,
   8.5).
5. `Torque_Surface` and the both-sides face selection (8.5).
6. `GetValue` of a property the condition lacks fails the call (8.1).
7. Geometry side: gmsh's `isInside(2, face, p)` ignores face trimming (a lead-end centre tested
   "inside" 5 faces); find small lead faces by bounding box and place boundary-face points on a
   coarse surface mesh, away from the face edges.
8. No modal dialog and no crash in the 2026-10-20 runs; every Designer quit at the end.

### 8.9 Arbitrary rotor angles (off-step positions)

The per-angle STEP route of 8.6 takes any rotor angle: nothing is tied to a mesh step, because every
case is its own geometry and mesh. VERIFIED on 23.0, 2026-10-20, same machine, settings and mesh sizes
(621-622 k elements): no load at 0.7, 1.3 and 2.9 deg and I_q = 5 A at 0.7 deg, against an
independent 3D FE solver that reaches these angles with a non-conforming (mortar / interpolation)
sliding interface: psi 0.6 % at every angle (the same offset as on the 1 deg grid), load torque
0.3-0.7 %, cogging within 0.019 N m (5.4 % of 0.357 N m peak-to-peak; the same solver pair scattered
by up to 0.028 N m on the 1 deg grid). Measured noise of this route: the off-step points lie on the
periodic cubic through JMAG's own on-step points (0..5 deg, period 6 deg) to 0.007 N m (2 % of its
peak-to-peak) and 1e-4 in psi. Cost as 8.6: 28-30 s mesh + 37-41 s solve, 77-80 s per no-load case,
160 s with a load study. So for a cogging curve at arbitrary angles write one STEP per wanted angle;
sample finely enough to resolve the peaks (a cubic through 1 deg points on a 6 deg period missed a
peak by 0.006 N m on the reference curve, the same size as the remeshing noise), and quote the
period-end mismatch as the noise level (8.7).
