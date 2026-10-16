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
