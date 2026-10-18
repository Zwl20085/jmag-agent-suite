# Changelog

## Unreleased

- One-time, opt-in offer to star the repository: `jmag_capabilities` and `jmag_self_test` carry a
  `first_run_note` until the user answers; new tool `jmag_star_repo(action)` (`star` via `gh api`, falling back
  to opening the page in the browser; `open`; `dismiss`). The answer is recorded in `state.json` (`star_prompt`).
- `jmag_bridge.__version__` now matches pyproject (0.2.0; it still said 0.1.0).

## 0.2.0

- Renamed to `jmag-agent-suite` (console script `jmag-mcp` and package `jmag_bridge` unchanged).
- Claude Code plugin and marketplace packaging: `/plugin marketplace add Zwl20085/jmag-agent-suite`,
  then `/plugin install jmag@jmag-agent-suite`.
- Health checks: `ZERO_CURRENT` is a warning in pre- and postflight (error only if flux linkage is also
  zero); postflight honours the known-message classifier levels; limits exposed as `health.THRESHOLDS`.
- CI runs the worker protocol tests on Python 3.8.
- 17 new MCP tools (55 -> 72):
  - analytic layer, no JMAG needed: `jmag_estimate` (15 hand-calc kinds), `jmag_units`, `jmag_winding_plan`
  - project and results: `jmag_inventory`, `jmag_compare`, `jmag_plot` (matplotlib, `plots` extra),
    `jmag_make_report`, `jmag_export_table`, `jmag_case_report`; `case=` on `jmag_list_results` / `jmag_get_result`
  - JMAG API: `jmag_capabilities`, `jmag_case_table`, `jmag_run_cases`, `jmag_loss_study`,
    `jmag_efficiency_map` (unverified), `jmag_winding`, `jmag_optimization_table` (set unverified)
  - `jmag_experiment`: cogging, back-EMF, load point, static inductance / Ld-Lq as one call
  - `jmag_report` renamed `jmag_solver_report`
- Skills: 10-skill family (`jmag` router, simulation, geometry, machine-design, experiments,
  losses-efficiency, parametric-optimisation, postprocess, scripting, troubleshooting).
- Live-verified on JMAG-Designer 23.0: case runs, iron-loss study, back-EMF, cogging, Ld/Lq, load point.
  Calls that crashed Designer (JMAG winding definition, field value exports) are refused unless `force=true`.
- `tests/test_tool_names.py` fails the build if README or a skill names a tool that does not exist.
- New hero banner (`docs/hero/make_hero.py` generates `docs/images/hero.svg`); JMAG facts moved to `docs/jmag-facts.md`.

## 0.1.0

- Initial MCP server and Claude skills for JMAG-Designer with engineering feedback (setup lint,
  solver monitoring, physics invariants).
- Custom B-H material creation (`jmag_create_material`), connect/model image fixes.
- DC-dominant result tables in `jmag_get_result`; cleaner contour images.
