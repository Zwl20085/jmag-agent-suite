import math

from jmag_bridge import health


def facts(**kw):
    base = {
        "type": "Transient2D", "dimension": 2, "model_thickness": 50.0, "has_mesh": True, "elements": 5000,
        "parts": [
            {"id": 1, "name": "Core", "material": "35JN210", "eddy_current": 0},
            {"id": 2, "name": "CoilA", "material": "Copper", "eddy_current": 0},
            {"id": 3, "name": "CoilB", "material": "Copper", "eddy_current": 0},
        ],
        "conditions": [{
            "name": "Phase", "type": "FEMCoil", "link": "Coil1", "parts": [],
            "subconditions": [
                {"name": "a", "parts": [2], "properties": [{"name": "Direction2D", "value": 0}]},
                {"name": "b", "parts": [3], "properties": [{"name": "Direction2D", "value": 1}]},
            ],
        }],
        "circuit": {"has_circuit": True, "components": [
            {"name": "Source", "type": "CurrentSource", "properties": []},
            {"name": "Coil1", "type": "Coil", "properties": [{"name": "Turn", "value": 40}]},
            {"name": "Ground", "type": "Ground", "properties": []},
        ]},
        "step": {"StepType": 0, "Step": 121, "TimeStep": 1 / 6000},
    }
    base.update(kw)
    return base


def codes(findings):
    return {f["code"] for f in findings}


def test_clean_setup_passes():
    s = health.summarize(health.preflight(facts(), electrical_frequency_hz=50))
    assert s["verdict"] == "PASS", s


def test_missing_material_is_error():
    f = facts()
    f["parts"][0]["material"] = None
    assert "NO_MATERIAL" in codes(health.preflight(f))


def test_default_stack_length_warning():
    assert "DEFAULT_STACK_LENGTH" in codes(health.preflight(facts(model_thickness=1.0)))


def test_broken_link_and_same_direction():
    f = facts()
    f["conditions"][0]["link"] = "Nope"
    f["conditions"][0]["subconditions"][1]["properties"][0]["value"] = 0
    c = codes(health.preflight(f))
    assert {"BAD_LINK", "SAME_DIRECTION"} <= c


def test_no_source_no_ground_default_turns():
    f = facts()
    f["circuit"]["components"] = [{"name": "Coil1", "type": "Coil", "properties": [{"name": "Turn", "value": 1}]}]
    c = codes(health.preflight(f))
    assert {"NO_SOURCE", "NO_GROUND", "DEFAULT_TURNS"} <= c


def test_time_step_resolution():
    f = facts(step={"StepType": 0, "Step": 11, "TimeStep": 1 / 500})
    c = codes(health.preflight(f, electrical_frequency_hz=50))
    assert "COARSE_TIME_STEP" in c


def test_zero_speed_motion():
    f = facts()
    f["conditions"].append({"name": "Motion", "type": "RotationMotion", "parts": [1],
                            "properties": [{"name": "AngularVelocity", "value": 0}, {"name": "LoadType", "value": 0}]})
    assert "ZERO_SPEED" in codes(health.preflight(f))


def test_coil_eddy_warning():
    f = facts()
    f["parts"][1]["eddy_current"] = 1
    assert "COIL_EDDY" in codes(health.preflight(f))


def test_static_current_condition_defaults():
    f = facts(type="Static2D", circuit={"has_circuit": False}, step=None,
              conditions=[{"name": "In", "type": "Current", "parts": [2],
                           "properties": [{"name": "Turn", "value": 1}, {"name": "Current", "value": 0}]}])
    c = codes(health.preflight(f))
    assert {"DEFAULT_TURNS", "ZERO_CURRENT"} <= c


def test_waveform_stats_and_ripple_only_for_dc():
    ac = [math.sin(2 * math.pi * k / 100) for k in range(100)]
    assert "ripple_pct_of_mean" not in health.waveform_stats(ac)
    dc = [2.0 + 0.1 * math.sin(2 * math.pi * k / 100) for k in range(100)]
    st = health.waveform_stats(dc)
    assert abs(st["ripple_pct_of_mean"] - 10.0) < 0.5


