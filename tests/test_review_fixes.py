"""Regression tests for the 0.4.0 review findings (no JMAG): cancel scoping, envelope invariants in reports,
run_cases invalidation, per-model verification record, mirrored DXF entities and $INSUNITS, solver command-line
parsing, THD independent of n_harmonics, default hints, plausible pole equations, f_e only for Transient studies."""
import asyncio
import math
import time

import pytest

from jmag_bridge import dxf_inspect, experiments, health, reporting, server, server_support as sup, solver_monitor
from jmag_bridge.worker import ops

from test_server_contract import Fake, F_E, _facts

PROJECT = "C:/work/motor.jproj"
OWN = "C:/work/motor.jfiles/M~1/Load~1/Case1"
OTHER = "D:/elsewhere/other.jfiles/M~1/S~1/Case1"


@pytest.fixture
def fake(monkeypatch):
    fb = Fake()
    orig = fb.call

    async def call(op, timeout=600, **a):
        if op == "app_info":
            return {"ok": True, "result": {"jmag_pid": 100, "project_path": PROJECT, "models": []}}
        return await orig(op, timeout, **a)

    fb.call = call
    monkeypatch.setattr(server, "B", fb)
    monkeypatch.setattr(server, "VERIFY", sup.VerifyRecord())

    async def no_sleep(_):
        return None

    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    return fb


def _ui(monkeypatch, windows, folders, alive=True):
    """Fake UI/process layer: windows = [(pid, title)], folders = calc folders of running solvers."""
    clicks, killed = [], []

    def dialogs(title_filter="", pid=None):
        return [{"pid": p, "kind": "progress" if "Run Analysis" in t else "modal", "title": t, "text": "",
                 "buttons": ["Abort"]} for p, t in windows
                if (pid is None or p == pid) and (not title_filter or title_filter in t)]

    def click(button, title_filter="", wait=0, pid=None):
        hit = dialogs(title_filter, pid)
        if hit:
            clicks.append((button, pid))
        return {"clicked": bool(hit)}

    monkeypatch.setattr(server.winui, "dialogs", dialogs)
    monkeypatch.setattr(server.winui, "click", click)
    monkeypatch.setattr(server.sup, "solver_status", lambda folder: {
        "solver_processes": [{"pid": 7}] * len(folders), "calc_folders": list(folders),
        "newest_monitor_age_s": 1.0, "stalled": False})
    monkeypatch.setattr(server.solver_monitor, "solver_processes",
                        lambda folder=None, **k: [{"pid": 7}] if alive else [])
    monkeypatch.setattr(server.solver_monitor, "kill_solver", lambda folder=None: killed.append(folder) or [7])
    return clicks, killed


# --------------------------------------------------------------------------- 1. cancel scoping

def test_cancel_after_own_run_finished_refuses(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(200, "Run Analysis")], [OTHER])
    fake.job = {"id": "r1", "study": "Load", "done": True, "started": time.time()}
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is False and r["error"]["message"] == "nothing is running"
    assert clicks == [] and killed == []          # another instance's solve is never touched


def test_cancel_unowned_considers_only_the_connected_pid(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(200, "Run Analysis")], [OTHER])
    r = asyncio.run(server.jmag_cancel_run())     # only pid 200 (not connected) is solving
    assert r["ok"] is False and "pid 100" in r["error"]["message"] and clicks == [] and killed == []

    clicks, killed = _ui(monkeypatch, [(200, "Run Analysis"), (100, "Run Analysis")], [OTHER, OWN])
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is True and {p for _, p in clicks} == {100}
    assert killed == [OWN]                         # the one folder of the connected project


def test_cancel_unowned_never_kills_a_foreign_folder(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(100, "Run Analysis")], [OTHER])
    r = asyncio.run(server.jmag_cancel_run())
    assert r["ok"] is True and killed == [] and any("not killed" in s for s in r["steps"])
    assert clicks and all(p == 100 for _, p in clicks)


