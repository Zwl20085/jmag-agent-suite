---
name: jmag-parametric-optimisation
description: Parametric studies, sweeps, DOE and optimisation in JMAG-Designer through the jmag MCP tools - study equations, design-table cases (jmag_case_table), batch runs (jmag_run_cases), runtime budgeting, geometry variables (regenerate DXF per case vs equation-driven JMAG parameters), mesh-convergence and mesh-noise traps, JMAG's optimisation table (jmag_optimization_table), Pareto reporting, and efficiency / torque-speed / flux maps as sweeps. Use when the user wants to sweep a parameter, compare design variants, run a DOE, optimise torque/ripple/loss/cost, build a map over operating points, or asks how many runs a study will take.
---

# Parametric studies and optimisation

A sweep is many single studies; each one must still pass the `jmag-simulation` ladder. The
extra failure modes are budget (too many runs), noise (mesh changes masquerading as design
effects) and topology (a geometry change that breaks part names or the slide mesh).

## Choose the variable type first

| Variable | Examples | How | Cost |
|---|---|---|---|
| Operating point | current, angle, speed, temperature | study equations (`jmag_set_equations`) used as property values, cases in the design table | cheap, mesh unchanged |
| Material / setting | steel grade, turns, stack length | equations or `jmag_duplicate_study` + setting | cheap |
| Geometry | magnet arc, slot opening, bridge width | regenerate the DXF per case and import it as a new model (robust) | re-import + re-setup per case |
| Mesh (convergence) | gap divisions, local sizes | `jmag_duplicate_study` + `jmag_mesh_size` | one-off, do it first |

Equations: `jmag_set_equations(study, {"I": 10, "rpm": 1500, "beta": 30})`, then use the names
as values (`{"AngularVelocity": "rpm"}`, `{"Amplitude": "I"}`). Verified on 23.0 (equations
live in the study's design table).

Geometry from a script: JMAG's CAD-parameter route (`DesignTable.AddCadParameter`, "adds
parameters of the CAD model as design variables") needs a parametric model inside JMAG's
Geometry Editor or a linked CAD; a DXF import has no live parameters (**unverified on 23.0**
whether any import route keeps them). So for external geometry: script -> DXF + labels per case -> `jmag_import_dxf` into a new model -> the same
setup sequence (keep it as a reusable list of tool calls) -> run. Keep part names identical
across variants (`jmag-geometry` labels) so setup and post-processing are reusable.

## Workflow

1. **Baseline**: one validated study (ladder rungs 1-5 passed). Record its runtime `t1` from
   `jmag_run` (`seconds`).
2. **Mesh study** (moved from the old recipes): `jmag_duplicate_study` -> `jmag_mesh_size` on
   air gap / teeth -> rerun -> compare torque/inductance; < 1-2 % change means mesh-converged.
   For geometry sweeps also estimate **mesh noise** (below).
3. **Budget**: `N_cases x t1 / parallel_jobs` (+ re-import time for geometry cases). Ask the
   user before anything above ~1 hour or ~50 runs; licences limit parallel solves
   (`jmag_capabilities`).
4. **Cases**: `jmag_case_table` (verified on 23.0). Cases are 0-based, case 0 = base case.
   `action='get'` shows parameters, cases, values, `has_result`; `action='set'` with
   `add_parameters` (full names like `'Motion (RotationMotion2D): AngularVelocity'`, short
   `'Owner: Prop'`, or equation names - equations from `jmag_set_equations` join the table as
   `'Equation parameters: <name>'` by themselves), `cases=[{name: value}, ...]` to append,
   `set_cases`, `remove_cases`, `active`, `current_case`. Unknown names are rejected before
   anything changes. DOE points come from your own design (references), one dict per case.
5. **Run**: `jmag_run_cases(study, cases?)` - `method='loop'` (verified: SetCurrentCase + Run per
   case, progress and cancel via `jmag_run_status` / `jmag_cancel_run`); `all_cases` and
   `optimization` are unverified. Read case k with `jmag_get_result(..., case=k)` /
   `jmag_list_results(..., case=k)`. A failed case is data (record why), not a reason to stop.
6. **Collect**: one metric table (case, variables, metrics, verdicts); `jmag_compare` for
   pairs, `jmag_plot` for sweeps/maps, `jmag_export_table` for case values.
7. **Interpret**: effect sizes vs noise, sensitivities, constraints, Pareto front
   (`references/doe-optimisation.md`).

## The mesh-noise trap

Every geometry change remeshes; a torque-ripple or cogging metric can move 1-5 % from the mesh
alone. Before trusting a trend:
- Repeat the baseline with a tiny geometry perturbation (1e-3 mm) - the metric change is the
  noise floor.
- Use fixed absolute local sizes in gap/teeth/bridges (not "auto") and a fixed
  `CircumferentialDivision`.
- A design effect smaller than ~3 x the noise floor is not an effect.
- Optimisers chase noise: smooth/surrogate methods and a final mesh-converged rerun of the
  chosen design are mandatory.

## Optimisation

- JMAG's own optimiser: `jmag_optimization_table(action='set', parameters=[{name, min, max}],
  objectives=[{name, expression, type, weight}], constraints=[{name, expression, type, value}],
  csv_output_path=...)` (variables must be design-table parameters first), then
  `jmag_run_cases(study, method='optimization')`. **Unverified on 23.0**, needs the optimiser
  licence; objective expressions refer to response values defined in JMAG.
- External loop (works with any licence): DOE (LHS) -> surrogate -> optimiser proposes cases
  -> `jmag_run_cases` -> repeat; geometry via your script. Details and Pareto reporting:
  `references/doe-optimisation.md`.
- Always rerun the chosen design(s) as full validated studies (ladder + mesh check) before
  reporting; optimiser outputs are candidates, not results.

## Maps as sweeps

Efficiency, torque-speed and flux maps are sweeps over operating points on one geometry:
- Flux map psi_dq(i_d, i_q): grid of current magnitude x angle (or i_d x i_q), one load point
  per case, steady last period, Park transform (`jmag-experiments`).
- Torque-speed / efficiency: post-process the flux map + loss model with voltage/current
  limits, or JMAG's efficiency map (`jmag-losses-efficiency`).
- Grid sizing: 6-10 points per axis is typical; refine near MTPA and the current limit.

API members for design tables, analysis groups and the optimisation table:
`references/design-table-api.md`.