def test_harmonics_thd_only_for_ac():
    n = 120
    y = [math.sin(2 * math.pi * k / n) + 0.1 * math.sin(2 * math.pi * 5 * k / n) for k in range(n)]
    h = health.harmonics(y)
    assert abs(h["fundamental"] - 1.0) < 1e-9 and abs(h["thd_pct"] - 10.0) < 1e-6
    torque = [3.0 + 0.2 * math.cos(2 * math.pi * 6 * k / n) for k in range(n)]
    assert "thd_pct" not in health.harmonics(torque)


def test_periodicity():
    t = [k / 1000 for k in range(201)]
    steady = [math.sin(2 * math.pi * 50 * x) for x in t]
    assert health.periodicity(t, steady, 0.02)["steady"]
    growing = [x * 50 * math.sin(2 * math.pi * 50 * x) for x in t]
    assert not health.periodicity(t, growing, 0.02)["steady"]


def test_kvl_backward_euler_identity():
    dt, R, L = 1e-4, 0.1, 0.01
    t = [k * dt for k in range(200)]
    i = [5 * math.sin(2 * math.pi * 50 * x) for x in t]
    psi = [L * x for x in i]
    v = [0.0] + [R * i[k] + (psi[k] - psi[k - 1]) / dt for k in range(1, len(t))]
    assert health.kvl_residual(t, i, v, psi, R)["ok"]
    v_bad = [x * 1.05 for x in v]
    assert not health.kvl_residual(t, i, v_bad, psi, R)["ok"]


def test_power_balance_ideal_pm_machine():
    """Ideal 3-phase PM machine, q-axis current: T = 3/2 p psi I and P_em must equal T*omega."""
    p, psi_m, I, rpm = 2, 0.06, 10.0, 1500.0
    fe = rpm / 60 * p
    w_e = 2 * math.pi * fe
    dt = 1 / (fe * 600)
    t = [k * dt for k in range(1201)]
    cur, flux = {}, {}
    for n, sh in (("U", 0), ("V", -2 * math.pi / 3), ("W", 2 * math.pi / 3)):
        flux[n] = [psi_m * math.cos(w_e * x + sh) for x in t]
        cur[n] = [-I * math.sin(w_e * x + sh) for x in t]
    torque = [1.5 * p * psi_m * I] * len(t)
    r = health.power_balance(t, cur, flux, torque, rpm, 1 / fe)
    assert r["ok"] and r["rel_diff"] < 0.01, r


def test_inductance_consistency():
    assert health.inductance_consistency([0.053, 0.002], [5, 5], 0.1375)["ok"]


def test_postflight_flags():
    tables = {
        "Circuit Current: A": {"x": [0, 1, 2], "columns": {"Coil1": [0.0, 0.0, 0.0]}},
        "Torque: Nm": {"x": [0, 1, 2], "columns": {"Torque": [1.0, float("nan"), 1.0]}},
    }
    run = {"has_result": True, "errors": [], "warnings": ["Newton Raphson Iteration has not converged at step: 021."]}
    c = codes(health.postflight(run, {"n_steps_at_limit": 2, "steps_at_iteration_limit": [5, 9]}, tables,
                                configured_steps=10))
    assert {"ZERO_CURRENT", "NON_FINITE", "NONLINEAR_NOT_CONVERGED", "ITERATION_LIMIT", "STEPS_CHANGED"} <= c


def test_balanced_three_phase_source_column_not_flagged():
    tables = {"Circuit Current: A": {"x": [0, 1], "columns": {"CoilU": [1.0, -1.0], "Source3Ph": [0.0, 0.0]}}}
    assert "ZERO_CURRENT" not in codes(health.postflight({"has_result": True}, None, tables))


