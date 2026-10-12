"""Known-answer test (KAT) for the whole chain: geometry -> JMAG -> solver -> results -> checks.

Model: gapped C-core inductor, 60x60 mm core, 15 mm legs, 1 mm gap, 50 mm stack,
100 turns, 5 A, iron 50JN400 (far from saturation at 500 At).

Analytic (gap reluctance only, no fringing): L0 = N^2 mu0 A / g = 9.42 mH.
In 2D the gap fringing adds ~10-25 %; JMAG 23 on the default mesh gives ~11.0 mH.

Checks
  static   : L from flux linkage within [1.05, 1.35] x L0
  static   : energy identity W = 1/2 sum(i psi) within 3 %
  transient: psi/i at peak equals static L within 2 % (same physics, different solver path)
  transient: KVL residual (v = R i + dpsi/dt) < 1e-3 relative -> circuit + FEM coil wiring correct
  field    : peak |B| in the core between 0.3 and 2.2 T
"""
from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

from . import health
from .config import JmagInstall
from .dxf import c_core_inductor
from .worker_client import Worker

N, I, R, F_HZ, STACK = 100, 5.0, 0.1, 50.0, 50.0


def _ok(resp: dict, what: str) -> dict:
    if not resp.get("ok"):
        err = resp.get("error") or {}
        msgs = (resp.get("jmag") or {}).get("messages")
        raise RuntimeError(f"{what}: {err.get('message')} | hint: {err.get('hint')} | jmag: {msgs}")
    return resp.get("result")


