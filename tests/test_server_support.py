"""Pure helpers of server_support (no JMAG): envelopes, verification record, verdicts, physics-check defaults,
solver status without a bridge job, dq <-> phase kinds."""
import math

import pytest

from jmag_bridge import experiments, server_support as sup, solver_monitor


def test_wrap_always_has_ok_and_seconds():
    assert sup.wrap({"a": 1}) == {"ok": True, "result": {"a": 1}, "seconds": 0.0}
    e = sup.wrap(ok=False, error={"message": "m", "hint": "h"}, t0=0.0)
    assert e["ok"] is False and "result" not in e and e["error"]["hint"] == "h" and e["seconds"] > 0


def test_verify_record_lifecycle():
    v = sup.VerifyRecord()
    assert v.unverified("S") == ["post-check", "physics-check"]
    v.mark_post("S", "PASS")
    assert v.unverified("S") == ["physics-check"]
    v.mark_physics("S", {"check": "kvl", "passed": True, "verdict": "PASS"})
    assert v.unverified("S") == [] and v.physics("S")[0]["check"] == "kvl"
    assert v.unverified("S", case=2) == ["post-check", "physics-check"]   # other design-table case: never covered
    assert v.unverified("Other") == ["post-check", "physics-check"]
    v.invalidate("S")                       # re-solved: old checks no longer describe the results
    assert v.unverified("S") == ["post-check", "physics-check"]
    v.mark_post("S", "PASS")
    v.clear()                               # project changed
    assert v.post("S") is None


def test_check_verdict_pass_check_fail():
    f_warn = {"level": "warning", "code": "NOT_STEADY", "message": "m", "fix": "simulate more periods"}
    f_err = {"level": "error", "code": "NO_MATERIAL", "message": "parts without material", "fix": "jmag_set_material"}
    assert sup.check_verdict({"verdict": "PASS", "counts": {}, "findings": []}) == {
        "passed": True, "verdict": "PASS", "counts": {}}
    c = sup.check_verdict({"verdict": "CHECK", "counts": {}, "findings": [f_warn]})
    assert c["passed"] is True and "NOT_STEADY: simulate more periods" in c["hint"]
    f = sup.check_verdict({"verdict": "FAIL", "counts": {}, "findings": [f_err, f_warn]})
    assert f["passed"] is False and "NO_MATERIAL: jmag_set_material" in f["hint"] and "NOT_STEADY" not in f["hint"]


def test_invariant_verdict_splits_ok_from_passed():
    good = sup.invariant_verdict("kvl", {"max_rel_residual": 1e-5, "ok": True})
    assert good == {"max_rel_residual": 1e-5, "check": "kvl", "passed": True, "verdict": "PASS"}
    bad = sup.invariant_verdict("power_balance", {"rel_diff": 0.4, "ok": False})
    assert bad["passed"] is False and bad["verdict"] == "FAIL" and "ok" not in bad
    assert "speed_rpm" in bad["hint"] and "NOT_STEADY" in bad["hint"]
    short = sup.invariant_verdict("power_balance", {"ok": False, "reason": "need at least one electrical period"})
    assert short["hint"].startswith("need at least")


PMSM_FACTS = {
    "type": "Transient2D",
    "conditions": [{"name": "Rot", "type": "RotationMotion", "parts": [1],
                    "properties": [{"name": "AngularVelocity", "value": 1500}]}],
    "circuit": {"has_circuit": True, "components": [
        {"name": "Source3Ph", "type": "3PhaseCurrentSource", "properties": [{"name": "Frequency", "value": 100.0}]},
        {"name": "CoilU", "type": "Coil", "properties": [{"name": "Resistance", "value": 0.05}]},
        {"name": "CoilV", "type": "Coil", "properties": []},
        {"name": "Ground", "type": "Ground", "properties": []}]},
}


def test_physics_defaults_power_balance_from_facts():
    vals, used = sup.physics_defaults(PMSM_FACTS, "power_balance", None, None, None, None, None)
    assert vals["speed_rpm"] == 1500 and vals["electrical_frequency_hz"] == 100.0
    assert "RotationMotion" in used["speed_rpm"] and "Source3Ph" in used["electrical_frequency_hz"]
    vals, used = sup.physics_defaults(PMSM_FACTS, "power_balance", None, None, None, 3000, 200.0)
    assert vals["speed_rpm"] == 3000 and vals["electrical_frequency_hz"] == 200.0 and used == {}  # explicit wins


