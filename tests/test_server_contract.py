"""0.4.0 tool contract with the worker replaced by canned op responses (no JMAG): every tool has ok + seconds,
ok (tool ran) is separate from passed (physics held), unverified results are labelled, physics-check defaults,
run status / cancel without a bridge-owned job, dq kinds, n_harmonics, jmag_dxf_inspect."""
import asyncio
import math
import time

import pytest

from jmag_bridge import server, server_support as sup

F_E = 100.0                       # Hz, 8 poles at 1500 rpm
RPM = 1500.0
N = 241                           # 4 electrical periods at 60 steps
T = [k / (60 * F_E) for k in range(N)]
PSI, AMP, R = 0.05, 10.0, 0.05
SHIFT = (0.0, -2 * math.pi / 3, 2 * math.pi / 3)


def _psi(k, ph):
    return PSI * math.cos(2 * math.pi * F_E * T[k] + SHIFT[ph])


def _cur(k, ph):
    return AMP * math.cos(2 * math.pi * F_E * T[k] + SHIFT[ph] + math.pi / 2)


def _power_torque():
    """Mean of sum i dpsi/dt (backward differences, as health.power_balance) over the last period / omega."""
    m = 60
    p = [sum(_cur(k, ph) * (_psi(k, ph) - _psi(k - 1, ph)) / (T[k] - T[k - 1]) for ph in range(3))
         for k in range(N - m, N)]
    return sum(p) / m / (RPM * 2 * math.pi / 60)


def _facts(study, torque_cond=True, motion=True):
    conds = []
    if motion:
        conds.append({"name": "Rot", "type": "RotationMotion", "parts": [1],
                      "properties": [{"name": "AngularVelocity", "value": RPM}]})
    if torque_cond:
        conds.append({"name": "T", "type": "Torque", "parts": [1], "properties": []})
    return {"model": "M", "study": study, "type": "Transient2D", "dimension": 2, "has_result": True,
            "has_mesh": True, "elements": 6000, "model_thickness": 50.0, "length_unit": "mm",
            "parts": [{"id": 1, "name": "Core", "material": "35JN210", "eddy_current": 0},
                      {"id": 2, "name": "Magnet", "material": "N42", "is_magnet": True}],
            "conditions": conds,
            "circuit": {"has_circuit": True, "components": [
                {"name": "Source3Ph", "type": "3PhaseCurrentSource",
                 "properties": [{"name": "Frequency", "value": F_E}, {"name": "Amplitude", "value": AMP}]},
                {"name": "CoilU", "type": "Coil", "properties": [{"name": "Turn", "value": 20}]},
                {"name": "CoilV", "type": "Coil", "properties": [{"name": "Turn", "value": 20}]},
                {"name": "CoilW", "type": "Coil", "properties": [{"name": "Turn", "value": 20}]},
                {"name": "Ground", "type": "Ground", "properties": []}]},
            "step": {"StepType": 0, "Step": N, "TimeStep": T[1]}, "equations": {}}


class Fake:
    """Canned ops for a 3-phase PMSM transient study 'Load' (consistent power balance) or 'Dead' (zero torque)."""

    def __init__(self, torque_scale=1.0):
        self.calls = []
        self.install = None
        self.main = None
        self.job = None
        self.journal = server.B.journal
        self.session = "test"
        self.torque_scale = torque_scale

    def table(self, study, name):
        coils = ("CoilU", "CoilV", "CoilW")
        if name in ("LineCurrent", "Circuit Current"):
            return {"columns": {c: [_cur(k, i) for k in range(N)] for i, c in enumerate(coils)}, "unit": "A"}
        if name in ("FEMCoilFlux", "Coil Flux-Linkage"):
            return {"columns": {c: [_psi(k, i) for k in range(N)] for i, c in enumerate(coils)}, "unit": "Wb"}
        if name == "Torque":
            tq = 0.0 if study == "Dead" else _power_torque() * self.torque_scale
            ripple = 0.0 if study == "Dead" else 0.01
            return {"columns": {"Torque": [tq + ripple * math.sin(12 * math.pi * F_E * t) for t in T]}, "unit": "N*m"}
        return None

    async def call(self, op, timeout=600, **a):
        self.calls.append((op, a))
        study = a.get("study") or (a.get("target") or {}).get("study")
        if op == "study_facts":
            return {"ok": True, "result": _facts(study)}
        if op == "report":
            return {"ok": True, "result": {"has_result": True, "errors": [], "warnings": [], "calc_folder": None}}
        if op == "list_results":
            return {"ok": True, "result": {"tables": [{"index": 0, "name": "Torque", "title": "Torque"},
                                                      {"index": 1, "name": "Circuit Current", "title": "Circuit Current"},
                                                      {"index": 2, "name": "Coil Flux-Linkage",
                                                       "title": "Coil Flux-Linkage"}]}}
        if op == "get_result":
            names = {0: "Torque", 1: "Circuit Current", 2: "Coil Flux-Linkage"}
            name = names.get(a["table"], a["table"])
            tb = self.table(study, name)
            if tb is None:
                return {"ok": False, "error": {"message": f"result table {name!r} not found"}}
            cols = tb["columns"]
            if a.get("columns"):
                cols = {c: v for c, v in cols.items() if c in a["columns"]}
            return {"ok": True, "result": {"table": name, "unit": tb["unit"], "x_name": "time", "x_unit": "s",
                                           "x": T, "columns": cols}}
        if op == "export_results":
            return {"ok": True, "result": {"written": [], "out_dir": a["out_dir"]}}
        return {"ok": False, "error": {"message": f"op {op} not faked"}}


