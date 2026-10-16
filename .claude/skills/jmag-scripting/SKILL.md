---
name: jmag-scripting
description: Safe use of the JMAG-Designer Python API through jmag_exec_python when no jmag_* tool covers a feature - the Python 3.8 worker environment, `app` in scope, the need/IsValid/ClearError idioms, avoiding calls that log errors, looking up the API and manual with jmag_api_lookup / jmag_docs_search / jmag_docs_page, a library of small snippets tagged VERIFIED or UNVERIFIED, and how to promote a useful snippet to a proper MCP tool. Use when a task needs JMAG API calls beyond the existing tools (PWM circuits, winding definitions, probes, section graphs, design tables, loss studies, exports) or when the user asks how to script JMAG.
---

# Scripting JMAG through `jmag_exec_python`

`jmag_exec_python(code, timeout_s)` runs your code inside the bridge's worker - JMAG's bundled
**Python 3.8**, attached to the connected Designer. It is the escape hatch: powerful, journalled,
and with none of the guard rails the dedicated tools have. Prefer a tool whenever one exists
(see the tool map of the `jmag` skill).

## The environment

- Globals: `app` (JMAG `Application`), `designer` (the module), `ctx` (bridge context),
  `OpError` (raise it to fail with a message), `result` (assign to return JSON-able data).
  `print()` output is captured (last 20 kB).
- Python 3.8 syntax only: no `match`, no `X | Y` types, no 3.9+ APIs. The DLL is only safe on
  3.8 - never import the JMAG API in another interpreter.
- Return plain types (numbers, strings, lists, dicts). JMAG objects are not serialisable.
- JMAG errors are detected by diffing the message log around the call: if JMAG logged an error
  while your code ran, the result is `ok: false` even if your code finished.

## Idioms (all from the bridge's own worker, verified on 23.0)

1. **Check every object.** A failed lookup returns an object whose `IsValid()` is False and every
   later call on it silently does nothing. Wrap fetches in a `need()` helper
   (`references/snippets.md` S1).
2. **Never probe with calls that log errors**: `GetModel("missing")`, `GetStudy("missing")`,
   `GetCondition("missing")`, `GetLink()` on a non-circuit condition, `GetValue` on Integer /
   Vector / Table properties, `GetEquation(i)` past the end. Enumerate by index and compare
   names instead; gate `GetValue` on `GetPropertyType`.
3. **If a probe is unavoidable**, call `app.ClearError()` right after it so the bridge does not
   report it as your failure - and say in your answer that you did.
4. **Read back what you set.** After `SetValue`, read the value (or list the object) and compare;
   the bridge's own tools do this on every set (their `mismatch: true` flags are why).
5. **Small steps.** One logical change per call, with `result` describing what changed. Long
   scripts that fail half-way leave a half-built model.
6. **Save first** (`jmag_save_project`) before scripts that create or delete objects.

## Finding the right API

1. `jmag_api_lookup("Study.CreateLossStudy")` or keywords (`"export image"`) - methods and
   docstrings from the installed `designer.py`.
2. `jmag_docs_search("efficiency map")` -> `jmag_docs_page(page)` - the script reference and
   manual installed on this machine (property names, flags, valid strings).
3. `jmag_get_properties(..., with_help=true)` - property names/types on a live object.
4. Write the smallest snippet that reads (not writes) the object first; then write.

Do not guess string arguments (study type names, condition types, result types, file-type
flags): list them (`GetStudyTypeNames`, `GetConditionTypes`, `GetComponentNames`,
`GetPropertyNames`) or look them up.

## Snippet library

`references/snippets.md`: 12 small patterns. **VERIFIED** = the same calls run in the bridge's
`ops.py` against JMAG 23.0. **UNVERIFIED** = the members exist in the 23.0 script reference but
have not been exercised; run them on a copy, read back, and report what happened.

## Promoting a snippet to a tool

When a snippet is useful twice, make it a tool (repository `CLAUDE.md`, "Adding a tool"):
1. Implement the op in `worker/ops.py` (3.8 syntax, `need` / `set_props` / `resolve_part`,
   `@op(read_only=...)`).
2. Expose it in `server.py` with a docstring written for an LLM caller (units, examples,
   pitfalls); add it to `READ_ONLY_OPS` if it does not change state.
3. Rules or invariants go into `health.py` with a test in `tests/test_health.py`.
4. Document verified names/behaviour in `jmag-simulation/references/`; mark everything else
   unverified. Run `jmag_self_test` after touching `ops.py`.

Until then, `jmag_journal(export_script=...)` keeps a replayable record of what the snippet did.

## When a snippet fails

- `ok: false` with `jmag_messages`: JMAG logged an error - the message usually names the
  object or property. Fix and rerun on a fresh object (do not reuse invalid ones).
- Timeout: a modal dialog is open (`jmag_dialogs`, `jmag_screenshot`), or the call is a long
  foreground operation (`Run`, `RunAllCases`) - use the run tools instead.
- `JmagCrashed`: see `jmag-troubleshooting`.