def test_physics_defaults_kvl_single_coil():
    facts = {"circuit": {"has_circuit": True, "components": [
        {"name": "Coil1", "type": "Coil", "properties": [{"name": "Resistance", "value": 0.2}]},
        {"name": "VP_Coil1", "type": "VoltageProbe", "properties": []}]}}
    vals, used = sup.physics_defaults(facts, "kvl", None, None, None, None, None)
    assert (vals["coil"], vals["probe"], vals["resistance"]) == ("Coil1", "VP_Coil1", 0.2) and len(used) == 3
    vals, _ = sup.physics_defaults(PMSM_FACTS, "kvl", None, None, None, None, None)
    assert vals["coil"] is None            # two coils: no guess


def test_solver_status_without_job():
    procs = [{"pid": 11, "name": "ExecSolver.exe", "command": 'ExecSolver.exe "C:/p.jfiles/M~1/S~1/Case1/Designer.jcf"'},
             {"pid": 12, "name": "jam.exe", "command": "jam.exe -x"}]
    seen = []

    def progress(folder):
        seen.append(folder)
        return {"steps_done": 3, "monitor_found": True, "last_write_age_s": 900.0}

    st = sup.solver_status(None, list_procs=lambda folder, names: procs, progress=progress)
    assert [p["pid"] for p in st["solver_processes"]] == [11, 12]
    assert st["newest_monitor_age_s"] == 900.0 and st["stalled"] is True
    assert seen == [str(solver_monitor.Path("C:/p.jfiles/M~1/S~1/Case1"))]
    idle = sup.solver_status(None, list_procs=lambda folder, names: [], progress=progress)
    assert idle["stalled"] is False and idle["solver_processes"] == [] and idle["newest_monitor_age_s"] is None
    fresh = sup.solver_status("job_folder", list_procs=lambda folder, names: procs[1:],
                              progress=lambda f: {"last_write_age_s": 5.0})
    assert fresh["stalled"] is False and fresh["calc_folders"] == ["job_folder"]


def test_process_lines_and_folder_from_command():
    out = "11\tExecSolver.exe\tExecSolver.exe D:\\w\\a.jfiles\\Case1\\Designer.jcf\nbad line\n12\tjam.exe\tjam.exe\n"
    rows = solver_monitor.parse_process_lines(out)
    assert [(r["pid"], r["name"]) for r in rows] == [(11, "ExecSolver.exe"), (12, "jam.exe")]
    assert solver_monitor.parse_process_lines(out, "d:/w/a.jfiles/case1")[0]["pid"] == 11
    assert solver_monitor.parse_process_lines("7\tExecSolver.exe x")[0]["name"] == "ExecSolver.exe"  # old 2-column rows
    assert solver_monitor.folder_from_command('a.exe "C:/x y/Case1/Designer.jcf"') == str(solver_monitor.Path("C:/x y/Case1"))
    assert solver_monitor.folder_from_command("jam.exe") is None


@pytest.mark.parametrize("i_d,i_q,theta", [(-20.0, 50.0, 0.0), (10.0, 0.0, 37.0), (0.0, 30.0, 200.0)])
def test_dq_to_phase_round_trip(i_d, i_q, theta):
    r = sup.dq_estimate("dq_to_phase", {"id": i_d, "iq": i_q, "theta_e_deg": theta})
    ph = r["details"]["phase_currents_A"]
    assert abs(ph["U"] + ph["V"] + ph["W"]) < 1e-9
    back = sup.dq_estimate("phase_to_dq", {"abc": [ph["U"], ph["V"], ph["W"]], "theta_e_deg": theta})
    assert abs(back["details"]["d"] - i_d) < 1e-9 and abs(back["details"]["q"] - i_q) < 1e-9
    src = r["details"]["circuit_source"]
    # documented source convention: i_U(t=0) = amplitude * sin(phase)
    assert abs(src["amplitude"] * math.sin(math.radians(src["phase"])) - ph["U"]) < 1e-6
    assert r["value"] == pytest.approx(math.hypot(i_d, i_q))


def test_dq_matches_experiment_helpers():
    a, b, c = experiments.dq_currents(10.0, 30.0, "q")
    d = sup.dq_estimate("phase_to_dq", {"abc": [a, b, c], "theta_e_deg": 30.0})["details"]
    assert abs(d["d"]) < 1e-9 and abs(d["q"] - 10.0) < 1e-9


@pytest.mark.parametrize("kind,params,msg", [
    ("dq_to_phase", {"id": 1, "iq": 2}, "missing"),
    ("dq_to_phase", {"id": 1, "iq": 2, "theta_e_deg": 0, "x": 1}, "unknown"),
    ("phase_to_dq", {"abc": [1, 2], "theta_e_deg": 0}, "three phase values"),
    ("dq_to_phase", {"id": "a", "iq": 2, "theta_e_deg": 0}, "finite number"),
])
def test_dq_bad_params(kind, params, msg):
    with pytest.raises(ValueError, match=msg):
        sup.dq_estimate(kind, params)
