"""Glue of the server-side analysis tools, with the worker replaced by canned op responses (no JMAG)."""
import asyncio
import math

import pytest

from jmag_bridge import server

T = [k * 1e-4 for k in range(201)]
TORQUE = [2.0 + 0.1 * math.sin(2 * math.pi * 300 * t) for t in T]


def _facts(study, thickness=50.0):
    return {"model": "M", "study": study, "type": "Transient2D", "dimension": 2, "has_result": True, "has_mesh": True,
            "elements": 6000, "model_thickness": thickness, "length_unit": "mm",
            "parts": [{"id": 1, "name": "Core", "material": "35JN210", "eddy_current": 0},
                      {"id": 2, "name": "Magnet", "material": "N42", "is_magnet": True}],
            "conditions": [{"name": "Rot", "type": "RotationMotion", "parts": [1],
                            "properties": [{"name": "AngularVelocity", "value": 3000}]},
                           {"name": "T", "type": "Torque", "parts": [1], "properties": []}],
            "circuit": {"has_circuit": False}, "step": {"StepType": 0, "Step": 201, "TimeStep": 1e-4}}


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.install = None
        self.job = None
        self.journal = server.B.journal
        self.session = "test"

    async def call(self, op, timeout=600, **a):
        self.calls.append((op, a))
        study = a.get("study") or (a.get("target") or {}).get("study")
        scale = 1.02 if study == "Fine" else 1.0
        if op == "study_facts":
            return {"ok": True, "result": _facts(study, 50.0 if study != "Fine" else 50.0)}
        if op == "get_properties":
            return {"ok": True, "result": {"properties": [{"name": "MultiCPU", "value": 4 if study != "Fine" else 8}]}}
        if op == "report":
            return {"ok": True, "result": {"has_result": True, "errors": [], "warnings": [], "calc_folder": None}}
        if op == "list_results":
            return {"ok": True, "result": {"tables": [{"index": 0, "name": "Torque", "title": "Torque:N*m"}]}}
        if op == "get_result":
            return {"ok": True, "result": {"table": "Torque", "unit": "N*m", "x_name": "time", "x_unit": "s", "x": T,
                                           "columns": {"Torque": [v * scale for v in TORQUE]}}}
        if op == "app_info":
            return {"ok": True, "result": {"project_path": "p.jproj", "models": [
                {"name": "M", "dimension": 2, "parts": 2,
                 "studies": [{"name": "Coarse", "type": "Transient2D", "has_result": True},
                             {"name": "Empty", "type": "Static2D", "has_result": False}]}]}}
        if op == "list_conditions":
            n = 0 if study == "Empty" else 2
            return {"ok": True, "result": {"conditions": [{"type": "Torque"}] * n}}
        if op == "calc_folder":
            return {"ok": True, "result": {"folder": None, "has_result": study != "Empty"}}
        return {"ok": False, "error": {"message": f"op {op} not faked"}}


@pytest.fixture
def fake(monkeypatch):
    fb = FakeBridge()
    monkeypatch.setattr(server, "B", fb)
    return fb


def test_compare_tool(fake):
    r = asyncio.run(server.jmag_compare("Coarse", "Fine"))
    assert r["ok"]
    changed = {c["key"] for c in r["result"]["setup_diff"]["changed"]}
    assert changed == {"study.MultiCPU"}
    sig = r["result"]["results"]["significant"]
    assert any(x["stat"] == "mean" and abs(x["delta_pct"] - 2.0) < 1e-6 for x in sig)


def test_inventory_tool(fake):
    r = asyncio.run(server.jmag_inventory())
    st = {s["name"]: s for s in r["result"]["models"][0]["studies"]}
    assert st["Coarse"]["solved"] and st["Coarse"]["result_tables"] == 1 and st["Coarse"]["conditions"] == 2
    assert "M/Empty: not solved" in r["result"]["flags"]


def test_make_report_tool(fake, tmp_path):
    r = asyncio.run(server.jmag_make_report("t", ["Coarse"], str(tmp_path), include_images=False,
                                            electrical_frequency_hz=300, invariants={"Coarse": [{"check": "kvl", "ok": True}]},
                                            sweeps=[{"title": "sw", "columns": ["a"], "rows": [[1]]}]))
    assert r["ok"], r
    md = open(r["result"]["path"], encoding="utf-8").read()
    assert "Study `Coarse`" in md and "| Torque:N*m | Torque |" in md and "kvl" in md and "Sweep: sw" in md
    assert "Coarse" in r["result"]["verdicts"]


def test_plot_tool_from_study(fake, tmp_path):
    pytest.importorskip("matplotlib")
    out = asyncio.run(server.jmag_plot("fft", study="Coarse", table="Torque", out=str(tmp_path / "f.png"),
                                       electrical_frequency_hz=300))
    assert '"ok": true' in out[0] and len(out) == 2
    assert ("get_result", {"study": "Coarse", "table": "Torque"}) in fake.calls


