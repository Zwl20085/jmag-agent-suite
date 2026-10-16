# Contributing

## Dev install and tests

```powershell
python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"   # Python >= 3.10
.venv\Scripts\python -m pytest                                 # offline, no JMAG needed
```

Live tests (`-m live`, scripts in `tests/live/`) need a licensed local JMAG-Designer; run
`jmag_self_test` after touching `worker/ops.py`, `server.py` run/cancel logic or `selftest.py`.

## The Python 3.8 worker rule

`src/jmag_bridge/worker/` runs under JMAG's bundled Python 3.8. No `match`, no `X | Y` or
`list[str]` at runtime, no 3.9+ APIs. CI runs `vermin --target=3.8-` and the worker protocol tests
on Python 3.8. The server (`server.py` and everything else) targets Python >= 3.10.

## Adding a tool

1. Op in `worker/ops.py` (`@op(...)`, use `need()` / `set_props()`).
2. Expose it in `server.py` with an LLM-oriented docstring; add to `READ_ONLY_OPS` if it is read-only.
3. Rules and invariants go in `health.py` with tests in `tests/test_health.py`; numeric limits live in
   `health.THRESHOLDS` (the README quotes them, a test checks the match).
4. Document it in `.claude/skills/jmag-simulation/references/`.

## Verified vs unverified documentation

Only write down JMAG behaviour you observed on a real installation. Anything inferred from the
manual or by analogy must be marked **unverified**.

## Repo hygiene

The repo is public: no `.jproj`/`.jfiles`, solver output, JMAG manuals or files from the JMAG
install, and no personal paths in examples or tests.

## PR checklist

- [ ] `pytest` passes; new logic has tests
- [ ] Worker changes are 3.8 compatible (`vermin --target=3.8- src/jmag_bridge/worker`)
- [ ] Tool docstring, skill references and README tool list updated
- [ ] Unverified behaviour is labelled as such
- [ ] CHANGELOG.md entry added
- [ ] No JMAG-derived or personal files committed
