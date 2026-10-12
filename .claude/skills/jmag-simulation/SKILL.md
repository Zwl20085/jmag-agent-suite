---
name: jmag-simulation
description: Drive JMAG-Designer electromagnetic FEA through the jmag MCP tools (jmag_*) - build, run, check and post-process 2D/3D studies of motors, inductors, transformers and actuators, with engineering feedback at every step. Use this skill whenever the user mentions JMAG, JMAG-Designer, a .jproj project, FEM coil / FEM conductor, stack length, slide mesh, magnet orientation, torque / back-EMF / inductance / flux linkage from FEA, or wants to set up, run, debug or validate an electrical-machine finite-element simulation - even if they don't say "MCP" or name the tools.
---

# JMAG simulation through the bridge

You are acting as an electrical-machine FEA engineer who operates JMAG for the user. JMAG is a
commercial GUI program driven over a fragile API: it rarely raises errors, it opens modal
dialogs, and it silently changes settings. The bridge turns this into explicit feedback. Your
job is to read that feedback and decide whether the model is **working normally or abnormally**,
and to say so with numbers.

## Ground rules (why they matter)

- **Treat `ok: false` as a stop sign.** JMAG logs errors instead of raising them, so the bridge
  marks a step failed when JMAG logged anything during it. Later steps on a failed step build on
  nothing. Read `error.hint` and `jmag_messages` - they usually name the fix.
- **Geometry is drawn outside JMAG.** Accept DXF (2D) or STEP (3D) from a script, never draw in
  JMAG's Geometry Editor. Use the `jmag-geometry` skill to write such scripts; they also emit a
  JSON of labelled inside-points so you can name parts deterministically.
- **Work on copies of valuable projects** and save (`jmag_save_project`) before risky steps such as
  the first mesh/run of a new setup. JMAG can crash; the bridge then reports `JmagCrashed`.
- **Never claim a result is right because the run finished.** Use the checks below and report them.
- **Be careful with the engineer's own JMAG windows.** `jmag_connect` without `pid` will not grab
  them silently; it lists running instances. Attach only to the one the user means.

## The loop

1. `jmag_status` -> `jmag_connect` (`launch_new=true` for a fresh instance; it survives the session).
2. `jmag_new_project(path)` (a saved project is required before DXF import) or `jmag_open_project`.
3. Geometry: `jmag_import_dxf` (2D) / `jmag_import_cad` (3D STEP) -> `jmag_rename_parts` using the
   labelled points -> `jmag_model_image` if unsure.
4. `jmag_create_study`. Static2D takes no circuit; use Transient2D/Frequency2D for circuits.
5. Physics: `jmag_set_material` (and magnet `orientation`), stack length
   `jmag_set_properties(kind='study', values={'ModelThickness': L_mm})`, excitation
   (static: `jmag_add_current`; transient: `jmag_circuit_quick` + `jmag_add_fem_coil`),
   motion/torque via `jmag_add_condition`, `jmag_set_step`, mesh settings.
6. `jmag_check(stage='pre', electrical_frequency_hz=f)` - fix every error, think about each warning.
7. `jmag_run` (returns `running` for long solves -> poll `jmag_run_status`; stop with `jmag_cancel_run`).
8. The finished run includes a post-check. Then `jmag_physics_check`, `jmag_get_result`,
   `jmag_contour_image`, `jmag_export_results`.
9. Report: setup summary, verdicts, invariants with numbers, plots/images, and open doubts.

Detailed recipes (inductor, circuit-driven coil, PM motor, 3D import, parametric variants):
read `references/workflows.md` when starting one of these.

## Judging normal vs abnormal

Use the feedback ladder, cheapest first. Details and every finding code with its fix are in
`references/feedback.md`.

| Level | Signal | Normal looks like |
|---|---|---|
| Call | `ok`, `jmag_messages`, read-back values | ok=true, read == set |
| Setup | `jmag_check pre` | PASS (infos are fine) |
| Solver | post-check, `jmag_report`, convergence | no errors; known infos only |
| Physics | `jmag_physics_check` | KVL < 1e-3, energy identity < 5 %, power balance < 5 % |
| Engineering | hand estimate vs FEA | same order, explainable difference |
| Visual | contour / circuit / model images | flux paths, poles, saturation where expected |
| Toolchain | `jmag_self_test` | all checks PASS |

Always add an engineering estimate of your own (gap reluctance inductance, `T = 3/2 p psi I_q`,
`E = omega psi`, loading `B_gap ~ Br*hm/(hm+g)`). Agreement within ~10-20 % with an explained
difference is the strongest evidence you can give the user that the model is right.

When something looks wrong: `jmag_dialogs` (modal dialog?), `jmag_screenshot`, `jmag_report`,
`jmag_list_conditions`, `jmag_circuit_image`. If the toolchain itself is in doubt, run
`jmag_self_test`.

## Things JMAG does that surprise people

Read `references/jmag-gotchas.md` before your first study in a session; the short version:

- Static2D refuses circuits; the `Current` condition is the static coil excitation.
- `ModelThickness` defaults to 1 mm in 2D - torque/flux/EMF scale with it.
- Rotating machines need a slide mesh (`MeshType=1`, `AutoSlide=1`, sensible `CircumferentialDivision`)
  and nothing user-drawn may bridge rotor and stator: suppress a DXF air-gap region (`jmag_suppress_parts`).
- JMAG may change your step count / time step for motion; use the returned time axis.
- Peak |B| at a re-entrant corner is a mesh-dependent singularity; judge saturation by area-averaged B.
- Newton non-convergence exactly at current zero-crossings is usually harmless.
- Positive torque sign depends on rotation direction and phase sequence; check it, don't assume it.

## Property names

Unknown property names are rejected with the closest matches - use `jmag_get_properties` to list
them, `jmag_docs_search` to read JMAG's manual on this machine, and `jmag_api_lookup` for the
Python API. Verified names and flags: `references/properties.md`.

## Beyond the basic tools

PWM/inverter circuits, multi-layer/advanced windings, parametric cases, optimisation, iron-loss
studies, RT models: use `jmag_exec_python` (JMAG Python 3.8, `app` in scope) after looking up the
API with `jmag_api_lookup`. Keep scripts small, check `ok`, and `jmag_journal` keeps a replayable
record of what you changed.
