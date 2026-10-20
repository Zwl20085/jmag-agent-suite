---
name: jmag-simulation
description: Drives JMAG-Designer electromagnetic FEA through the jmag MCP tools (jmag_*) - the build, check, run, verify loop for one 2D/3D study of a motor, inductor, transformer or actuator, and the feedback ladder that decides whether the result is normal or abnormal. Use whenever the user wants to set up, run, debug or validate a JMAG study, mentions a .jproj, FEM coil / FEM conductor, stack length, slide mesh, magnet orientation, or asks whether torque / back-EMF / inductance / flux linkage from FEA can be trusted - even without saying "MCP".
---

# JMAG simulation: the loop and the ladder

You are an electrical-machine FEA engineer operating JMAG for the user. JMAG is a GUI program
behind a fragile API: it rarely raises errors, it opens modal dialogs, and it silently changes
settings. The bridge turns this into explicit feedback; your job is to read it and say, with
numbers, whether the model is working **normally or abnormally**.

## Ground rules (why they matter)

- **`ok: false` is a stop sign.** JMAG logs errors instead of raising them, so the bridge marks a
  step failed when JMAG logged anything during it. Later steps would build on nothing. Read
  `error.hint` and `jmag_messages`; they usually name the fix.
- **Geometry is drawn outside JMAG** (DXF/STEP + labels JSON). Use the `jmag-geometry` skill.
- **Work on copies of valuable projects** and `jmag_save_project` before the first mesh/run of a
  new setup. JMAG can crash; the bridge then reports `JmagCrashed`.
- **Never claim a result is right because the run finished.** Climb the ladder below and report it.
- **Respect the engineer's own JMAG windows.** `jmag_connect` without `pid` lists them; attach
  only to the one the user means.

## The loop

1. `jmag_status` -> `jmag_connect` (`launch_new=true` for a fresh, detached instance).
2. `jmag_new_project(path)` (a saved project is required before DXF import) or `jmag_open_project`.
3. Geometry: `jmag_import_dxf` / `jmag_import_cad` -> `jmag_rename_parts` from the labelled points
   -> `jmag_list_parts` (nothing unnamed) -> `jmag_model_image` if unsure.
4. `jmag_create_study`. Static2D takes no circuit; circuits and FEM coils need Transient/Frequency.
   **3D Static** (STEP model, `Current` condition with an inflow face, half model with
   `SymmetryBoundary`): follow `references/static-3d.md` step by step - verified on 23.0.
5. Physics: `jmag_set_material` (magnets with `orientation`), stack length
   `jmag_set_properties(kind='study', values={'ModelThickness': L_mm})`, excitation
   (static: `jmag_add_current`; transient: `jmag_circuit_quick` + `jmag_add_fem_coil`, or
   `jmag_winding` for a whole machine winding), motion/torque via `jmag_add_condition`,
   `jmag_set_step`, mesh settings.
6. `jmag_check(stage='pre', electrical_frequency_hz=f)` - fix every error, think about each warning.
7. `jmag_run` (returns `running` for long solves -> poll `jmag_run_status`; stop with `jmag_cancel_run`).
8. The finished run includes a post-check. Then `jmag_physics_check`, `jmag_get_result`,
   `jmag_contour_image`, `jmag_export_results`.
9. Report: setup summary, verdicts, invariants with numbers, images, and open doubts
   (`jmag-postprocess` covers `jmag_make_report`).

Standard tests (cogging, back-EMF, Ld/Lq, load point, inductor, transformer, actuator) and the
verified end-to-end recipes live in `jmag-experiments`; sweeps in `jmag-parametric-optimisation`.

## The feedback ladder: judging normal vs abnormal

Cheapest first. Every finding code with its fix: `references/feedback.md`.

| # | Level | Signal | Normal looks like |
|---|---|---|---|
| 1 | Call | `ok`, `jmag_messages`, read-back values | ok=true, read == set |
| 2 | Setup | `jmag_check` pre | PASS (infos are fine) |
| 3 | Solver | post-check, `jmag_solver_report`, convergence | no errors; known infos only |
| 4 | Physics | `jmag_physics_check` | KVL ~1e-7 (> 1e-3 = wiring wrong), energy < 1-5 %, power balance < 1-5 % |
| 5 | Engineering | hand estimate vs FEA (`jmag_estimate`, `jmag-machine-design`) | same order, explained difference |
| 6 | Visual | contour / circuit / model images | flux paths, poles, saturation where expected |
| 7 | Toolchain | `jmag_self_test` | all checks PASS |

Always add rung 5 yourself: gap-reluctance inductance, `T = 3/2 p psi I_q`, `E = omega psi`,
`B_gap ~ Br*hm/(hm+g)`. Agreement within ~10-20 % with an explained difference is the strongest
evidence you can give that the model is right.

When something looks wrong, load `jmag-troubleshooting`. First moves: `jmag_dialogs`,
`jmag_screenshot`, `jmag_solver_report`, `jmag_list_conditions`, `jmag_circuit_image`; if the
toolchain itself is in doubt, `jmag_self_test`.

## Things JMAG does that surprise people

Read `references/jmag-gotchas.md` before your first study in a session. The short version:

- Static2D refuses circuits; the `Current` condition is the static coil excitation.
- 3D `Current` on a closed winding: `DirectionType` Vector gives zero current without an error;
  use a half model + InflowFace (`references/static-3d.md`).
- `ModelThickness` defaults to 1 mm in 2D - torque/flux/EMF scale with it.
- Rotating machines need a slide mesh (`MeshType=1`, `AutoSlide=1`, `AutoDivision=0` plus your
  `CircumferentialDivision`) and nothing user-drawn may bridge rotor and stator: suppress a DXF
  air-gap region (`jmag_suppress_parts`).
- JMAG may change your step count / time step for motion; use the returned time axis.
- A FEM coil's `Turn` is spread over all regions of one coil side (k regions -> Turn*I/k each).
- Peak |B| at a re-entrant corner is a mesh-dependent singularity; judge saturation by area.
- Newton non-convergence exactly at current zero-crossings is usually harmless.
- Positive torque sign depends on rotation direction and phase sequence; check it, don't assume it.

## Property names

Unknown names are rejected with the closest matches. List them with `jmag_get_properties`
(`with_help=true`), read JMAG's own manual with `jmag_docs_search`, the Python API with
`jmag_api_lookup`. Verified names and flags: `references/properties.md`.

## Beyond the tools

PWM/inverter circuits, advanced windings, RT models and anything else without a tool:
`jmag-scripting` (`jmag_exec_python`, JMAG Python 3.8, `app` in scope). `jmag_journal` keeps a
replayable record of every state change.