@pytest.fixture
def fake(monkeypatch):
    fb = Fake()
    monkeypatch.setattr(server, "B", fb)
    monkeypatch.setattr(server, "VERIFY", sup.VerifyRecord())
    return fb


# --------------------------------------------------------------------------- item 1: envelopes

def test_envelope_always_has_seconds():
    assert server.envelope({"ok": True, "result": {"a": 1}}) == {"ok": True, "result": {"a": 1}, "seconds": 0.0}
    assert server.envelope({"ok": True, "result": 1, "elapsed": 2.5})["seconds"] == 2.5


def test_journal_and_knowledge_tools_are_enveloped(fake, monkeypatch, tmp_path):
    j = asyncio.run(server.jmag_journal())
    assert j["ok"] is True and "entries" in j["result"] and "last" in j["result"] and "seconds" in j
    for coro in (server.jmag_docs_search("x"), server.jmag_docs_page("p.html"), server.jmag_api_lookup("Study.Run")):
        r = asyncio.run(coro)              # no install in the fake: a clean ok=false, not an exception
        assert r["ok"] is False and "JMAG_HOME" in r["error"]["hint"] and "seconds" in r
    st = asyncio.run(server.jmag_status())
    assert st["ok"] is False and st["result"]["install"] is None and "JMAG_HOME" in st["error"]["hint"]


def test_cleanup_star_and_self_test_enveloped(fake, monkeypatch):
    monkeypatch.setattr(server.solver_monitor, "cleanup_orphans",
                        lambda: {"killed_orphan_modeller_pids": [5], "solver_processes": []})
    c = asyncio.run(server.jmag_cleanup())
    assert c["ok"] and c["result"]["killed_orphan_modeller_pids"] == [5] and "seconds" in c

    monkeypatch.setattr(server.star, "star_repo", lambda action: {"ok": True, "method": "dismissed",
                                                                  "message": "ok", "url": "u"})
    s = asyncio.run(server.jmag_star_repo("dismiss"))
    assert s == {"ok": True, "result": {"method": "dismissed", "message": "ok", "url": "u"}, "seconds": s["seconds"]}
    monkeypatch.setattr(server.star, "star_repo", lambda action: {"ok": False, "method": "browser",
                                                                  "message": "no browser", "url": "u"})
    s = asyncio.run(server.jmag_star_repo("star"))
    assert s["ok"] is False and s["error"]["message"] == "no browser" and s["result"]["url"] == "u"

    from jmag_bridge import selftest
    monkeypatch.setattr(server.star, "first_run_prompt", lambda: None)
    fake.install = object()
    checks = [{"check": "L", "pass": True}, {"check": "kvl", "pass": False}]
    monkeypatch.setattr(selftest, "run_self_test", lambda *a: {"ok": False, "checks": checks, "total_s": 1.0})
    r = asyncio.run(server.jmag_self_test())
    assert r["ok"] is True and r["result"]["passed"] is False and r["result"]["verdict"] == "FAIL"
    assert "kvl" in r["result"]["hint"] and "ok" not in r["result"]
    monkeypatch.setattr(selftest, "run_self_test", lambda *a: {"ok": False, "error": "boom", "checks": []})
    r = asyncio.run(server.jmag_self_test())
    assert r["ok"] is False and "boom" in r["error"]["message"]


# --------------------------------------------------------------------------- item 2: ran vs passed