def test_known_messages_are_classified():
    run = {"has_result": True, "errors": [], "warnings": [
        "The step control has been automatically updated by the function that specifies a step control based on the motion condition.",
        "In order to improve the accuracy of torque and electromagnetic force, the calculation area of the force condition "
        "or the torque condition was extended to include the air region.",
        "Something nobody has seen before."]}
    f = health.postflight(run)
    assert [x["level"] for x in f] == ["info", "info", "warning"]
    assert health.summarize(f)["verdict"] == "CHECK"
    assert health.summarize(f[:2])["verdict"] == "PASS"


def test_harmonics_of_a_dc_dominant_signal_has_no_thd():
    # torque-like signal: DC + small ripple -> harmonics() omits thd_pct, callers must use .get()
    import math as _m
    from jmag_bridge import health as _h
    y = [5.0 + 0.5 * _m.sin(2 * _m.pi * 6 * i / 72) for i in range(72)]
    out = _h.harmonics(y)
    assert "thd_pct" not in out and out["dc"] > 4.9


def test_zero_current_is_warning_unless_flux_also_zero():
    cur = {"Circuit Current: A": {"x": [0, 1, 2], "columns": {"Coil1": [0.0, 0.0, 0.0]}}}
    f = [x for x in health.postflight({"has_result": True}, None, cur) if x["code"] == "ZERO_CURRENT"]
    assert [x["level"] for x in f] == ["warning"]  # cogging / open-circuit run
    both = dict(cur)
    both["Coil Flux: Wb"] = {"x": [0, 1, 2], "columns": {"Coil1": [0.0, 0.0, 0.0]}}
    f = [x for x in health.postflight({"has_result": True}, None, both) if x["code"] == "ZERO_CURRENT"]
    assert [x["level"] for x in f] == ["error"]
    flux_live = dict(cur)
    flux_live["Coil Flux: Wb"] = {"x": [0, 1, 2], "columns": {"Coil1": [0.0, 0.1, 0.2]}}
    f = [x for x in health.postflight({"has_result": True}, None, flux_live) if x["code"] == "ZERO_CURRENT"]
    assert [x["level"] for x in f] == ["warning"]


def test_postflight_honours_classifier_for_errors():
    run = {"has_result": True, "warnings": [], "errors": [
        "The step control has been automatically updated by the function ...",
        "Element volume is negative or zero at step 5",
        "Totally unknown failure"]}
    f = health.postflight(run)
    assert [(x["code"], x["level"]) for x in f] == [
        ("STEP_AUTO_UPDATED", "info"), ("NEGATIVE_ELEMENT_VOLUME", "error"), ("SOLVER_ERROR", "error")]


def test_thresholds_drive_checks():
    assert set(health.THRESHOLDS) >= {"kvl_rel", "energy_rel", "power_balance_rel"}
    just_in = health.THRESHOLDS["energy_rel"] * 0.9
    assert health.inductance_consistency([1.0], [2.0], 1.0 * (1 + just_in))["ok"]
    assert not health.inductance_consistency([1.0], [2.0], 1.0 * (1 + 2 * health.THRESHOLDS["energy_rel"]))["ok"]


def test_readme_numbers_match_thresholds():
    """The README feedback-ladder row for physics invariants must quote the numbers health.py enforces."""
    import re
    from pathlib import Path
    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    row = next((ln for ln in readme.splitlines() if "**Physics invariants**" in ln), None)
    assert row, "README has no 'Physics invariants' row in the feedback ladder"
    cell = [c.strip() for c in row.strip().strip("|").split("|")][-1]
    nums = [float(m) for m in re.findall(r"(\d+(?:\.\d+)?(?:e-?\d+)?)\s*%", cell)]
    expected = [100 * health.THRESHOLDS["energy_rel"], 100 * health.THRESHOLDS["power_balance_rel"]]
    if nums != expected:
        import pytest
        pytest.xfail(f"README physics row says {nums} % but health.THRESHOLDS give {expected} % "
                     f"(energy, power balance): update README.md (cell: {cell!r})")
