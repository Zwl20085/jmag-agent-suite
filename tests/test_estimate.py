import math

import pytest

from jmag_bridge import estimate

KEYS = {"kind", "value", "unit", "formula", "assumptions", "expected_fea_tolerance_pct", "details"}


def test_every_kind_returns_the_contract():
    examples = {
        "carter_factor": {"slot_opening_mm": 2.5, "slot_pitch_mm": 12, "airgap_mm": 1},
        "airgap_flux_density": {"Br": 1.2, "magnet_mm": 4, "airgap_mm": 1},
        "winding_factor": {"slots": 12, "poles": 10},
        "emf_constant": {"turns_per_phase": 80, "kw1": 0.933, "Bg": 0.9, "bore_mm": 60, "stack_mm": 50, "poles": 10},
        "torque_constant": {"turns_per_phase": 80, "kw1": 0.933, "Bg": 0.9, "bore_mm": 60, "stack_mm": 50, "poles": 10},
        "torque_dq": {"poles": 8, "psi_m": 0.05, "iq": 50},
        "magnetizing_inductance": {"turns_per_phase": 80, "kw1": 0.933, "bore_mm": 60, "stack_mm": 50, "poles": 10,
                                   "g_eff_mm": 4.8},
        "dq_inductance": {"turns_per_phase": 80, "kw1": 0.933, "bore_mm": 60, "stack_mm": 50, "poles": 10, "g_eff_mm": 4.8},
        "gapped_core_inductance": {"turns": 100, "core_area_mm2": 750, "gap_mm": 1},
        "tooth_yoke_flux_density": {"Bg": 0.9, "bore_mm": 60, "slots": 12, "poles": 10, "tooth_width_mm": 8,
                                    "yoke_height_mm": 6},
        "slot_fill": {"slot_area_mm2": 80, "conductors_per_slot": 40, "wire_diameter_mm": 0.9},
        "copper_loss": {"current_rms": 10, "turns_per_phase": 80, "conductor_area_mm2": 0.64, "stack_mm": 50,
                        "end_winding_mm": 20},
        "electrical_frequency": {"rpm": 3000, "poles": 10},
        "step_plan": {"rpm": 3000, "poles": 10},
        "iron_loss_steinmetz": {"kh": 0.025, "ke": 4e-5, "B": 1.5, "f": 250, "mass_kg": 2.0},
    }
    assert set(examples) == set(estimate.KINDS)
    for kind, params in examples.items():
        r = estimate.run(kind, params)
        assert KEYS <= set(r), kind
        assert r["formula"] and isinstance(r["assumptions"], list)


def test_self_test_c_core_matches_selftest_analytic():
    r = estimate.gapped_core_inductance(100, 750, 1.0)
    assert r["value"] == pytest.approx(9.42e-3, rel=2e-3)
    fr = estimate.gapped_core_inductance(100, 750, 1.0, core_dims_mm=[15, 50])
    assert fr["value"] > r["value"]  # fringing increases L
    b = estimate.gapped_core_inductance(100, 750, 1.0, iron_path_mm=180, mu_r_iron=2000, current=5)
    assert b["details"]["B_core_T"] == pytest.approx(4e-7 * math.pi * 500 / (1e-3 + 0.18 / 2000), rel=1e-6)


def test_airgap_flux_density_and_carter():
    r = estimate.airgap_flux_density(1.2, 4, 1, mu_r=1.0, leakage=1.0)
    assert r["value"] == pytest.approx(1.2 * 4 / 5)
    kc = estimate.carter_factor(2.5, 12, 1)["value"]
    assert 1.0 < kc < 1.2
    x = 2.5 / 2.0                                   # b0 / (2 g)
    gamma = 4 / math.pi * (x * math.atan(x) - math.log(math.sqrt(1 + x * x)))
    assert kc == pytest.approx(12.0 / (12.0 - gamma * 1.0), rel=1e-12)
    assert kc == pytest.approx(1.07402, abs=1e-5)
    bg = estimate.airgap_flux_density(1.2, 4, 1, carter=kc, mu_r=1.0, leakage=1.0)["value"]
    assert bg == pytest.approx(1.2 * 4 / (4 + kc * 1.0), rel=1e-12)   # Bg = Br hm / (hm + mu_r kc g)
    assert bg == pytest.approx(0.94599, abs=1e-5) and bg < r["value"]


