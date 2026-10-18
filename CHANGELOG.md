# Changelog

## 0.3.0

- 3 new MCP tools (72 -> 75): `jmag_doctor`, `jmag_guide`, `jmag_star_repo`.
- Install diagnostics: `python -m jmag_bridge --doctor [--json]` (exit 1 if any check FAILs) and the read-only
  tool `jmag_doctor` run the same stdlib-only checks without touching JMAG: host Python, `mcp` package, JMAG
  install (`JMAG_HOME`), `designer.exe`, JMAG's Python (`JMAG_PYTHON`) and whether it runs, PyJmag import under it,
  state dir writable, matplotlib, claude CLI, `claude mcp list` registration (CLI only), installed skills up to
  date, running Designer PIDs. Every non-PASS row carries a `fix`. `--version` prints the package version.
- Start-up failures (import or `mcp.run`) are appended with their traceback to `<state dir>/server-startup.log`
  (fallback `%TEMP%`) with a one-line pointer on stderr, since Claude Code hides a failing server's output.
  The `jmag-mcp` console script (used by the plugin's `uvx` launch) now goes through `jmag_bridge.__main__`, so
  import-time failures are logged there too.
- `scripts/install.ps1` hardened: finds Python >= 3.10 (`py -3.14` .. `-3.10`, then `python`/`python3`), rejects
  the Microsoft Store stub and Store Python, `-Python <exe>` to choose; rejects a `.venv` that no longer runs or is
  older than 3.10; checks for the `claude` CLI unless `-NoMcp`; validates `-JmagHome` (needs `designer.exe`, warns
  without `python3.8`) and passes it to the MCP registration and to the final doctor run; installs `.[plots]`;
  checks the exit code of venv, pip and `claude mcp add`; `-Uninstall` removes the registration and the skills;
  ends with a doctor run and exits 1 if it reports FAIL.
- The skills ship inside the wheel (`jmag_bridge/skills`) and the sdist; a checkout still reads `.claude/skills`
  directly. `jmag_guide(topic?, reference?)` and the resources `jmag://guide`, `jmag://guide/{topic}`,
  `jmag://guide/{topic}/{reference}` serve them to clients that do not load Claude skills; the server instructions
  tell such clients to call `jmag_guide` first.
- One-time, opt-in offer to star the repository: `jmag_capabilities` and `jmag_self_test` carry a
  `first_run_note` until the user answers; `jmag_star_repo(action)` (`star` via `gh api`, falling back to opening
  the page in the browser; `open`; `dismiss`). The answer is recorded in `state.json` (`star_prompt`).
- `jmag_bridge.__version__` matches pyproject again (it still said 0.1.0 in 0.2.0).
- Docs: `docs/quickstart.md` (first ten minutes), `docs/install-troubleshooting.md` (one section per symptom),
  `docs/other-clients.md` (Cursor, Codex, Claude Desktop, VS Code, Windsurf), Chinese README `README.zh-CN.md`.
  `tests/test_tool_names.py` now also checks `docs/*.md` and `README.zh-CN.md`.

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
