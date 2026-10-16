import pytest

from jmag_bridge import winding


@pytest.mark.parametrize("slots,poles,layers,pitch,kw1", [
    (12, 10, 2, None, 0.933),   # concentrated double layer
    (12, 4, 1, None, 1.0),      # q = 1, full pitch
    (24, 4, 2, None, 0.966),    # q = 2, full pitch: distribution factor only
    (24, 4, 1, None, 0.966),
    (9, 8, 2, None, 0.945),
    (12, 10, 1, None, 0.966),   # single-layer tooth coils
    (24, 4, 2, 5, 0.933),       # 5/6 short pitch: 0.966 * sin(75 deg)
    (12, 8, 2, None, 0.866),    # q = 0.5
    (36, 8, 2, None, 0.945),    # q = 1.5
])
def test_kw1_known_values(slots, poles, layers, pitch, kw1):
    p = winding.plan(slots, poles, 3, layers, pitch)
    assert p["kw1"] == pytest.approx(kw1, abs=1e-3)


def test_infeasible_combinations_raise_actionable_errors():
    with pytest.raises(ValueError, match="unbalanced"):
        winding.plan(12, 12)  # Q/(m t) = 12/(3*6)
    with pytest.raises(ValueError, match="divisible by 3"):
        winding.plan(10, 8)
    with pytest.raises(ValueError, match="poles"):
        winding.plan(12, 5)
    with pytest.raises(ValueError, match="single layer"):
        winding.plan(9, 8, layers=1)
    with pytest.raises(ValueError, match="coil_pitch"):
        winding.plan(12, 10, coil_pitch=0)


def test_slot_map_balanced_and_named():
    p = winding.plan(12, 10, turns=20)
    assert len(p["slot_map"]) == 24
    per_phase = {ph: sum(1 for s in p["slot_map"] if s["phase"] == ph) for ph in "UVW"}
    assert set(per_phase.values()) == {8}
    assert p["slot_map"][0]["part"] == "Slot01a_U+"
    assert all(s["part"].startswith("Slot") and s["part"][-1] in "+-" for s in p["slot_map"])
    assert p["coils_per_phase"] == 4 and p["series_turns_per_phase"] == 80
    coils = p["labels_coils"]
    assert set(coils) == {"U", "V", "W"} and len(coils["U"]["up"]) == len(coils["U"]["down"]) == 4


def test_single_layer_names_and_coils():
    p = winding.plan(12, 4, layers=1)
    assert [s["part"] for s in p["slot_map"][:4]] == ["Slot01_U+", "Slot02_W-", "Slot03_V+", "Slot04_U-"]
    assert len(p["coil_list"]) == 6 and all(c["pitch"] == 3 for c in p["coil_list"])


def test_symmetry_sector():
    assert winding.symmetry(12, 10)["sector_fraction"] == "1/2"      # anti-periodic half
    assert winding.symmetry(12, 4)["sector_fraction"] == "1/4"
    assert winding.symmetry(9, 8)["boundary"].startswith("none")
    assert winding.symmetry(12, 8)["boundary"] == "periodic"


def test_mmf_harmonics_working_wave_and_subharmonic():
    h = {r["order_mech"]: r for r in winding.plan(12, 10)["mmf_harmonics"]}
    assert h[5]["working"] and h[5]["rel_amplitude"] == 1.0
    assert 1 in h and h[1]["rel_amplitude"] > 0.2  # 12s/10p's known sub-harmonic
    dist = {r["order_mech"] for r in winding.plan(24, 4)["mmf_harmonics"]}
    assert 1 not in dist and 2 in dist  # integral-slot winding: no sub-harmonic


def test_parallel_paths_validation():
    with pytest.raises(ValueError, match="parallel_paths"):
        winding.plan(12, 10, parallel_paths=3)
    p = winding.plan(24, 4, turns=10, parallel_paths=2)
    assert p["series_turns_per_phase"] == 40 and not p["warnings"]