def test_check_pre_passed_and_failed(fake):
    r = asyncio.run(server.jmag_check("Load", "pre"))
    assert r["ok"] is True and r["result"]["passed"] is True and r["result"]["verdict"] in ("PASS", "CHECK")
    assert r["verdict"] == r["result"]["verdict"] and "findings" in r            # old top-level keys kept
    assert "FE_DERIVED" in {f["code"] for f in r["findings"]}                    # 100 Hz from the 3-phase source

    orig = fake.call

    async def no_material(op, timeout=600, **a):
        env = await orig(op, timeout, **a)
        if op == "study_facts":
            env["result"]["parts"][0]["material"] = None
        return env

    fake.call = no_material
    r = asyncio.run(server.jmag_check("Load", "pre"))
    assert r["ok"] is True and r["result"]["passed"] is False and r["result"]["verdict"] == "FAIL"
    assert "NO_MATERIAL" in r["result"]["hint"]


def test_check_post_zero_torque_is_flagged_and_recorded(fake):
    r = asyncio.run(server.jmag_check("Dead", "post"))
    codes = {f["code"] for f in r["health"]["findings"]}
    assert r["ok"] is True and "TORQUE_ZERO" in codes
    assert r["result"]["verdict"] == "CHECK" and r["result"]["passed"] is True and "TORQUE_ZERO" in r["result"]["hint"]
    assert server.VERIFY.post("Dead")["verdict"] == "CHECK"
    bad = asyncio.run(server.jmag_check("Dead", "middle"))
    assert bad["ok"] is False and "stage" in bad["error"]["hint"]


def test_physics_check_power_balance_defaults_and_failure(fake):
    r = asyncio.run(server.jmag_physics_check("Load", "power_balance"))   # everything derived from the study
    assert r["ok"] is True, r
    res = r["result"]
    assert res["passed"] is True and res["verdict"] == "PASS" and res["rel_diff"] < 1e-6 and "ok" not in res
    assert set(res["defaults_used"]) == {"speed_rpm", "electrical_frequency_hz"}
    assert res["speed_rpm"] == RPM and res["electrical_frequency_hz"] == F_E

    fake.torque_scale = 2.0                                                  # torque disagrees with the flux
    r = asyncio.run(server.jmag_physics_check("Load", "power_balance", speed_rpm=RPM, electrical_frequency_hz=F_E))
    assert r["ok"] is True and r["result"]["passed"] is False and r["result"]["verdict"] == "FAIL"
    assert "defaults_used" not in r["result"] and "speed_rpm" in r["result"]["hint"]

    r = asyncio.run(server.jmag_physics_check("Load", "kvl"))                # 3 coils, no probe: cannot derive
    assert r["ok"] is False and "coil" in r["error"]["message"] and "resistance" in r["error"]["hint"]
    r = asyncio.run(server.jmag_physics_check("Load", "nope"))
    assert r["ok"] is False and "power_balance" in r["error"]["hint"]


# --------------------------------------------------------------------------- item 5: unverified results

def test_unverified_tracks_checks_and_resets_on_rerun(fake, tmp_path):
    r = asyncio.run(server.jmag_get_result("Load", "Torque"))
    assert r["result"]["unverified"] == ["post-check", "physics-check"]
    asyncio.run(server.jmag_check("Load", "post"))
    assert asyncio.run(server.jmag_get_result("Load", "Torque"))["result"]["unverified"] == ["physics-check"]
    asyncio.run(server.jmag_physics_check("Load", "power_balance"))
    assert asyncio.run(server.jmag_get_result("Load", "Torque"))["result"]["unverified"] == []
    e = asyncio.run(server.jmag_export_results("Load", str(tmp_path)))
    assert e["ok"] and e["result"]["unverified"] == []
    assert asyncio.run(server.jmag_get_result("Other", "Torque"))["result"]["unverified"] == [
        "post-check", "physics-check"]
    asyncio.run(server.run_op("run", 60, study="Load"))                       # a new solve invalidates the record
    assert asyncio.run(server.jmag_get_result("Load", "Torque"))["result"]["unverified"] == [
        "post-check", "physics-check"]