# --------------------------------------------------------------------------- per-case reads vs. running jobs

def test_case_reads_refused_while_a_job_runs(fake, tmp_path):
    fake.job = {"id": "j1", "kind": "cases", "study": "Coarse", "done": False}
    for coro in (server.jmag_get_result("Coarse", "Torque", case=1),
                 server.jmag_list_results("Coarse", case=1),
                 server.jmag_loss_study(action="results", study="Loss", case=0),
                 server.jmag_export_table("Coarse", str(tmp_path / "t.csv"), case=1)):
        r = asyncio.run(coro)
        assert not r["ok"] and "jmag_run_status" in r["error"]["hint"], r
    assert fake.calls == []          # nothing reached the worker
    r = asyncio.run(server.jmag_list_results("Coarse"))   # current case: still allowed
    assert r["ok"]
    fake.job["done"] = True
    r = asyncio.run(server.jmag_list_results("Coarse", case=1))
    assert r["ok"] and ("list_results", {"study": "Coarse", "case": 1}) in fake.calls


def test_run_cases_status_running_and_finished(fake, tmp_path, monkeypatch):
    import json
    import time
    monkeypatch.setattr(server.winui, "dialogs", lambda: [])
    prog = tmp_path / "j.progress.json"
    prog.write_text(json.dumps({"state": "running", "cases": [0, 1, 2], "current_case": 1, "folder": None,
                                "done": [{"case": 0, "state": "finished", "seconds": 3.2, "calc_folder": "x"}]}))
    job = {"id": "j1", "kind": "cases", "study": "Coarse", "started": time.time() - 10, "done": False,
           "cases": None, "progress_file": str(prog), "total_steps": 100}
    st = asyncio.run(server._run_cases_status(job))
    assert st["state"] == "running" and st["cases_done"] == "1/3" and st["current_case"] == 1
    assert st["done"] == [{"case": 0, "state": "finished", "seconds": 3.2}]
    job.update(done=True, finished=time.time(), response={"ok": True, "result": {"finished": [0, 1, 2]}})
    st = asyncio.run(server._run_cases_status(job))
    assert st["state"] == "finished" and st["run"]["result"]["finished"] == [0, 1, 2] and "case=k" in st["hint"]
    job["response"] = {"ok": False, "error": {"message": "no case produced results"}}
    assert asyncio.run(server._run_cases_status(job))["state"] == "failed"


def _finished_job(response):
    import time
    return {"id": "r1", "study": "Coarse", "model": None, "started": time.time() - 5, "finished": time.time(),
            "done": True, "folder": "calc", "freq": None, "response": response}


def test_run_status_top_level_ok_and_solver_aborted(fake, monkeypatch):
    # jmag_run / jmag_run_status used to carry no top-level "ok", so a client reading out.get("ok", True) took an
    # aborted solve for a success. Messages below are recorded from JMAG-Designer 23.0 runs.
    log = [{"status": "Error", "text": "The solver module was aborted.", "step": None}]
    monkeypatch.setattr(server.solver_monitor, "log_messages", lambda folder: log)
    monkeypatch.setattr(server.solver_monitor, "convergence", lambda *a, **k: None)
    failed = {"ok": False, "error": {"type": "OpError", "message": "the solver finished without results (failed or cancelled)",
                                     "data": {"errors": ["The solver module jam was aborted.\nPlease confirm the model."]}}}
    fake.job = _finished_job(failed)
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is False and st["state"] == "failed" and st["solver_aborted"] is True
    assert st["error"]["message"].startswith("the solver aborted: The solver module jam was aborted.")
    assert "jmag_run again" in st["error"]["hint"] and st["solver_log_warnings"] == log

    # results present, but the solver log says it aborted: not usable, though state keeps its old meaning
    fake.job = _finished_job({"ok": True, "result": {"has_result": True, "errors": []}})
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is False and st["state"] == "finished" and st["solver_aborted"] is True

    monkeypatch.setattr(server.solver_monitor, "log_messages", lambda folder: [])
    fake.job = _finished_job({"ok": True, "result": {"has_result": True, "errors": []}})
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is True and st["solver_aborted"] is False and "error" not in st

    fake.job = _finished_job({"ok": False, "error": {"message": "cancelled by user"}})
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is False and st["solver_aborted"] is False and st["error"]["message"] == "cancelled by user"


def test_run_status_running_is_ok(fake, monkeypatch):
    import time
    monkeypatch.setattr(server.winui, "dialogs", lambda: [])
    fake.job = {"id": "r1", "study": "Coarse", "model": None, "started": time.time(), "done": False, "folder": None}
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is True and st["state"] == "running"
