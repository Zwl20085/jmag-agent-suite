"""Metric math of jmag_experiment on synthetic waveforms (no JMAG)."""
import asyncio
import math

import pytest

from jmag_bridge import experiments as ex


def test_cogging_period_and_step_plan():
    assert ex.cogging_period_deg(12, 4) == pytest.approx(30.0)
    assert ex.cogging_period_deg(12, 10) == pytest.approx(6.0)
    p = ex.step_plan_for_span(60.0, 30.0, 90)
    assert p["steps"] == 91 and p["end_time"] == pytest.approx(30 / 360)  # 60 rpm = 360 deg/s
    assert p["time_step"] * 90 == pytest.approx(p["end_time"])


def test_cogging_metrics_dominant_order():
    rpm, span = 60.0, 30.0                      # 12s4p: 12 cogging periods per revolution
    n = 90
    dt = span / (6 * rpm) / n
    t = [k * dt for k in range(n + 1)]
    tq = [0.2 * math.sin(2 * math.pi * k / n) + 0.05 * math.sin(4 * math.pi * k / n) for k in range(n + 1)]
    m = ex.cogging_metrics(t, tq, rpm, span)
    assert m["dominant_order_per_rev"] == pytest.approx(12.0)
    assert m["dominant_amplitude_Nm"] == pytest.approx(0.2, rel=1e-6)
    assert m["pk_pk_Nm"] == pytest.approx(max(tq[1:]) - min(tq[1:]), rel=1e-6)
    assert abs(m["mean_Nm"]) < 1e-9


def test_back_emf_from_flux_linkage():
    f, n = 50.0, 120
    dt = 1 / f / n
    t = [k * dt for k in range(n + 1)]
    psi1, psi5 = 0.1, 0.004
    fluxes = {}
    for name, sh in (("CoilU", 0.0), ("CoilV", -120.0), ("CoilW", 120.0)):
        a = math.radians(sh)
        fluxes[name] = [psi1 * math.cos(2 * math.pi * f * x + a) + psi5 * math.cos(5 * (2 * math.pi * f * x + a))
                        for x in t]
    w = 2 * math.pi * f
    volts = {"CoilU": [-w * psi1 * math.sin(2 * math.pi * f * x) for x in t]}
    m = ex.back_emf_metrics(t, fluxes, f, volts)
    u = m["phases"]["CoilU"]
    assert u["E1_peak_V"] == pytest.approx(w * psi1, rel=1e-6)
    assert u["E1_rms_V"] == pytest.approx(w * psi1 / math.sqrt(2), rel=1e-6)
    assert u["thd_pct"] == pytest.approx(5 * psi5 / psi1 * 100, rel=1e-4)    # E5 = 5 w psi5
    assert u["E_equals_omega_psi_rel_diff"] < 1e-6
    assert m["phase_imbalance_pct"] < 1e-6
    assert m["phase_shift_deg"]["CoilU->CoilV"] == pytest.approx(120.0, abs=1e-6)


def test_load_point_ripple():
    f, n = 50.0, 60
    dt = 1 / f / n
    t = [k * dt for k in range(2 * n + 1)]
    tq = [10 + 0.5 * math.cos(6 * 2 * math.pi * f * x) for x in t]
    m = ex.load_point_metrics(t, tq, f)
    assert m["mean_Nm"] == pytest.approx(10.0, rel=1e-9)
    assert m["pk_pk_Nm"] == pytest.approx(1.0, rel=1e-3)
    assert m["ripple_pct"] == pytest.approx(10.0, rel=1e-3)


@pytest.mark.parametrize("theta", [0.0, 37.0, 200.0])
def test_dq_currents_and_park_roundtrip(theta):
    for axis, want in (("d", (5.0, 0.0)), ("q", (0.0, 5.0))):
        i = ex.dq_currents(5.0, theta, axis)
        assert sum(i) == pytest.approx(0.0, abs=1e-12)
        d, q = ex.park(i, theta)
        assert (d, q) == pytest.approx(want, abs=1e-12)


def test_ld_lq_recovers_synthetic_machine():
    Ld, Lq, psi_pm, theta, I = 2e-3, 5e-3, 0.08, 63.0, 10.0

    def psi_abc(id_, iq):
        d, q = psi_pm + Ld * id_, Lq * iq           # inverse Park of (psi_d, psi_q)
        th = math.radians(theta)
        return [d * math.cos(th - s) - q * math.sin(th - s) for s in (0.0, 2 * math.pi / 3, -2 * math.pi / 3)]

    r = ex.ld_lq(psi_abc(0, 0), psi_abc(I, 0), psi_abc(0, I), I, theta)
    assert r["Ld_H"] == pytest.approx(Ld, rel=1e-9)
    assert r["Lq_H"] == pytest.approx(Lq, rel=1e-9)
    assert r["psi_pm_d_Wb"] == pytest.approx(psi_pm, rel=1e-9)
    assert r["saliency_Lq_over_Ld"] == pytest.approx(2.5, rel=1e-9)


def test_run_experiment_validates_before_touching_jmag():
    calls = []

    async def call(op, timeout, **a):
        calls.append(op)
        return {"ok": True, "result": {}}

    async def solve(*a, **k):
        return {}

    for kind, params, action in (("nope", {}, "run"), ("load_point", {"poles": 4}, "run"),
                                 ("cogging", {"poles": 4}, "run"), ("cogging", {"poles": 4}, "analyze"),
                                 ("inductance_static", {"current_peak": 5}, "run"),
                                 ("inductance_static", {"conditions": ["A"]}, "run")):
        with pytest.raises(ValueError):
            asyncio.run(ex.run_experiment(kind, "S", params, call, solve, action=action))
    assert calls == []          # nothing was duplicated or changed in JMAG


