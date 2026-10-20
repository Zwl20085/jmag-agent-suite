# Result tables, internal keys and the numbers the tools return

Contents: 1 Display names and internal keys - 2 What jmag_get_result returns -
3 Derived quantities - 4 Raw API access

## 1. Display names and internal keys (verified on 23.0 unless marked)

| Display name | Internal key (`GetDataFromName`) | Columns | Notes |
|---|---|---|---|
| Torque | 2D transient: - (read by display name); 2D Static and 3D Static: `Torque` (verified 2026-10-20) | per Torque condition | sign follows the condition axis and rotation; Static: nodal `Torque` condition, one row, N m (`Torque_Surface` aborts the run on parts); `RotationPeriodicBoundary` sector: already full-model, factor 1 (verified 2026-10-20); a stator condition has the opposite sign |
| Circuit Current | `LineCurrent` | per circuit component | 3-phase source column = phase sum (0 when balanced) |
| Circuit Voltage | `TerminalVoltage` | per probe/component | probe = motor-convention terminal voltage |
| Coil Flux-Linkage | `FEMCoilFlux` | per FEM coil (circuit coil name) | whole phase, stack length included |
| FEM Coil Inductance | `FEMCoilInductance` | per FEM coil | |
| Stored Energy | `StoredEnergy` | `Total` (3D Static: one column per part + `Total`) | only with study property `Energy: 1` |
| Magnetic Flux of Current Condition | `CoilFlux` (3D Static, verified 2026-10-19; 2D Static, verified 2026-10-20) | per `Current` condition | static studies; psi per condition, turns included, model value (half model with `SymmetryBoundary`: x2; `RotationPeriodicBoundary` sector: already full-model, factor 1, verified 2026-10-20); a 0 A condition still reports it. That `CoilFlux` is this display table is inferred |
| Current of Current Condition | `CoilCurrent` (3D Static, verified 2026-10-19) | per `Current` condition | static studies |
| Joule Loss | - (unverified key) | per conducting part | eddy/FEM-conductor loss |
| Iron Loss, Hysteresis Loss | - (unverified key) | per part | from loss studies / loss conditions |
| Circuit Electric Power | - (unverified key) | per component | |
| Total Rotational Displacement | - (unverified key) | | rotor angle vs time |

Display names come from `jmag_list_results`; internal keys only matter for raw API code.
The bridge's `jmag_get_result` tries display names first, then internal keys.

## 2. What `jmag_get_result` returns

- `x` (time [s] or step), `x_name`, `x_unit`, `unit`, `rows`, downsampled `series`
  (`max_points`, use `jmag_export_results` for full resolution).
- `stats.<column>`: `n, mean, rms, min, max, pk_pk`, and `ripple_pct_of_mean` only for
  DC-like signals (|mean| > 0.1 rms).
- With `electrical_frequency_hz`: `last_period` = `{mean, fundamental, thd_pct (AC only),
  harmonic_amplitudes[0..9]}` (peak values, order k = k x f_e, last full period, endpoint
  excluded) and `periodicity` (difference between the last two periods; `steady` flag).

## 3. Derived quantities (compute and cite the inputs)

| Quantity | From | Formula |
|---|---|---|
| Back-EMF | `FEMCoilFlux` | `e = -dpsi/dt` (backward difference, as JMAG integrates) |
| Terminal voltage check | `TerminalVoltage`, `LineCurrent`, `FEMCoilFlux` | `v = R i + dpsi/dt` (`jmag_physics_check('kvl')`) |
| Static inductance | psi per condition / FEM coil | `L = psi / i` (sum go+return conditions for one coil) |
| dq flux linkages | `FEMCoilFlux` x3 + rotor angle | Park transform (`jmag-machine-design` formulas 6) |
| Electromagnetic power | `LineCurrent`, `FEMCoilFlux` | `<sum i dpsi/dt>` (`jmag_physics_check('power_balance')`) |
| Mechanical power | `Torque` | `<T> omega_m` |
| Line-line voltage | two phase columns | `v_ab = v_a - v_b` |
| rms over a period | last-period samples | `sqrt(mean(y^2))` |

## 4. Raw API access (inside `jmag_exec_python`, verified pattern from the bridge)

```python
s = app.GetModel(0).GetStudy(0)                 # resolve by index; see jmag-scripting
rt = s.GetResultTable()
d = rt.GetDataFromName("FEMCoilFlux")           # internal key only
if d is None or not d.IsValid() or d.GetRows() == 0:
    raise OpError("table empty - wrong key or no results")
cols = [d.GetColName(j) for j in range(d.GetCols())]
t = [d.GetTime(k) for k in range(d.GetRows())]
psi_u = [d.GetValue(k, cols[0]) for k in range(d.GetRows())]
result = {"cols": cols, "t_end": t[-1], "psi_u_max": max(psi_u)}
```
