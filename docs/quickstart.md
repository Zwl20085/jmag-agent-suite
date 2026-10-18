# First ten minutes

For an engineer who has just run `.\scripts\install.ps1` (see the README) and uses Claude Code. Other MCP clients:
[other-clients.md](other-clients.md). If something fails, go to step 6.

## 1. Restart Claude Code

MCP servers and skills are read at start-up. After restarting, `claude mcp list` should show `jmag`. If it does not,
run `.\.venv\Scripts\python.exe -m jmag_bridge --doctor` (the installer already ran it once; every non-PASS row has
a `fix:` line) and see [install-troubleshooting.md](install-troubleshooting.md).

## 2. Run the self-test

Ask: *run the JMAG self-test*. The agent calls `jmag_self_test`. It launches JMAG, builds a gapped C-core inductor
(100 turns, 5 A, 50 mm stack) and solves a static and a transient study. There are six checks and all must pass:

| Check | PASS means |
|---|---|
| core area (geometry import) | imported core area matches the script, within 0.5 mm2 |
| static L / analytic L0 | inductance is 1.05 to 1.35 times the no-fringing value (9.42 mH; about 11.0 mH on 23.0) |
| static energy identity | stored energy equals 1/2 sum(i psi) within 3 % |
| transient psi/i at peak vs static L | same physics through the transient solver, within 2 % |
| transient KVL residual | below 1e-3 relative: circuit and FEM coil are wired correctly |
| peak \|B\| at peak current | between 0.3 and 2.2 T |

Six PASS means the bridge, JMAG and the licence work; a failure points at the toolchain, not at your model. On first
use you may see a one-time, opt-in question about starring the repository. Answer or decline as you like.

## 3. Run an example

Ask for the C-core (the self-test model) to be built and solved, with the inductance checked against a hand estimate.
Or run the SPM example from a terminal in the repository:

```powershell
.\.venv\Scripts\python.exe examples\spm_motor\run_mcp.py <work_dir> [--pid <running JMAG pid>] [--keep-open]
```

`<work_dir>` is required. `--pid` attaches to a JMAG you already started (otherwise a new one is launched).
`--keep-open` leaves JMAG open. The script draws a 12-slot / 4-pole SPM motor outside JMAG, imports it, names the
parts, sets materials and magnet orientation, builds a three-phase circuit with FEM coils, runs at 1500 rpm, then
checks power balance and the analytic torque. The README reports torque 1.774 Nm against 1.777 Nm from flux linkage
(0.2 %) on JMAG-Designer 23.0. Other versions and meshes may give slightly different numbers.

## 4. Prompts to try

> Here is my motor geometry script. Build a Transient2D load point at 1500 rpm, 10 A peak, 50 mm stack, find the
> max-torque current angle and prove the torque is right.

> Run cogging torque and open-circuit back-EMF on this 12-slot / 10-pole design, compare the EMF constant with a hand
> estimate, and plot the FFT.

> Sweep the magnet thickness over 4 cases, solve them all, and write a report with torque, loss and the invariants
> for each.

## 5. Read a tool answer

Every tool answers with:

```json
{"ok": true, "result": {}, "error": {"message": "", "hint": ""}, "jmag_messages": [], "seconds": 0.4}
```

`ok: false` is a stop sign. Read `error.hint` (written as the next action) and `jmag_messages` (what JMAG logged),
fix, then continue.

A number is trusted only after the feedback ladder, cheapest first: the post-run verdict, a physics invariant (KVL,
energy, power balance), and a hand estimate (`jmag_estimate`) with a tolerance. The agent should report where it
stopped climbing. The full seven-level ladder is in the README.

## 6. When something fails

- Install, start-up, path and Python problems: [install-troubleshooting.md](install-troubleshooting.md). If the
  server answers but JMAG is not found or the worker fails, ask the agent to run `jmag_doctor`.
- JMAG-side problems (dialogs, crashes, non-convergence, wrong torque, empty tables): ask the agent to use the
  `jmag-troubleshooting` skill. Its first moves are `jmag_status`, `jmag_dialogs` and `jmag_screenshot`.
- Known JMAG behaviour: [jmag-facts.md](jmag-facts.md).

## 7. Work on your own project safely

- Work on copies: `jmag_save_project(path=<copy>)` before the first mesh or run.
- `jmag_connect` without `pid` lists running JMAG instances instead of attaching. Name the instance you want.
- Geometry stays outside JMAG: supply a DXF or STEP plus a labels JSON (`jmag_winding_plan` gives the winding
  layout). `examples/spm_motor/make_geometry.py` is a complete template.
- Geometry import needs a saved project, and the 2D stack length defaults to 1 mm, so set it.
- Ask before deleting studies or overwriting results.