def run_self_test(install: JmagInstall, keep_open: bool = False, work_dir: str | None = None) -> dict:
    t_start = time.time()
    work = Path(work_dir or tempfile.mkdtemp(prefix="jmag_selftest_"))
    work.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    timings: dict[str, float] = {}

    def check(name: str, value, lo, hi, unit: str = "") -> None:
        checks.append({"check": name, "value": value, "range": [lo, hi], "unit": unit,
                       "pass": value is not None and lo <= value <= hi})

    w = Worker(install, "selftest")
    launched = False
    try:
        w.start()
        geo = c_core_inductor(str(work / "ccore.dxf"))
        t = time.time()
        from .launcher import launch_detached
        pid = launch_detached(install)["pid"]
        launched = True
        info = _ok(w.call("connect", 300, pid=pid, launch=False, visible=True), "attach JMAG")
        timings["launch_s"] = round(time.time() - t, 1)
        _ok(w.call("project_new", 120, path=str(work / "selftest.jproj"), overwrite=True), "new project")
        imp = _ok(w.call("import_dxf", 300, path=str(work / "ccore.dxf"), model_name="ccore"), "import dxf")
        check("core area (geometry import)", sum(p["area"] for p in imp["parts"] if abs(p["area"] - geo["core_area_mm2"]) < 1),
              geo["core_area_mm2"] - 0.5, geo["core_area_mm2"] + 0.5, "mm2")
        _ok(w.call("rename_parts", 60, renames=[{"part": geo["core_point"], "name": "Core"},
                                                {"part": geo["coil_in_point"], "name": "CoilIn"},
                                                {"part": geo["coil_out_point"], "name": "CoilOut"}]), "rename parts")

        def common(study: str) -> None:
            _ok(w.call("set_material", 60, study=study, parts=["Core"], material="50JN400"), "core material")
            _ok(w.call("set_material", 60, study=study, parts=["CoilIn", "CoilOut"], material="Copper",
                       properties={"EddyCurrentCalculation": 0}), "coil material")
            _ok(w.call("set_properties", 60, target={"study": study, "kind": "study"},
                       values={"ModelThickness": STACK, "Energy": 1}), "stack length")

        # ---------------- static
        _ok(w.call("create_study", 60, study_type="Static2D", name="KAT_static"), "static study")
        common("KAT_static")
        _ok(w.call("add_current", 60, study="KAT_static", name="In", parts=["CoilIn"], turns=N, current=I, direction="up"), "current in")
        _ok(w.call("add_current", 60, study="KAT_static", name="Out", parts=["CoilOut"], turns=N, current=I, direction="down"), "current out")
        facts = _ok(w.call("study_facts", 60, study="KAT_static"), "facts")
        pre = health.summarize(health.preflight(facts))
        t = time.time()
        _ok(w.call("run", 1800, study="KAT_static"), "static run")
        timings["static_run_s"] = round(time.time() - t, 1)
        psi = _ok(w.call("get_result", 60, study="KAT_static", table="Magnetic Flux of Current Condition"), "flux")["columns"]
        W = _ok(w.call("get_result", 60, study="KAT_static", table="Stored Energy", columns=["Total"]), "energy")["columns"]["Total"][-1]
        L_static = (psi["In"][-1] + psi["Out"][-1]) / I
        L0 = N ** 2 * 4e-7 * math.pi * (15e-3 * STACK * 1e-3) / 1e-3
        check("static L / analytic L0 (fringing 5..35 %)", L_static / L0, 1.05, 1.35)
        ec = health.inductance_consistency([psi["In"][-1], psi["Out"][-1]], [I, I], W)
        check("static energy identity |W - 1/2 sum(i psi)| / W", ec["rel_diff"], 0.0, 0.03)

        # ---------------- transient with circuit
        _ok(w.call("create_study", 60, study_type="Transient2D", name="KAT_transient"), "transient study")
        common("KAT_transient")
        _ok(w.call("circuit_quick", 60, study="KAT_transient", topology="current_source",
                   coils=[{"name": "Coil1", "turns": N, "resistance": R}],
                   source={"type": "sin", "amplitude": I, "frequency": F_HZ, "phase": 0}, probe=True), "circuit")
        _ok(w.call("add_fem_coil", 60, study="KAT_transient", name="Phase", component="Coil1",
                   sides=[{"parts": ["CoilIn"], "direction": "up"}, {"parts": ["CoilOut"], "direction": "down"}]), "fem coil")
        _ok(w.call("set_step", 60, study="KAT_transient", steps=41, time_step=1.0 / F_HZ / 40), "steps")
        facts_t = _ok(w.call("study_facts", 60, study="KAT_transient"), "facts")
        pre_t = health.summarize(health.preflight(facts_t, F_HZ))
        t = time.time()
        _ok(w.call("run", 1800, study="KAT_transient"), "transient run")
        timings["transient_run_s"] = round(time.time() - t, 1)
        cur = _ok(w.call("get_result", 60, study="KAT_transient", table="LineCurrent"), "current")
        vol = _ok(w.call("get_result", 60, study="KAT_transient", table="TerminalVoltage"), "voltage")
        fl = _ok(w.call("get_result", 60, study="KAT_transient", table="FEMCoilFlux"), "flux linkage")
        i_t = cur["columns"]["Coil1"]
        v_t = vol["columns"][list(vol["columns"])[0]]
        p_t = fl["columns"]["Coil1"]
        k = max(range(len(i_t)), key=lambda j: abs(i_t[j]))
        check("transient psi/i at peak vs static L", (p_t[k] / i_t[k]) / L_static, 0.98, 1.02)
        kvl = health.kvl_residual(cur["x"], i_t, v_t, p_t, R)
        check("transient KVL residual (relative)", kvl["max_rel_residual"], 0.0, 1e-3)
        img = _ok(w.call("contour_image", 120, study="KAT_transient", path=str(work / "kat_B.png"), step=k + 1), "contour")
        check("peak |B| at peak current", img["max"], 0.3, 2.2, "T")
        _ok(w.call("project_save", 120), "save")
        result = {
            "ok": all(c["pass"] for c in checks), "checks": checks, "timings": timings, "work_dir": str(work),
            "values": {"L_static_mH": L_static * 1e3, "L_analytic_no_fringing_mH": L0 * 1e3,
                       "L_transient_mH": p_t[k] / i_t[k] * 1e3, "kvl": kvl, "B_max_T": img["max"]},
            "precheck": {"static": pre["verdict"], "transient": pre_t["verdict"],
                         "findings": pre["findings"] + pre_t["findings"]},
            "contour_image": img["path"], "jmag": {"version": info.get("version")},
        }
    except Exception as e:  # report, don't raise: the agent needs the diagnostics
        result = {"ok": False, "error": str(e), "checks": checks, "timings": timings, "work_dir": str(work),
                  "worker_log": str(w.log_path)}
    finally:
        if launched and not keep_open:
            try:
                w.call("quit_app", 60, save=False)
            except Exception:
                pass
        w.stop()
    result["total_s"] = round(time.time() - t_start, 1)
    return result
