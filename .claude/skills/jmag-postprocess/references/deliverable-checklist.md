# Deliverable structure and checklist

Contents: 1 Structure - 2 Number citation format - 3 Checklist - 4 Wording rules

## 1. Structure (what `jmag_make_report` should contain; add what is missing)

1. **Summary**: the question, the answer in 3-5 bullet numbers, overall confidence
   (verified / consistent / doubtful) and why.
2. **Model**: device, geometry source (script + parameters + labels file), 2D/3D, symmetry,
   stack length, materials per part, magnet orientation, winding (turns, layout, connection),
   circuit image.
3. **Study setup**: study type, excitation, speed, steps (requested and actual), mesh (element
   count, gap divisions, local sizes), solver settings changed from default.
4. **Verdicts**: pre-check and post-check verdicts with every WARNING/ERROR code and the action
   taken; convergence summary.
5. **Results**: tables of metrics with sources; plots (waveforms, spectra, sweeps); contour
   images with the step and the colour range.
6. **Validation**: physics invariants (KVL / energy / power balance) with residuals; hand
   estimates with formula, value and ratio; mesh sensitivity.
7. **Assumptions and limitations**: 2D end effects, temperature, material data, loss-model
   approximations, unverified API paths used.
8. **Open doubts and next steps.**
9. **Reproduction**: journal replay script (`jmag_journal(export_script=...)`), geometry
   script version, bridge/JMAG version, exported CSVs.

## 2. Number citation format

`1.774 Nm  [study TR_load, table Torque, column Torque, mean over last electrical period
t = 20-40 ms, 60 samples]`

Derived numbers cite their inputs and formula:
`T_est = 3/2 p psi_1 I = 1.777 Nm  [psi_1 = 0.059 Wb from Coil Flux-Linkage/CoilU
last-period fundamental; I = 10 A peak; p = 2]`.

## 3. Checklist

- [ ] Every number has a unit and a source (study / table / column / window) or a formula.
- [ ] Pre- and post-check verdicts given; every CHECK/FAIL finding listed with its code.
- [ ] At least one physics invariant per headline result, with its residual.
- [ ] At least one hand estimate per headline result, with the ratio and the explanation.
- [ ] Steady state shown (`periodicity.steady`) or the transient nature stated.
- [ ] Stack length stated (2D) and correct (`DEFAULT_STACK_LENGTH` absent).
- [ ] Mesh sensitivity stated (or explicitly "not done").
- [ ] Sign conventions stated (torque sign, current angle reference, dq axis choice).
- [ ] Images: model, contour at a stated step, circuit for circuit-driven studies.
- [ ] Unverified paths (marked "unverified on 23.0" in the skills) that the result depends on
      are listed.
- [ ] Files written outside the repository; paths given in full.

## 4. Wording rules

- "The run finished" is not a result. "Torque 1.774 Nm, consistent with 1.777 Nm from flux
  linkage (0.2 %), power balance 0.12 %" is.
- Distinguish **verified** (invariant + estimate agree), **consistent** (one check),
  **unchecked**, and **suspect** (a check failed). Say which applies to each headline number.
- Never round away a discrepancy; never omit a FAIL because the numbers "look fine".
