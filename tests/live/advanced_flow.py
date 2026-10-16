"""Live check of the advanced tools on the 12s/4p SPM example, through the public MCP tools only.

    python tests/live/advanced_flow.py <work_dir> [--pid <running JMAG pid>] [--skip-risky]

Builds the SPM model (examples/spm_motor/make_geometry.py) with a coarse 30-steps/period load study, then
exercises: jmag_capabilities, jmag_case_table (condition + equation parameters), jmag_run_cases (2 cases) and
per-case results, jmag_loss_study (create + run + results), jmag_export_table, jmag_winding (fem_coil and
jmag_winding modes on fresh studies), jmag_experiment (back_emf, cogging), jmag_optimization_table (get),
jmag_efficiency_map (create on a Transient study: expected to be refused), jmag_case_report and a field-table
export (the last two are 'risky': the project is saved before each). Writes <work_dir>/advanced_flow.json.
Needs a licensed JMAG-Designer 23.x. Nothing here assumes a particular machine.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "examples" / "spm_motor"))
import make_geometry  # noqa: E402

RPM, POLES, SLOTS = 1500.0, 4, 12
FE = RPM / 60 * POLES / 2
SPP = 30


async def main(work: Path, pid: int | None, skip_risky: bool) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    meta = make_geometry.build(work)
    log: list[dict] = []
    params = StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(tool: str, args: dict, label: str | None = None, must: bool = False) -> dict:
                t0 = time.time()
                res = await s.call_tool(tool, args)
                text = next(c.text for c in res.content if c.type == "text")
                try:
                    out = json.loads(text)
                except ValueError:
                    out = {"ok": False, "raw": text[:2000]}
                ok = out.get("ok", True) is not False
                print(f"{label or tool:40s} {'ok' if ok else 'FAILED'} {time.time() - t0:6.1f}s", flush=True)
                log.append({"step": label or tool, "tool": tool, "args": args, "ok": ok, "out": out})
                if must and not ok:
                    raise RuntimeError(f"{tool}: {json.dumps(out.get('error'))[:800]}")
                return out

            async def wait_run(st: dict) -> dict:
                while st.get("state") == "running":
                    await asyncio.sleep(5)
                    st = await call("jmag_run_status", {}, "  poll")
                    log.pop()  # keep the log short
                return st

            S = "Load"
            await call("jmag_connect", {"pid": pid} if pid else {"launch_new": True}, must=True)
            await call("jmag_new_project", {"path": str(work / "adv.jproj"), "overwrite": True}, must=True)
            await call("jmag_import_dxf", {"path": meta["dxf"], "model_name": "SPM"}, must=True)
            await call("jmag_rename_parts", {"renames": [{"part": p["point"], "name": p["name"]} for p in meta["parts"]]},
                       must=True)
            left = [p for p in (await call("jmag_list_parts", {}))["result"]["parts"] if p["name"].startswith("0/")]
            if left:
                await call("jmag_rename_parts", {"renames": [{"part": left[0]["id"], "name": "GapAir"}]})
            await call("jmag_capabilities", {}, "capabilities")

            async def build_study(name: str, coils: bool = True) -> None:
                await call("jmag_create_study", {"study_type": "Transient2D", "name": name}, must=True)
                if left:
                    await call("jmag_suppress_parts", {"parts": ["GapAir"], "study": name})
                await call("jmag_set_material", {"study": name, "parts": ["StatorCore", "RotorCore"], "material": "35JN210"})
                for m in meta["magnets"]:
                    await call("jmag_set_material", {"study": name, "parts": [m["name"]], "material": "N40H",
                                                     "orientation": {"pattern": "Radial",
                                                                     "outward": m["polarity"] == "outward"}})
                slots = [p["name"] for p in meta["parts"] if p["name"].startswith("Slot")]
                await call("jmag_set_material", {"study": name, "parts": slots, "material": "Copper",
                                                 "properties": {"EddyCurrentCalculation": 0}})
                await call("jmag_set_properties", {"study": name, "values": {"ModelThickness": meta["parameters"]["stack_mm"]}})
                await call("jmag_circuit_quick", {"study": name, "topology": "three_phase_current",
                                                  "coils": [{"name": f"Coil{ph}", "turns": 40, "resistance": 0.2} for ph in "UVW"],
                                                  "source": {"type": "sin", "amplitude": 10, "frequency": FE, "phase": -90}},
                           must=True)
                if coils:
                    for ph in "UVW":
                        c = meta["coils"][ph]
                        await call("jmag_add_fem_coil", {"study": name, "name": f"Phase{ph}", "component": f"Coil{ph}",
                                                         "sides": [{"parts": c["up"], "direction": "up"},
                                                                   {"parts": c["down"], "direction": "down"}]})
                await call("jmag_add_condition", {"study": name, "condition_type": "RotationMotion", "name": "Motion",
                                                  "parts": meta["rotor_parts"], "properties": {"AngularVelocity": RPM}})
                await call("jmag_add_condition", {"study": name, "condition_type": "Torque", "name": "Torque",
                                                  "parts": meta["rotor_parts"]})
                await call("jmag_set_step", {"study": name, "steps": SPP + 1, "time_step": 1 / (FE * SPP)})
                deg_per_step = RPM / 60 * 360 / (FE * SPP)
                await call("jmag_set_properties", {"study": name, "kind": "mesh", "values": {
                    "MeshType": 1, "AutoSlide": 1, "RadialDivision": 4,
                    "CircumferentialDivision": int(round(360 / deg_per_step)) * 6}})

            await build_study(S)
            await build_study("Base")     # clean copy (no design table) for the experiments
            await call("jmag_save_project", {})

            # ---- design table
            await call("jmag_set_equations", {"study": S, "equations": {"k_test": 2}}, "equation k_test")
            await call("jmag_case_table", {"study": S, "candidates": "Source3Ph"}, "case_table get (candidates)")
            await call("jmag_case_table", {"study": S, "action": "set", "add_parameters": ["Source3Ph: Amplitude"],
                                           "cases": [{"Source3Ph: Amplitude": 5, "k_test": 3}]}, "case_table set")
            await call("jmag_case_table", {"study": S, "action": "set", "add_parameters": ["Motion: NoSuchThing"]},
                       "case_table bad name (expect FAILED + hint)")
            tbl = await call("jmag_case_table", {"study": S}, "case_table get")

            # ---- run cases
            st = await call("jmag_run_cases", {"study": S, "wait_seconds": 30}, "run_cases (2 cases)")
            st = await wait_run(st)
            log.append({"step": "run_cases final", "ok": st.get("state") == "finished", "out": st})
            print("run_cases final state:", st.get("state"), st.get("cases_done"))
            means = {}
            for k in (0, 1):
                r = await call("jmag_get_result", {"study": S, "table": "Torque", "case": k}, f"get_result Torque case={k}")
                if r.get("ok"):
                    col = list(r["result"]["stats"])[0]
                    means[k] = r["result"]["stats"][col]["mean"]
            await call("jmag_list_results", {"study": S, "case": 1}, "list_results case=1")
            if len(means) == 2:
                log.append({"step": "torque ratio case1/case0 (expect ~0.5 for 5 A vs 10 A)", "ok": True,
                            "out": {"means": means, "ratio": means[1] / means[0] if means[0] else None}})
                print("torque means", means)

            # ---- loss study (from case 0 results)
            await call("jmag_case_table", {"study": S, "action": "set", "current_case": 0}, "current case 0")
            last = SPP + 1
            await call("jmag_loss_study", {"action": "create", "study": S, "name": "IronLoss",
                                           "parts": ["StatorCore", "RotorCore"], "frequency_hz": FE,
                                           "reference_steps": [2, last]}, "loss_study create+run")
            await call("jmag_loss_study", {"action": "results", "study": "IronLoss"}, "loss_study results")
            await call("jmag_save_project", {})

            # ---- exports (safe kinds)
            await call("jmag_export_table", {"study": S, "path": str(work / "exports" / "all_tables.csv")}, "export all_tables")
            await call("jmag_export_table", {"study": S, "path": str(work / "exports" / "torque.csv"), "kind": "table",
                                             "table": "Torque"}, "export table Torque")
            await call("jmag_export_table", {"study": S, "path": str(work / "exports" / "all_cases.csv"),
                                             "kind": "all_cases"}, "export all_cases")

            # ---- winding on fresh studies
            await build_study("WindFEM", coils=False)
            await call("jmag_winding", {"study": "WindFEM", "coils": meta["coils"], "phase_turns": {"U": 40, "V": 40, "W": 40}},
                       "winding fem_coil")
            await call("jmag_check", {"study": "WindFEM", "stage": "pre", "electrical_frequency_hz": FE}, "precheck WindFEM")
            await call("jmag_optimization_table", {"study": S}, "optimization get")
            await call("jmag_efficiency_map", {"action": "create", "study": S, "name": "Map1",
                                               "settings": {"max_voltage": 300}}, "efficiency map create (expect refusal)")
            await call("jmag_save_project", {})

            # ---- experiments
            ex = await call("jmag_experiment", {"kind": "back_emf", "study": "Base",
                                                "params": {"poles": POLES, "rpm": RPM, "steps_per_period": SPP,
                                                           "wait_seconds": 900}}, "experiment back_emf")
            ex = await call("jmag_experiment", {"kind": "cogging", "study": "Base",
                                                "params": {"poles": POLES, "slots": SLOTS, "rpm": 60,
                                                           "steps_per_span": 30, "wait_seconds": 900}}, "experiment cogging")
            await call("jmag_save_project", {})

            if not skip_risky:
                await build_study("WindJMAG", coils=False)
                await call("jmag_save_project", {})
                plan_coils = [{"phase": ph, "go_slot": a, "return_slot": b} for ph, a, b in
                              (("U", 1, 4), ("U", 7, 10), ("V", 3, 6), ("V", 9, 12), ("W", 5, 2), ("W", 11, 8))]
                await call("jmag_winding", {"study": "WindJMAG", "coils": plan_coils, "mode": "jmag_winding",
                                            "slots": SLOTS, "poles": POLES, "layers": 1,
                                            "reference_slot_parts": {"1": ["Slot01_U+"]}}, "winding jmag_winding (risky)")
                await call("jmag_save_project", {})
                await call("jmag_case_report", {"study": S, "path": str(work / "exports" / "case_report.html")},
                           "case report (risky)")
                await call("jmag_save_project", {})
                await call("jmag_export_table", {"study": S, "path": str(work / "exports" / "B_field.csv"), "kind": "field",
                                                 "result_type": "MagneticFluxDensity", "component": "Absolute",
                                                 "steps": str(last)}, "export field (risky)")
            await call("jmag_status", {}, "status at end")
    out = {"work_dir": str(work), "steps": [{k: v for k, v in e.items() if k != "args"} for e in log]}
    (work / "advanced_flow.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    failed = [e["step"] for e in log if not e["ok"]]
    print("FAILED steps:", failed)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--skip-risky", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(Path(a.work_dir).resolve(), a.pid, a.skip_risky))
