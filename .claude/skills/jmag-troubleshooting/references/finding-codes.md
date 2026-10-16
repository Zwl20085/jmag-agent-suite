# Finding codes (from `health.py`, the bridge's rule engine)

Contents: 1 Verdicts - 2 Call-level error types - 3 Pre-run codes - 4 Post-run codes -
5 Known solver messages

Source of truth: `src/jmag_bridge/health.py` (pre/post rules) and the worker's error envelope.
The `jmag-simulation` skill's `references/feedback.md` explains the same codes with the
physics-invariant thresholds.

## 1. Verdicts

`jmag_check` and the post-run check return `{"verdict", "counts", "findings": [{"level",
"code", "message", "fix"}]}`. FAIL = at least one error; CHECK = warnings, no errors; PASS =
only infos (or nothing). `jmag_run` refuses to start on a FAIL pre-check unless `force=true`.

## 2. Call-level error types (`error.type`)

| Type | Meaning | Action |
|---|---|---|
| `JmagError` | JMAG logged an error while the op ran (message-log diff); partial result attached | read `jmag_messages`; treat the result as invalid |
| `OpError` | the bridge refused or detected a failure (unknown name, missing part, read-back mismatch, no results) | follow `hint` (closest names listed) |
| `BadArguments` | wrong/missing tool arguments | the hint repeats the op's first doc line |
| `JmagCrashed` | the Designer process died during the call | `jmag_connect(launch_new=true)`, reopen the saved project, `jmag_cleanup` |
| timeout (message "did not answer within ...") | worker got no answer; dialogs attached | `jmag_dialogs` / `jmag_screenshot`, close the dialog, `jmag_connect` (the worker was restarted) |
| `mismatch: true` in `applied` | value read back differs from what was set | unit/range problem; `jmag_get_properties(with_help=true)` |

## 3. Pre-run codes (`jmag_check(stage='pre')`)

| Code | Level | Trigger | Fix |
|---|---|---|---|
| NO_MATERIAL | error | a part has no material | `jmag_set_material`; air pockets `Air` or suppress |
| DEFAULT_STACK_LENGTH | warning | 2D `ModelThickness` == 1 | set the real stack length |
| NO_EXCITATION | error | no Current/FEMCoil/FEMConductor/CurrentDensity/MagneticFlux condition and no magnet | add excitation |
| COND_NO_PARTS | error | Current condition, coil/conductor side or RotationMotion without parts | add parts |
| DEFAULT_TURNS | warning | Current condition or circuit coil with Turn = 1 | set turns |
| ZERO_CURRENT | warning | Current condition with Current = 0 | intended (e.g. coupling test)? else set it |
| UNLINKED | error | FEM coil/conductor without circuit link | `jmag_add_fem_coil(component=...)` |
| BAD_LINK | error | link to a component that does not exist | use a listed component name |
| SAME_DIRECTION | warning | both sides of a 2D FEM coil have the same Direction2D | go/return should be up/down |
| COIL_EDDY | warning | coil parts with eddy currents on | stranded coils: `EddyCurrentCalculation: 0` |
| NO_SOURCE | error | circuit without voltage/current source | add a source |
| NO_GROUND | warning | circuit without Ground | add Ground |
| ONE_STEP | error | transient with <= 1 step | `jmag_set_step` |
| COARSE_TIME_STEP | warning | < 20 steps per electrical period (StepType 0, needs `electrical_frequency_hz`) | >= 30-60 steps/period |
| SHORT_SPAN | warning | < 1 electrical period simulated | simulate >= 1 period (2+ with circuits) |
| ZERO_SPEED | warning | RotationMotion AngularVelocity = 0 without load type | set speed |
| NO_TORQUE_COND | info | rotation without a Torque condition | add Torque if you need torque |
| COARSE_MESH | warning | mesh exists with < 500 elements | local sizes, refine, compare |
| NO_MESH_YET | info | no mesh yet (made at run time) | fine; `jmag_mesh` to see it first |
| CHECK_MAGNETIZATION | info | magnets present | verify with a B contour after the run |

## 4. Post-run codes (`jmag_check(stage='post')`, included in finished `jmag_run_status`)

| Code | Level | Meaning |
|---|---|---|
| NO_RESULT | error | the study has no results (failed or cancelled) |
| STEPS_CHANGED | warning | result rows != configured steps (JMAG retimed motion, or the run stopped early) - use the returned time axis |
| ITERATION_LIMIT | warning | steps where Newton hit `NonlinearMaxIteration` (listed) |
| ZERO_CURRENT | error | every column of a circuit-current table is identically zero |
| NON_FINITE | error | NaN or Inf in a result column |
| NOT_STEADY | info | torque / flux / current: last two electrical periods differ > 2 % |
| SOLVER_ERROR / SOLVER_WARNING | error / warning | a solver message not in the known list - read it |
| known solver messages | see 5 | classified by pattern |

## 5. Known solver messages (pattern -> code)

| Pattern in the message | Code | Level | Meaning |
|---|---|---|---|
| "step control has been automatically updated" | STEP_AUTO_UPDATED | info | JMAG retimed steps for the slide-mesh motion; normal |
| "calculation area of the force condition or the torque condition was extended" | TORQUE_AREA_EXTENDED | info | torque integration widened into air; normal |
| "leakage inductance of fem-coil is ignored" | LEAKAGE_L_IGNORED | info | zero/small leakage L skipped; normal unless intended |
| "not converged" | NONLINEAR_NOT_CONVERGED | warning | Newton limit at some steps; harmless at current zero-crossings |
| "negative or zero" | NEGATIVE_ELEMENT_VOLUME | error | distorted elements: moving parts on a normal mesh / part bridging rotor and stator |

When a message arrives in the report's error list, the finding is raised at error level even if
its pattern is normally a warning.