def test_plot_and_report_use_the_record(fake, tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    out = asyncio.run(server.jmag_plot("waveform", study="Load", table="Torque", out=str(tmp_path / "w.png")))
    assert __import__("json").loads(out[0])["result"]["unverified"] == ["post-check", "physics-check"]
    asyncio.run(server.jmag_physics_check("Load", "power_balance"))
    rep = asyncio.run(server.jmag_make_report("t", ["Load"], str(tmp_path / "rep"), include_images=False))
    md = open(rep["result"]["path"], encoding="utf-8").read()
    assert "power_balance" in md                                              # recorded invariant used
    assert "post-check not run" in md and "physics-check not run" not in md


# --------------------------------------------------------------------------- item 10: n_harmonics, dq kinds

def test_get_result_n_harmonics(fake):
    r = asyncio.run(server.jmag_get_result("Load", "Circuit Current", electrical_frequency_hz=F_E))
    lp = r["result"]["stats"]["CoilU"]["last_period"]
    assert len(lp["harmonic_amplitudes"]) == 10 and abs(lp["harmonic_amplitudes"][1] - AMP) < 1e-6
    r = asyncio.run(server.jmag_get_result("Load", "Circuit Current", electrical_frequency_hz=F_E, n_harmonics=25))
    assert len(r["result"]["stats"]["CoilU"]["last_period"]["harmonic_amplitudes"]) == 25
    for bad in (0, 65, 2.5):
        r = asyncio.run(server.jmag_get_result("Load", "Torque", n_harmonics=bad))
        assert r["ok"] is False and "64" in r["error"]["hint"]


def test_estimate_dq_kinds():
    r = asyncio.run(server.jmag_estimate("dq_to_phase", {"id": 0, "iq": 10, "theta_e_deg": 0}))
    assert r["ok"] and abs(r["result"]["details"]["phase_currents_A"]["U"]) < 1e-9
    r = asyncio.run(server.jmag_estimate("phase_to_dq", {"abc": [1.0, -0.5, -0.5], "theta_e_deg": 0}))
    assert r["ok"] and abs(r["result"]["details"]["d"] - 1.0) < 1e-12
    lst = asyncio.run(server.jmag_estimate("list"))["result"]
    assert lst["dq_to_phase"]["required"] == ["id", "iq", "theta_e_deg"] and "torque_dq" in lst
    r = asyncio.run(server.jmag_estimate("phase_to_dq", {"abc": [1.0]}))
    assert r["ok"] is False


# --------------------------------------------------------------------------- item 7: runs the bridge does not own

def test_run_status_without_job(fake, monkeypatch):
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [{"pid": 4, "name": "ExecSolver.exe"}], "calc_folders": ["c"],
        "newest_monitor_age_s": 700.0, "stalled": True})
    monkeypatch.setattr(server.winui, "dialogs", lambda title_filter="": [])
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is True and st["state"] == "running" and st["owned"] is False and st["stalled"] is True
    assert "jmag_cancel_run" in st["hint"] and "jmag_dialogs" in st["hint"]
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [], "calc_folders": [], "newest_monitor_age_s": None, "stalled": False})
    st = asyncio.run(server.jmag_run_status())
    assert st["ok"] is False and st["state"] == "none" and st["solver_processes"] == [] and "error" in st


def test_cancel_without_job_uses_the_abort_protocol(fake, monkeypatch):
    clicks, killed = [], []
    monkeypatch.setattr(server.winui, "dialogs", lambda title_filter="": (
        [{"kind": "progress", "title": "Run Analysis", "text": ""}] if title_filter == "Run Analysis" else []))
    monkeypatch.setattr(server.winui, "click", lambda button, title="", wait=0: (
        clicks.append(button) or {"clicked": button}))
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [{"pid": 4}], "calc_folders": ["C:/p/Case1"], "newest_monitor_age_s": 1.0, "stalled": False})
    procs = [[{"pid": 4}]] * 25                                               # solver never exits by itself
    monkeypatch.setattr(server.solver_monitor, "solver_processes", lambda folder=None, **k: procs.pop() if procs else [])
    monkeypatch.setattr(server.solver_monitor, "kill_solver", lambda folder=None: killed.append(folder) or [4])

    async def no_sleep(_):
        return None

    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is True and r["unverified_live"] is True
    assert clicks == ["Abort", "Yes, and open results"]                       # verified order: Abort, answer, kill
    assert killed == ["C:/p/Case1"] and any("terminated" in s for s in r["steps"])

    monkeypatch.setattr(server.winui, "dialogs", lambda title_filter="": [])
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is False and "nothing is running" in r["error"]["message"]


def test_cancel_without_job_never_kills_an_unknown_folder(fake, monkeypatch):
    killed = []
    monkeypatch.setattr(server.winui, "dialogs", lambda title_filter="": (
        [{"kind": "progress", "title": "Run Analysis", "text": ""}] if title_filter == "Run Analysis" else []))
    monkeypatch.setattr(server.winui, "click", lambda button, title="", wait=0: {"clicked": button})
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [{"pid": 4}, {"pid": 5}], "calc_folders": ["a", "b"], "newest_monitor_age_s": 1.0,
        "stalled": False})
    monkeypatch.setattr(server.solver_monitor, "kill_solver", lambda folder=None: killed.append(folder) or [])
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is True and killed == [] and any("not killed" in s for s in r["steps"])