def test_emf_torque_constants_consistent():
    p = {"turns_per_phase": 80, "kw1": 0.933, "Bg": 0.9, "bore_mm": 60, "stack_mm": 50, "poles": 10}
    e = estimate.emf_constant(**p)
    t = estimate.torque_constant(**p)
    assert t["value"] == pytest.approx(1.5 * e["value"])
    psi = e["details"]["psi_m_Wb"]
    tau = math.pi * 0.06 / 10
    assert psi == pytest.approx(80 * 0.933 * (2 / math.pi) * (4 / math.pi) * 0.9 * tau * 0.05)


def test_torque_dq_reluctance_sign():
    r = estimate.torque_dq(8, 0.05, 50, id=-20, Ld=2e-4, Lq=4e-4)
    assert r["details"]["reluctance_torque_Nm"] > 0
    assert r["value"] == pytest.approx(1.5 * 4 * (0.05 * 50 + (2e-4 - 4e-4) * -20 * 50))
    with pytest.raises(ValueError, match="both Ld and Lq"):
        estimate.torque_dq(8, 0.05, 50, Ld=1e-4)


def test_magnetizing_and_dq_inductance():
    lm = estimate.magnetizing_inductance(80, 0.933, 60, 50, 10, 4.8)["value"]
    assert lm == pytest.approx(3 / math.pi * 4e-7 * math.pi * (80 * 0.933) ** 2 * 0.06 * 0.05 / (25 * 4.8e-3))
    dq = estimate.dq_inductance(80, 0.933, 60, 50, 10, 4.8, saliency=2.0, leakage_fraction=0.0)["value"]
    assert dq["Ld"] == pytest.approx(lm) and dq["Lq"] == pytest.approx(2 * lm)


def test_copper_loss_and_temperature():
    cold = estimate.copper_loss(10, 80, 1.0, 50, end_winding_mm=25, temperature_C=20)
    hot = estimate.copper_loss(10, 80, 1.0, 50, end_winding_mm=25, temperature_C=120)
    r = 1.724e-8 * 80 * 0.15 / 1e-6
    assert cold["details"]["R_phase_ohm"] == pytest.approx(r)
    assert cold["value"] == pytest.approx(3 * 100 * r)
    assert hot["value"] / cold["value"] == pytest.approx(1 + 0.00393 * 100)
    est = estimate.copper_loss(10, 80, 1.0, 50, bore_mm=60, coil_pitch_slots=1, slots=12)
    assert est["details"]["end_turn_mm"] == pytest.approx(1.2 * math.pi * 60 / 12)
    with pytest.raises(ValueError, match="end_winding_mm"):
        estimate.copper_loss(10, 80, 1.0, 50)


def test_step_plan():
    r = estimate.step_plan(3000, 10, steps_per_period=60, periods=2, slots=12)
    d = r["details"]
    assert d["electrical_frequency_Hz"] == pytest.approx(250)
    assert r["value"] == pytest.approx(1 / 15000)
    assert d["total_steps"] == 121 and d["set_step_call"] == {"steps": 121, "time_step": r["value"]}
    assert d["cogging_period_mech_deg"] == pytest.approx(6.0)
    assert d["slide_mesh_divisions_360deg"] % 300 == 0


def test_tooth_yoke_and_fill_and_iron():
    r = estimate.tooth_yoke_flux_density(0.9, 60, 12, 10, 8, 6, stacking_factor=1.0)["value"]
    assert r["tooth"] == pytest.approx(0.9 * math.pi * 60 / 12 / 8)
    assert r["yoke"] == pytest.approx(0.9 * math.pi * 60 / 10 / 12)
    assert estimate.slot_fill(80, 40, conductor_area_mm2=0.8)["value"] == pytest.approx(0.4)
    i = estimate.iron_loss_steinmetz(0.02, 5e-5, 1.0, 100, 2.0)
    assert i["value"] == pytest.approx(2 * (0.02 * 100 + 5e-5 * 1e4))


def test_validation_messages():
    with pytest.raises(ValueError, match="unknown kind"):
        estimate.run("nope", {})
    with pytest.raises(ValueError, match="missing"):
        estimate.run("airgap_flux_density", {"Br": 1.2})
    with pytest.raises(ValueError, match="unknown parameters"):
        estimate.run("electrical_frequency", {"rpm": 1, "poles": 4, "speed": 2})
    with pytest.raises(ValueError, match="even integer"):
        estimate.electrical_frequency(3000, 5)
    with pytest.raises(ValueError, match="> 0"):
        estimate.airgap_flux_density(1.2, -4, 1)
    with pytest.raises(ValueError, match="exactly one"):
        estimate.slot_fill(80, 40)
    sig = estimate.signature("step_plan")
    assert sig["required"] == ["rpm", "poles"] and "steps_per_period" in sig["optional"]