def test_folder_in_project():
    assert sup.folder_in_project(OWN, PROJECT) and sup.folder_in_project(OWN.replace("/", "\\"), PROJECT.upper())
    assert not sup.folder_in_project(OTHER, PROJECT)
    assert not sup.folder_in_project("C:/work/motor.jfiles2/x", PROJECT)
    assert not sup.folder_in_project(OWN, None) and not sup.folder_in_project(None, PROJECT)


# --------------------------------------------------------------------------- 2. envelope invariant in a report

def test_report_reads_passed_inside_a_physics_envelope(tmp_path):
    env = {"ok": True, "result": {"check": "power_balance", "passed": False, "verdict": "FAIL", "rel_diff": 0.4},
           "seconds": 1.0}
    assert reporting._passed(env) is False
    assert reporting._passed({"check": "kvl", "ok": True}) is True             # pre-0.4.0 dict
    rows = [{"name": "S", "health": {"verdict": "PASS", "counts": {}, "findings": []}, "invariants": [env]}]
    res = reporting.build_report(str(tmp_path), "t", rows, include_images=False)
    md = open(res["path"], encoding="utf-8").read()
    assert "| power_balance | False |" in md and "S: invariant power_balance failed" in md


# --------------------------------------------------------------------------- 3 + 4. verification record

def test_run_cases_invalidates_the_record(fake, monkeypatch):
    server.VERIFY.mark_post("Load", None, "PASS")
    server.VERIFY.mark_physics("Load", None, {"check": "kvl", "passed": True})

    class Runner:
        def __init__(self, *a):
            pass

        def start(self):
            pass

        def call(self, op, timeout, **a):
            return {"ok": True, "result": {}}

        def stop(self):
            pass

    monkeypatch.setattr(server, "Worker", Runner)
    fake.install = object()
    fake.need_install = lambda: None
    fake._journal = lambda *a: None
    asyncio.run(server.jmag_run_cases("Load", wait_seconds=1))
    assert server.VERIFY.unverified("Load") == ["post-check", "physics-check"]


def test_record_is_per_model():
    v = sup.VerifyRecord()
    v.mark_post("Load", "M1", "PASS")
    v.mark_physics("Load", "M1", {"check": "kvl", "passed": True})
    assert v.unverified("Load", "M1") == [] and v.unverified("Load", "M2") == ["post-check", "physics-check"]
    v.mark_post("Load", "M2", "PASS")
    v.invalidate("Load", "M2")
    assert v.unverified("Load", "M1") == [] and v.post("Load", "M2") is None
    v.mark_post("Load", None, "PASS")            # recorded for the current model
    v.invalidate("Load", "M1")                    # a named model also drops the "" entry
    assert v.post("Load") is None and v.unverified("Load", "M1") == ["post-check", "physics-check"]
    v.mark_post("Load", "M1", "PASS")
    v.invalidate("Load")                          # model unknown: every model
    assert v.post("Load", "M1") is None


def test_tools_pass_the_model_to_the_record(fake):
    asyncio.run(server.jmag_check("Load", "post", model="M1"))
    assert server.VERIFY.post("Load", "M1") is not None and server.VERIFY.post("Load", "M2") is None
    r = asyncio.run(server.jmag_get_result("Load", "Torque", model="M2"))
    assert r["result"]["unverified"] == ["post-check", "physics-check"]
    r = asyncio.run(server.jmag_get_result("Load", "Torque", model="M1"))
    assert r["result"]["unverified"] == ["physics-check"]


# --------------------------------------------------------------------------- 5. DXF extrusion and units

def _dxf(*entities, insunits=None):
    head = ["0", "SECTION", "2", "HEADER", "9", "$INSUNITS", "70", str(insunits), "0", "ENDSEC"] if insunits else []
    body = ["0", "SECTION", "2", "ENTITIES"] + [x for e in entities for x in e] + ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(head + body) + "\n"


