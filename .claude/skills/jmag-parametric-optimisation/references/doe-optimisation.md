# DOE, surrogate optimisation, Pareto reporting

Contents: 1 Choosing a design of experiments - 2 Runtime budgeting - 3 Sensitivities -
4 External optimisation loop - 5 Pareto reporting - 6 Checklist

Standard engineering practice; nothing here depends on unverified JMAG behaviour except where
it calls a tool.

## 1. Choosing a design of experiments

| Goal | Design | Runs for k variables |
|---|---|---|
| One variable trend | 1-D sweep, 5-9 levels, denser near the expected optimum | 5-9 |
| Two-variable map | full factorial grid | n^2 (n = 5-8) |
| Screening many variables | 2-level fractional factorial / Plackett-Burman | k+1 ... 2^(k-p) |
| Response surface (quadratic) | central composite / Box-Behnken | ~2^k + 2k + 1 / ~2k(k-1) + 1 |
| Surrogate for optimisation | Latin hypercube (LHS), maximin | 10 k (minimum), 20 k (better) |

Normalise variables to [0, 1] on their ranges; record the ranges and units in the case table.
Include the baseline as a case. Fix the random seed of any LHS and report it.

## 2. Runtime budgeting

- `t_case = t_solve (+ t_import + t_setup for geometry cases) + t_post`; measure on the
  baseline, do not guess (2D transient motor load point: minutes; 3D: hours).
- `T_total = N x t_case / parallel`; parallel solves are limited by licences and RAM
  (`jmag_capabilities`), and JMAG's own `UseMultiCPU` / `MultiCPU` per solve competes for cores.
- Cut cost: fewer steps (one electrical period after a converged start; time-periodic settings
  `TimePeriodicEEC` exist as study properties), symmetry sectors, coarser DOE then refine
  locally, static instead of transient where the metric allows (e.g. Ld/Lq, force).
- Tell the user the budget before starting and offer the cheaper variant.

## 3. Sensitivities

- One-at-a-time: `S_i = (dY/Y) / (dX_i/X_i)` around the baseline (central differences when
  possible); step large enough to beat mesh noise (SKILL.md "mesh-noise trap").
- From a DOE: linear/quadratic regression coefficients on normalised variables, with R^2 and
  the residual compared with the noise floor.
- Report the top 3 drivers per metric and any interactions that change sign.

## 4. External optimisation loop

1. LHS initial set (section 1) -> `jmag_run_cases`.
2. Fit a surrogate per metric (quadratic RSM, Gaussian process / kriging, RBF); check it by
   leave-one-out error vs the noise floor.
3. Optimise the surrogate (NSGA-II for multi-objective, or weighted sum / epsilon-constraint),
   propose 3-10 infill cases (best predicted + highest uncertainty).
4. Run the infill cases, refit, repeat until the front stops moving (< noise) or budget ends.
5. Geometry variables: the loop calls the geometry script per case; failed geometries
   (self-intersecting, bridge < minimum) are rejected by the script before JMAG sees them.
6. Constraints as hard filters on validated runs (verdict PASS/CHECK, power balance < 5 %,
   tooth B, J, back-EMF at max speed vs V_dc).

## 5. Pareto reporting

- Table of non-dominated designs: variables, objectives, constraint margins, verdicts, case id.
- Plot objective pairs (`jmag_plot`) with dominated points greyed, baseline marked.
- Rerun 1-3 chosen designs as full studies with a mesh check; report the rerun numbers next to
  the surrogate/case numbers (differences > noise mean the surrogate is unreliable there).
- State the noise floor and that differences below ~3 x noise are not significant.

## 6. Checklist

- [ ] Baseline validated, runtime measured, mesh-converged.
- [ ] Noise floor measured for geometry variables.
- [ ] Budget agreed with the user.
- [ ] Case table exported and cited (`jmag_export_table`).
- [ ] Failed cases listed with reasons.
- [ ] Chosen designs rerun and validated.
