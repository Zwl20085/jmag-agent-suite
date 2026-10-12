# Feedback: finding codes, what they mean, what to do

Every tool returns `{"ok", "result", "error": {"message", "hint"}, "jmag_messages"}`.
`jmag_check` and the post-run check return `{"verdict": PASS|CHECK|FAIL, "findings": [...]}`.
PASS = no warnings/errors (infos allowed), CHECK = warnings to think about, FAIL = errors.

## Call level

| Signal | Meaning | Action |
|---|---|---|
| `error.type = JmagError` | JMAG logged an error while the call ran | read `jmag_messages`; the partial result may be wrong |
| `error.type = OpError` | the bridge refused or verified a failure (unknown name, missing part, readback mismatch) | follow `hint` (closest names are listed) |
| `JmagCrashed` | Designer died during the call | `jmag_connect(launch_new=true)`, reopen last saved project, `jmag_cleanup` |
| `TimeoutError` + dialogs | JMAG is blocked by a modal dialog | `jmag_dialogs`, `jmag_screenshot`, click a safe button (Cancel/No) |
| `mismatch: true` in `applied` | value read back differs from what was set | unit or range problem; check `jmag_get_properties(with_help=true)` |

## Pre-run (`jmag_check stage='pre'`)

| Code | Level | Fix |
|---|---|---|
| NO_MATERIAL | error | assign materials; air pockets get `Air` or are suppressed |
| DEFAULT_STACK_LENGTH | warning | set `ModelThickness` (2D results scale with it) |
| NO_EXCITATION | error | add Current / FEM coil / magnets |
| COND_NO_PARTS | error | condition or coil side without parts |
| UNLINKED / BAD_LINK | error | FEM coil must link to an existing circuit component |
| SAME_DIRECTION | warning | go/return sides of a coil normally up/down |
| COIL_EDDY | warning | stranded coils: `EddyCurrentCalculation: 0` |
| NO_SOURCE / NO_GROUND | error / warning | circuit incomplete |
| DEFAULT_TURNS / ZERO_CURRENT | warning | default Turn=1 or zero current |
| ONE_STEP / COARSE_TIME_STEP / SHORT_SPAN | error / warning | step control vs electrical frequency |
| ZERO_SPEED | warning | RotationMotion `AngularVelocity` is 0 |
| NO_TORQUE_COND | info | no torque table will exist |
| COARSE_MESH / NO_MESH_YET | warning / info | mesh |
| CHECK_MAGNETIZATION | info | look at a B contour after the run |

## Post-run

| Code | Level | Meaning |
|---|---|---|
| NO_RESULT | error | solve failed or was cancelled |
| NEGATIVE_ELEMENT_VOLUME | error | moving parts on a normal mesh / part bridging rotor-stator -> slide mesh, suppress gap region |
| SOLVER_ERROR / SOLVER_WARNING | error / warning | unknown message - read it |
| NONLINEAR_NOT_CONVERGED, ITERATION_LIMIT | warning | see "convergence" below |
| STEP_AUTO_UPDATED | info | JMAG retimed steps for motion - normal |
| TORQUE_AREA_EXTENDED | info | normal |
| LEAKAGE_L_IGNORED | info | normal unless you set leakage L on purpose |
| STEPS_CHANGED | warning | rows != configured steps: use the returned time axis |
| NON_FINITE | error | NaN/Inf in results |
| ZERO_CURRENT | error | every circuit current is zero |
| NOT_STEADY | info | last two electrical periods differ > 2 % |

### Convergence
`convergence.steps_at_iteration_limit` lists steps where Newton hit `NonlinearMaxIteration`.
If they are spaced exactly half an electrical period apart (current zero crossings, B ~ 0) this
is a relative-tolerance artefact and harmless. If they cluster at peak load, raise the limit
(`NonlinearMaxIteration: 50`), refine the mesh where B changes quickly, shorten the time step, or
check the BH curve.

## Physics invariants (`jmag_physics_check`)

| check | identity | normal |
|---|---|---|
| `kvl` | v_probe = R i + dpsi/dt (backward Euler, as JMAG integrates) | rel. residual ~1e-7; > 1e-3 = wiring/polarity/link wrong |
| `energy` | W = 1/2 sum(i psi) | < 1 % linear iron; few % when saturated |
| `power_balance` | mean(sum i dpsi/dt) = mean(T) omega over one period | < 1 %; larger = eddy losses in conducting parts, wrong speed, wrong torque parts |

## Waveform statistics (`jmag_get_result` with `electrical_frequency_hz`)

`stats.last_period` gives the DC value, fundamental and harmonic amplitudes over the last
electrical period (sample count taken from the actual time axis). THD is only reported for AC
quantities; torque ripple is in `harmonic_amplitudes` (6k harmonics for 3-phase machines) and
`ripple_pct_of_mean`. `periodicity.steady` tells whether the last two periods agree.

## Visual checks

- B contour: alternating poles, flux closing through yokes, teeth 1.4-1.8 T and yoke 1.2-1.6 T
  for typical silicon steel designs; max markers at sharp inner corners are singularities.
- Circuit image: every terminal connected, ground present, probes on the intended node.
- Model image (`show_mesh`): elements across the air gap (>= 2-4 layers), no slivers.

## Toolchain

`jmag_self_test` (C-core inductor, static + transient): six checks must PASS. Run it after
installing, after JMAG updates, or when results look suspicious before you blame the model.