def test_status_reports_solver_fields(fake, monkeypatch):
    from jmag_bridge import launcher
    fake.install = type("I", (), {"as_dict": lambda self: {"home": "x"}})()
    monkeypatch.setattr(launcher, "running_instances", lambda: [])
    monkeypatch.setattr(server.winui, "dialogs", lambda title_filter="": [])
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [], "calc_folders": [], "newest_monitor_age_s": None, "stalled": False})
    st = asyncio.run(server.jmag_status())
    assert st["ok"] is True and st["result"]["stalled"] is False and st["result"]["solver_processes"] == []
    assert "newest_monitor_age_s" in st["result"] and "seconds" in st


# --------------------------------------------------------------------------- item 3: experiment failures surface

def test_experiment_failure_is_not_swallowed(fake, monkeypatch):
    async def call(op, timeout=600, **a):
        if op == "duplicate_study":
            return {"ok": True, "result": {"study": a["new_name"]}}
        if op == "experiment_setup":
            return {"ok": True, "result": {}}
        return await Fake.call(fake, op, timeout, **a)

    fake.call = call

    async def failed_run(**kw):
        return {"ok": False, "state": "failed", "solver_aborted": True,
                "error": {"message": "the solver aborted: jam was aborted", "hint": "fix the model"}}

    monkeypatch.setattr(server, "jmag_run", failed_run)
    r = asyncio.run(server.jmag_experiment("back_emf", "Load", {"poles": 8, "rpm": 1500}))
    assert r["ok"] is False and "aborted" in r["error"]["message"]
    assert "jmag_delete_study(study='Load_back_emf')" in r["error"]["hint"] and r["result"]["run"]["state"] == "failed"

    async def refused(**kw):
        return {"ok": False, "error": {"message": "pre-run check failed", "hint": "fix the errors or pass force=true"},
                "precheck": {"verdict": "FAIL", "findings": [{"code": "NO_MATERIAL"}]}}

    monkeypatch.setattr(server, "jmag_run", refused)
    r = asyncio.run(server.jmag_experiment("back_emf", "Load", {"poles": 8, "rpm": 1500}))
    assert r["ok"] is False and "pre-run check" in r["error"]["message"]
    assert r["result"]["precheck"]["verdict"] == "FAIL" and "jmag_delete_study" in r["error"]["hint"]

    async def finished(**kw):
        return {"ok": True, "state": "finished", "post": {"health": {"verdict": "PASS"}}}

    monkeypatch.setattr(server, "jmag_run", finished)
    r = asyncio.run(server.jmag_experiment("load_point", "Load", {"poles": 8, "rpm": 1500, "current_peak": 10}))
    assert r["ok"] is True and r["result"]["unverified"] == ["post-check", "physics-check"]


# --------------------------------------------------------------------------- item 9: jmag_dxf_inspect

def test_dxf_inspect_tool(tmp_path):
    from jmag_bridge import dxf
    p = tmp_path / "c.dxf"
    dxf.c_core_inductor(str(p))
    r = asyncio.run(server.jmag_dxf_inspect(str(p)))
    assert r["ok"] and r["result"]["region_count"] == 3 and "seconds" in r
    bad = tmp_path / "bad.dxf"
    bad.write_text("0\nSECTION\n2\nENTITIES\n0\nSPLINE\n8\n0\n0\nENDSEC\n0\nEOF\n")
    r = asyncio.run(server.jmag_dxf_inspect(str(bad)))
    assert r["ok"] is False and "polylines" in r["error"]["hint"]
    r = asyncio.run(server.jmag_dxf_inspect(str(tmp_path / "missing.dxf")))
    assert r["ok"] is False


def test_new_tool_is_read_only_and_listed():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    t = tools["jmag_dxf_inspect"]
    ann = getattr(t, "annotations", None)
    assert ann is not None and (getattr(ann, "readOnlyHint", None) or getattr(ann, "read_only_hint", None)) is True
    assert "BLOCKING_TOOLS" in tools["jmag_mesh"].description and "3600" in tools["jmag_mesh"].description
    assert "jmag_cancel_run" in server.INSTRUCTIONS and "wait_seconds" in server.INSTRUCTIONS
    assert "progress" in tools["jmag_dialogs"].description