def test_mirrored_arc_closes_the_half_disc(tmp_path):
    arc = ["0", "ARC", "8", "0", "10", "5.0", "20", "0.0", "30", "0.0", "40", "5.0", "50", "0.0", "51", "180.0",
           "210", "0.0", "220", "0.0", "230", "-1.0"]
    line = ["0", "LINE", "8", "0", "10", "0.0", "20", "0.0", "11", "-10.0", "21", "0.0"]
    p = tmp_path / "half.dxf"
    p.write_text(_dxf(arc, line, insunits=4))
    r = dxf_inspect.inspect(str(p))
    assert r["region_count"] == 1 and r["units"] == "mm" and r["labels_draft"]["units"] == "mm"
    assert r["regions"][0]["area_mm2"] == pytest.approx(math.pi * 25 / 2, rel=1e-4)      # 39.27 mm^2
    assert r["warnings"] == []


def test_mirrored_lwpolyline_and_circle_and_general_extrusion(tmp_path):
    # square 0..10 in OCS with extrusion -z lands at x -10..0; a circle mirrored; a tilted arc is skipped
    lw = ["0", "LWPOLYLINE", "8", "0", "90", "4", "70", "1", "10", "0", "20", "0", "10", "10", "20", "0",
          "10", "10", "20", "10", "10", "0", "20", "10", "230", "-1"]
    circ = ["0", "CIRCLE", "8", "0", "10", "20", "20", "5", "40", "2", "230", "-1"]
    tilted = ["0", "ARC", "8", "0", "10", "0", "20", "0", "40", "1", "50", "0", "51", "90", "210", "1", "230", "0"]
    p = tmp_path / "m.dxf"
    p.write_text(_dxf(lw, circ, tilted, insunits=1))
    r = dxf_inspect.inspect(str(p))
    xs = sorted(round(x["centroid"][0], 6) for x in r["regions"])
    assert xs == [-20.0, -5.0] and r["units"] == "in"
    assert r["diagnostics"]["skipped_entities"] == {"ARC (non-Z extrusion)": 1}
    assert any("inches" in w for w in r["warnings"])
    p.write_text(_dxf(lw, insunits=6))
    assert dxf_inspect.inspect(str(p))["units"] == "unknown"


# --------------------------------------------------------------------------- 6. solver command lines

def test_folder_from_command_rejects_relative_fragments():
    f = solver_monitor.folder_from_command
    assert f("ExecSolver.exe D:\\my calc\\Designer.jcf") == str(solver_monitor.Path("D:\\my calc"))
    assert f("ExecSolver.exe my calc\\Designer.jcf") is None              # tail of an unquoted path: relative
    assert f('C:\\Program Files\\JMAG\\ExecSolver.exe "C:\\a b\\Case1\\Designer.jcf"') == \
        str(solver_monitor.Path("C:\\a b\\Case1"))
    assert f("C:\\Program Files\\JMAG\\ExecSolver.exe C:\\a b\\Case1\\Designer.jcf -n 4") == \
        str(solver_monitor.Path("C:\\a b\\Case1"))
    assert f("ExecSolver.exe \\\\srv\\share\\Case1\\Designer.jcf") == str(solver_monitor.Path("\\\\srv\\share\\Case1"))


def test_process_filter_is_a_path_prefix_not_a_substring():
    out = ("1\tExecSolver.exe\tExecSolver.exe C:\\w\\calc\\Designer.jcf\n"
           "2\tExecSolver.exe\tExecSolver.exe C:\\w\\calc2\\Designer.jcf\n"
           "3\tExecSolver.exe\tExecSolver.exe calc\\Designer.jcf\n"
           "4\tjam.exe\tjam.exe C:\\w\\calc\\x\n")
    assert [r["pid"] for r in solver_monitor.parse_process_lines(out, "C:/w/calc")] == [1]
    assert [r["pid"] for r in solver_monitor.parse_process_lines(out, "calc")] == []
    assert len(solver_monitor.parse_process_lines(out)) == 4


