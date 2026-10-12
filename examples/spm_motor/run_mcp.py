"""End-to-end SPM motor study through the public MCP tools only (no private APIs).

    python examples/spm_motor/run_mcp.py <work_dir> [--pid <running JMAG pid>] [--keep-open]

Does what an agent would do: draw geometry outside JMAG -> import -> name parts -> materials and magnet
orientation -> 3-phase circuit + FEM coils -> rotation, torque, slide mesh -> pre-check -> run ->
post-check -> physics checks (power balance, analytic torque) -> B contour.
Needs a licensed JMAG-Designer 23.x on this machine.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

sys.path.insert(0, str(Path(__file__).parent))
import make_geometry  # noqa: E402

RPM, POLES = 1500.0, 4
FE = RPM / 60 * POLES / 2          # 50 Hz
STEPS_PER_PERIOD, PERIODS = 60, 2


class Fail(RuntimeError):
    pass


async def main(work: Path, pid: int | None, keep_open: bool) -> dict:
    meta = make_geometry.build(work)
    S = "Load"
    params = StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(tool: str, args: dict, must: bool = True) -> dict:
                res = await s.call_tool(tool, args)
                text = next(c.text for c in res.content if c.type == "text")
                out = json.loads(text)
                status = "ok" if out.get("ok", True) else "FAILED"
                print(f"{tool:24s} {status}", flush=True)
                if must and out.get("ok") is False:
                    raise Fail(f"{tool}: {json.dumps(out.get('error'))[:600]}")
                return out

            await call("jmag_connect", {"pid": pid} if pid else {"launch_new": True})
            await call("jmag_new_project", {"path": str(work / "spm.jproj"), "overwrite": True})
            imp = await call("jmag_import_dxf", {"path": meta["dxf"], "model_name": "SPM"})
            await call("jmag_rename_parts", {"renames": [{"part": p["point"], "name": p["name"]} for p in meta["parts"]]})
            # The space between the closed rotor and stator outlines also became a region: name and suppress it so
            # JMAG's own air region (with the slide mesh) fills the air gap.
            left = [p for p in (await call("jmag_list_parts", {}))["result"]["parts"] if p["name"].startswith("0/")]
            if left:
                await call("jmag_rename_parts", {"renames": [{"part": left[0]["id"], "name": "GapAir"}]})
            await call("jmag_create_study", {"study_type": "Transient2D", "name": S})
            if left:
                await call("jmag_suppress_parts", {"parts": ["GapAir"], "study": S})
            await call("jmag_set_material", {"study": S, "parts": ["StatorCore", "RotorCore"], "material": "35JN210"})
            for m in meta["magnets"]:
                await call("jmag_set_material", {"study": S, "parts": [m["name"]], "material": "N40H",
                                                 "orientation": {"pattern": "Radial", "outward": m["polarity"] == "outward"}})
            slots = [p["name"] for p in meta["parts"] if p["name"].startswith("Slot")]
            await call("jmag_set_material", {"study": S, "parts": slots, "material": "Copper",
                                             "properties": {"EddyCurrentCalculation": 0}})
            await call("jmag_set_properties", {"study": S, "values": {"ModelThickness": meta["parameters"]["stack_mm"]}})
            await call("jmag_circuit_quick", {"study": S, "topology": "three_phase_current",
                                              "coils": [{"name": f"Coil{ph}", "turns": 40, "resistance": 0.2} for ph in "UVW"],
                                              "source": {"type": "sin", "amplitude": 10, "frequency": FE, "phase": -90}})
            for ph in "UVW":
                c = meta["coils"][ph]
                await call("jmag_add_fem_coil", {"study": S, "name": f"Phase{ph}", "component": f"Coil{ph}",
                                                 "sides": [{"parts": c["up"], "direction": "up"},
                                                           {"parts": c["down"], "direction": "down"}]})
            await call("jmag_add_condition", {"study": S, "condition_type": "RotationMotion", "name": "Motion",
                                              "parts": meta["rotor_parts"], "properties": {"AngularVelocity": RPM}})
            await call("jmag_add_condition", {"study": S, "condition_type": "Torque", "name": "Torque",
                                              "parts": meta["rotor_parts"]})
            await call("jmag_set_step", {"study": S, "steps": STEPS_PER_PERIOD * PERIODS + 1,
                                         "time_step": 1 / (FE * STEPS_PER_PERIOD)})
            deg_per_step = RPM / 60 * 360 / (FE * STEPS_PER_PERIOD)
            await call("jmag_set_properties", {"study": S, "kind": "mesh", "values": {
                "MeshType": 1, "AutoSlide": 1, "RadialDivision": 4, "CircumferentialDivision": int(round(360 / deg_per_step)) * 6}})
            await call("jmag_save_project", {})
            pre = await call("jmag_check", {"study": S, "stage": "pre", "electrical_frequency_hz": FE})
            print("pre-check:", pre["verdict"], [f["code"] for f in pre["findings"]])
            st = await call("jmag_run", {"study": S, "wait_seconds": 900, "electrical_frequency_hz": FE}, must=False)
            while st.get("state") == "running":
                await asyncio.sleep(5)
                st = await call("jmag_run_status", {}, must=False)
            if st.get("state") != "finished":
                raise Fail(f"run failed: {json.dumps(st)[:1500]}")
            post = st["post"]["health"]
            print("post-check:", post["verdict"])
            for f in post["findings"]:
                print("   ", f["level"], f["code"], " ".join(f["message"].split())[:160])
            pb = await call("jmag_physics_check", {"study": S, "check": "power_balance", "speed_rpm": RPM,
                                                   "electrical_frequency_hz": FE})
            flux = await call("jmag_get_result", {"study": S, "table": "Coil Flux-Linkage", "columns": ["CoilU"],
                                                  "electrical_frequency_hz": FE})
            psi1 = flux["result"]["stats"]["CoilU"]["last_period"]["fundamental"]
            t_analytic = 1.5 * (POLES / 2) * psi1 * 10.0
            img = await call("jmag_contour_image", {"study": S, "path": str(work / "B_last_step.png")})
            summary = {
                "pre_check": pre["verdict"], "post_check": post["verdict"],
                "mean_torque_Nm": pb["mean_torque_Nm"], "torque_from_flux_Nm": t_analytic,
                "power_balance_rel_diff": pb["rel_diff"], "B_max_T": img["result"]["max"],
                "contour": img["result"]["path"], "elements": imp and None,
            }
            print(json.dumps(summary, indent=2))
            if not keep_open:
                await call("jmag_quit", {"save": True}, must=False)
            ok = pb["ok"] and abs(abs(pb["mean_torque_Nm"]) - t_analytic) / t_analytic < 0.05
            if not ok:
                raise Fail("physics checks failed")
            return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--keep-open", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(Path(a.work_dir).resolve(), a.pid, a.keep_open))
