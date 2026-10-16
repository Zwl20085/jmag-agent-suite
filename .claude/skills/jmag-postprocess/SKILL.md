---
name: jmag-postprocess
description: Reading and reporting JMAG-Designer results through the jmag MCP tools - result tables and their internal keys (LineCurrent, TerminalVoltage, FEMCoilFlux, Torque ...), choosing the steady-state window, FFT / ripple / harmonics, reading contour plots (and their corner singularities), plots with jmag_plot, CSV export, study comparison with jmag_compare, and the final report with jmag_make_report (every number with its source, FAIL/CHECK never omitted). Use whenever the user asks what a result means, wants waveforms, spectra, ripple, plots, a comparison of two runs, exported data, or a write-up / report of a JMAG simulation.
---

# Post-processing and reporting

A number without its source and its check is an anecdote. Every value you give the user names
the study, the table, the column, the time window and the check that supports it.

## Result tables

`jmag_list_results(study)` lists what exists; `jmag_get_result(study, table, ...)` reads one
(both take `case=k` for design-table cases).
`table` accepts the display name or JMAG's internal key; raw API calls
(`GetDataFromName`) accept **only** internal keys - display names return empty tables
(verified). Map: `references/result-keys.md`.

| Display name | Internal key | Typical use |
|---|---|---|
| Torque | Torque | mean, ripple |
| Circuit Current | LineCurrent | phase currents (3-phase source column = phase sum) |
| Circuit Voltage | TerminalVoltage | probe voltages |
| Coil Flux-Linkage | FEMCoilFlux | psi per FEM coil, EMF, Ld/Lq |
| FEM Coil Inductance | FEMCoilInductance | inductance output |
| Stored Energy | StoredEnergy | energy check (needs `Energy: 1`) |

## The steady-state window

- Use the **returned time axis**, never `n x dt` (JMAG may retime motion studies).
- Pass `electrical_frequency_hz` to `jmag_get_result`: `stats.<col>.last_period` holds mean,
  fundamental, THD (AC quantities only) and harmonic amplitudes over the last electrical period;
  `periodicity.steady` says whether the last two periods agree (NOT_STEADY > 2 %).
- First period of a circuit-driven run = start-up transient; never average across it.
- Static studies have a step axis, not time.

## Harmonics and ripple

- `last_period.harmonic_amplitudes[k]` is the peak amplitude of order k of the electrical
  frequency, k = 0 (mean) to 9 only; for higher orders (12th torque ripple, slot harmonics)
  export the table and use `jmag_plot` or your own FFT over whole periods.
- Torque ripple: `ripple_pct_of_mean` and pk-pk; 3-phase machines ripple at 6k x f_e; cogging
  at `LCM(Q, 2p)/p` x f_e. Orders that are not multiples of 6 point at unbalance or wrong phase
  sequence.
- EMF: THD and the 5th/7th/11th/13th; triplen harmonics vanish line-line in a star connection.
- FFT needs an integer number of periods and uniform sampling; resample if JMAG changed the step.

## Contours

`jmag_contour_image(study, result_type='MagneticFluxDensity', component='Absolute', step=...)`:
- Look for alternating poles, flux closing through the yokes, saturation where designed (IPM
  bridges), none where not (teeth roots at rated load).
- Max |B| markers at sharp re-entrant corners are **field singularities** that grow with mesh
  refinement; judge saturation by the area-averaged B in the tooth/yoke body.
- Pick the step deliberately (peak current, max torque); state it.

## Plots, exports, comparisons

- `jmag_plot(kind, ...)`: `waveform` (mean/rms annotated) | `fft` (one period; pass
  `electrical_frequency_hz`) | `sweep` | `map` (2D grid, e.g. efficiency) | `torque_speed`
  (envelope + power, base speed annotated). Source: `data` (what `jmag_get_result` returns),
  `csv_path` (exported CSVs) or `study` + `table` (full resolution). Returns the PNG plus the
  plotted numbers in text, so you can check them without vision. Needs matplotlib (the
  `plots` extra); if missing, the error says so.
- `jmag_export_results(study, out_dir)`: one CSV per table written by the bridge (x column + data
  columns, units in headers) - the default choice.
- `jmag_export_table(study, path, kind=...)`: JMAG's own writers - `all_tables`, `all_cases`
  (every design-table case in one file), `table`, and field tables (`field`, `field_values`,
  `field_minmax`). **Save the project first**: a field-export probe sequence
  (ExportTable -> ExportResultValuesTable -> WriteCaseReport) crashed Designer once on 23.0.
- `jmag_case_report(study, path, case?)`: JMAG's own HTML case report (same save-first rule).
- `jmag_compare(study_a, study_b)`: metric deltas plus setup differences that could explain them;
  use it for mesh studies, variants and before/after fixes.
- `jmag_contour_image` / `jmag_circuit_image` / `jmag_model_image` produce PNGs for the report.
- `jmag_journal(export_script=...)` writes a replayable script of every state change - attach
  it for traceability.

## The report

`jmag_make_report(title, studies, out_dir, ...)` writes `report.md` + `images/` + `replay.py`:
per study the setup table, pre/post verdicts with every finding, convergence, statistics naming
their table/column, contour and circuit images. Run the physics checks first and pass them in
(`invariants={study: [jmag_physics_check results]}`); add `sweeps` (tables), `notes` and
`doubts` (merged with automatic ones). Before handing it over, walk the checklist in
`references/deliverable-checklist.md`. Non-negotiable:
1. Every number has its source (study / table / column / window) and unit.
2. FAIL and CHECK findings are listed with codes and what was done about them - never dropped.
3. At least one physics invariant and one hand estimate per headline result.
4. Assumptions (2D, no end effects, temperature, stack length, mesh) and open doubts.

Write reports outside the repository (user-chosen folder or `%LOCALAPPDATA%`).