def test_run_experiment_cogging_flow_with_fake_jmag():
    """Orchestration order and arguments: duplicate -> setup (zero current, one cogging period) -> solve -> analyze."""
    rpm, span, n = 60.0, 30.0, 90
    dt = span / (6 * rpm) / n
    t = [k * dt for k in range(n + 1)]
    tq = [0.3 * math.sin(2 * math.pi * k / n) for k in range(n + 1)]
    seen = {}

    async def call(op, timeout, **a):
        seen.setdefault(op, []).append(a)
        if op == "list_results":
            return {"ok": True, "result": {"tables": [{"index": 0, "name": "Torque", "title": "Torque:N*m"}]}}
        if op == "get_result":
            return {"ok": True, "result": {"x": t, "columns": {"Torque": tq}}}
        return {"ok": True, "result": {"op": op}}

    async def solve(study, wait, f_e):
        seen["solve"] = (study, f_e)
        return {"state": "finished", "post": {"health": {"verdict": "PASS"}}}

    out = asyncio.run(ex.run_experiment("cogging", "Load", {"slots": 12, "poles": 4}, call, solve))
    assert seen["duplicate_study"][0] == {"study": "Load", "new_name": "Load_cogging", "model": None}
    setup = seen["experiment_setup"][0]
    assert setup["zero_current"] is True and setup["steps"] == 91 and setup["rpm"] == 60.0
    assert setup["time_step"] * 90 == pytest.approx(30 / 360)
    assert seen["solve"][0] == "Load_cogging"
    assert out["metrics"]["dominant_order_per_rev"] == pytest.approx(12.0)
    assert out["metrics"]["pk_pk_Nm"] == pytest.approx(0.6, rel=1e-3)
    assert out["run_health"] == "PASS"


def test_cogging_several_periods_keeps_samples_per_period():
    seen = {}

    async def call(op, timeout, **a):
        seen.setdefault(op, []).append(a)
        return {"ok": True, "result": {}}

    async def solve(study, wait, f_e):
        return {"state": "running"}

    out = asyncio.run(ex.run_experiment("cogging", "Load", {"slots": 12, "poles": 4, "periods": 3,
                                                            "steps_per_span": 90}, call, solve))
    setup = seen["experiment_setup"][0]
    assert setup["steps"] == 3 * 90 + 1
    assert setup["time_step"] == pytest.approx(30 / 360 / 90)       # same sampling as one period
    assert out["plan"]["steps_per_span"] == 90 and out["plan"]["span_mech_deg"] == pytest.approx(90.0)


def test_analyze_picks_table_names_from_list_results():
    """Never asks get_result for a name the study does not list (that would be a JMAG-logged probe)."""
    asked = []
    t = [k * 1e-4 for k in range(101)]

    async def call(op, timeout, **a):
        if op == "list_results":
            return {"ok": True, "result": {"tables": [{"index": 0, "name": "Coil Flux-Linkage", "title": "Coil Flux-Linkage:Wb"}]}}
        if op == "get_result":
            asked.append(a["table"])
            return {"ok": True, "result": {"x": t, "columns": {"CoilU": [math.cos(2 * math.pi * 100 * x) for x in t]}}}
        raise AssertionError(op)

    out = asyncio.run(ex.analyze(call, "back_emf", "S", {"poles": 4, "rpm": 3000}))
    assert asked == ["Coil Flux-Linkage"]          # no FEMCoilFlux / TerminalVoltage / Circuit Voltage probes
    assert out["metrics"]["phases"]["CoilU"]["psi1_peak_Wb"] == pytest.approx(1.0, rel=1e-3)


def _inductance_fake(fail_on_run=None):
    log = []
    runs = [0]

    async def call(op, timeout, **a):
        log.append((op, a))
        if op == "list_conditions":
            return {"ok": True, "result": {"conditions": [{"name": "A", "type": "Current"}, {"name": "B", "type": "Current"}]}}
        if op == "set_properties":
            return {"ok": True, "result": {}}
        if op == "run":
            runs[0] += 1
            if runs[0] == fail_on_run:
                return {"ok": False, "error": {"message": "solver failed"}}
            return {"ok": True, "result": {}}
        if op == "get_result":
            cur = {}
            for o, x in log:
                if o == "set_properties":
                    cur[x["target"]["name"]] = x["values"]["Current"]
            return {"ok": True, "result": {"columns": {c: [0.01 + 1e-3 * i] for c, i in cur.items()}}}
        raise AssertionError(op)

    return call, log


def _currents_after(log):
    cur = {}
    for o, x in log:
        if o == "set_properties":
            cur[x["target"]["name"]] = x["values"]["Current"]
    return cur


def test_inductance_resets_currents_without_extra_solve_and_passes_model():
    call, log = _inductance_fake()
    out = asyncio.run(ex.run_experiment("inductance_static", "St", {"current_peak": 10, "conditions": ["A"]},
                                        call, None, model="M1"))
    assert out["L_H"] == pytest.approx(1e-3)
    assert [o for o, _ in log].count("run") == 2                     # psi(0), psi(I) - no reset solve
    assert _currents_after(log) == {"A": 0.0, "B": 0.0}
    assert all(x["target"].get("model") == "M1" for o, x in log if o == "set_properties")


def test_inductance_resets_currents_when_a_solve_fails():
    call, log = _inductance_fake(fail_on_run=2)
    with pytest.raises(RuntimeError, match="solver failed"):
        asyncio.run(ex.run_experiment("inductance_static", "St", {"current_peak": 10, "conditions": ["A"]}, call, None))
    assert _currents_after(log) == {"A": 0.0, "B": 0.0}
    assert all("model" not in x["target"] for o, x in log if o == "set_properties")