# --------------------------------------------------------------------------- 7. THD independent of n_harmonics

def test_thd_does_not_depend_on_n_harmonics(fake):
    thd = {}
    for n in (10, 64):
        r = asyncio.run(server.jmag_get_result("Load", "Circuit Current", electrical_frequency_hz=F_E, n_harmonics=n))
        thd[n] = r["result"]["stats"]["CoilU"]["last_period"]["thd_pct"]
    assert thd[10] == thd[64] and thd[10] is not None


# --------------------------------------------------------------------------- 8. default hints

def test_generic_worker_exception_has_a_hint():
    def boom(ctx):
        raise ZeroDivisionError("x")

    ops.OPS["_boom_test"] = boom
    try:
        r = ops.Context(None).dispatch("_boom_test", {})
    finally:
        ops.OPS.pop("_boom_test")
    assert r["ok"] is False and r["error"]["hint"] == ops.DEFAULT_HINT


def test_worker_error_in_bridge_call_has_a_hint(monkeypatch):
    class W:
        def call(self, *a, **k):
            raise server.WorkerError("pipe closed")

    b = server.Bridge.__new__(server.Bridge)
    b.install, b.main, b.job = object(), W(), None
    r = asyncio.run(b.call("ping", 5))
    assert r["error"]["hint"] == server.WORKER_HINT and "jmag_doctor" in r["error"]["hint"]


def test_experiment_ok_helper_never_prints_hint_none():
    with pytest.raises(RuntimeError) as e:
        experiments._ok({"ok": False, "error": {"message": "m"}}, "step")
    assert str(e.value) == "step: m"
    with pytest.raises(RuntimeError) as e:
        experiments._ok({"ok": False, "error": {"message": "m", "hint": "h"}}, "step")
    assert str(e.value) == "step: m | hint: h"


# --------------------------------------------------------------------------- 9. plausible pole equations

def _pmsm(equations, circuit=None):
    return {"type": "Transient2D", "equations": equations, "circuit": circuit or {"has_circuit": False},
            "conditions": [{"name": "Rot", "type": "RotationMotion",
                            "properties": [{"name": "AngularVelocity", "value": 1500}]}]}


def test_pole_equations_must_be_plausible():
    assert health.derive_electrical_frequency(_pmsm({"P": {"value": 1000}})) is None          # power, not p
    assert health.derive_electrical_frequency(_pmsm({"p": {"value": 4}}))["hz"] == 100.0
    assert health.derive_electrical_frequency(_pmsm({"poles": {"value": 7}})) is None         # odd
    assert health.derive_electrical_frequency(_pmsm({"poles": {"value": 8.5}})) is None
    assert health.derive_electrical_frequency(_pmsm({"poles": {"value": 200}})) is None
    assert health.derive_electrical_frequency(_pmsm({"poles": {"value": 8}}))["hz"] == 100.0
    # an implausible P falls through to the source frequency
    src = {"has_circuit": True, "components": [{"name": "S", "type": "3PhaseCurrentSource",
                                                 "properties": [{"name": "Frequency", "value": 50.0}]}]}
    d = health.derive_electrical_frequency(_pmsm({"P": {"value": 1000}}, src))
    assert d["hz"] == 50.0 and "Frequency" in d["source"]


# --------------------------------------------------------------------------- 10. f_e only for Transient in _post

def test_post_does_not_derive_fe_for_static(fake):
    orig = fake.call

    async def static(op, timeout=600, **a):
        env = await orig(op, timeout, **a)
        if op == "study_facts":
            env["result"]["type"] = "Static2D"
        return env

    fake.call = static
    r = asyncio.run(server.jmag_check("Load", "post"))
    assert "FE_DERIVED" not in {f["code"] for f in r["health"]["findings"]}
    fake.call = orig
    r = asyncio.run(server.jmag_check("Load", "post"))
    assert "FE_DERIVED" in {f["code"] for f in r["health"]["findings"]}
