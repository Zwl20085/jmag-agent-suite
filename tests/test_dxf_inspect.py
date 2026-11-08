"""jmag_dxf_inspect backend (dxf_inspect): DXFs written by dxf.Dxf read back (round trip) and a hand-written fixture."""
import math
from pathlib import Path

import pytest

from jmag_bridge import dxf, dxf_inspect

FIXTURE = Path(__file__).parent / "fixtures" / "hand_minimal.dxf"


def _spm(path: Path, slots=12, poles=4) -> str:
    """The examples/spm_motor geometry (12s/4p SPM, shared edges drawn twice)."""
    d = dxf.Dxf()
    pol = lambda r, a: (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)))  # noqa: E731
    r_rot, r_mag, r_bore, r_sb, r_out = 27.0, 30.0, 31.0, 45.0, 60.0
    pitch, half = 360.0 / slots, 7.5
    d.circle((0, 0), r_out)
    for k in range(slots):
        a0, a1 = k * pitch - half, k * pitch + half
        d.arc((0, 0), r_sb, a0, a1)
        d.line(pol(r_bore, a0), pol(r_sb, a0))
        d.line(pol(r_bore, a1), pol(r_sb, a1))
        d.arc((0, 0), r_bore, a1, a0 + pitch)
        d.arc((0, 0), r_bore, a0, a1)
    d.circle((0, 0), r_rot)
    for k in range(poles):
        c = k * 360.0 / poles
        d.annular_sector(r_rot, r_mag, c - 40, c + 40)
    return d.save(str(path))


def test_spm_round_trip(tmp_path):
    r = dxf.inspect(_spm(tmp_path / "spm.dxf"))
    assert r["region_count"] == 19 and r["units"] == "unknown"     # Dxf writes no header: units unknown, warned
    assert len(r["warnings"]) == 1 and "$INSUNITS missing" in r["warnings"][0]
    assert r["guesses"] == {"stator-like": 1, "slot-like": 12, "airgap-like": 1, "rotor-like": 1, "magnet-like": 4}
    by = {x["name"]: x for x in r["regions"]}
    slot_exact = (45 ** 2 - 31 ** 2) * math.pi * 15 / 360
    assert by["Slot01"]["area_mm2"] == pytest.approx(slot_exact, rel=2e-4)
    assert by["Magnet1"]["area_mm2"] == pytest.approx((30 ** 2 - 27 ** 2) * math.pi * 80 / 360, rel=2e-4)
    assert by["RotorCore"]["area_mm2"] == pytest.approx(math.pi * 27 ** 2, rel=2e-4)
    stator_exact = math.pi * (60 ** 2 - 31 ** 2) - 12 * slot_exact
    assert by["StatorCore"]["area_mm2"] == pytest.approx(stator_exact, rel=2e-4) and by["StatorCore"]["holes"] == 1
    # every inside point lies inside its own region: radius between r_min and r_max
    for x in r["regions"]:
        rp = math.hypot(*x["point"])
        assert x["r_min"] - 1e-6 <= rp <= x["r_max"] + 1e-6, x
    lab = r["labels_draft"]
    assert lab["suppress"] == ["GapAir"] and set(lab["rotor_parts"]) == {"RotorCore", "Magnet1", "Magnet2", "Magnet3",
                                                                       "Magnet4"}
    assert [m["polarity"] for m in lab["magnets"]] == ["outward", "inward", "outward", "inward"]
    assert [m["center_deg"] for m in lab["magnets"]] == pytest.approx([0, 90, 180, 270], abs=1e-6)
    assert all(0 < p["confidence"] <= 1 for p in lab["parts"]) and lab["expected"]["part_count"] == 19
    assert {"dxf", "parts", "magnets", "rotor_parts"} <= set(lab)


def test_c_core_round_trip(tmp_path):
    meta = dxf.c_core_inductor(str(tmp_path / "c.dxf"))
    r = dxf.inspect(str(tmp_path / "c.dxf"))
    areas = sorted(x["area_mm2"] for x in r["regions"])
    assert areas == pytest.approx([meta["coil_area_mm2"]] * 2 + [meta["core_area_mm2"]])
    assert r["guesses"] == {"core-like": 1, "coil-like": 2}     # not a machine: no stator/rotor guesses
    core = next(x for x in r["regions"] if x["guess"] == "core-like")
    pts = {tuple(x["point"]) for x in r["regions"] if x["guess"] == "coil-like"}
    assert pts == {(20.0, 30.0), (-5.0, 30.0)}
    assert core["point"] is not None and core["name"] == "Core"


def test_hand_written_fixture():
    r = dxf.inspect(str(FIXTURE))
    by_layer = {x["layer"]: x for x in r["regions"]}
    assert r["region_count"] == 3
    rect = by_layer["STATOR"]                                   # 20x10 + semicircular bulge (r=5) - hole r=2
    assert rect["area_mm2"] == pytest.approx(200 + math.pi * 25 / 2 - math.pi * 4, rel=1e-4) and rect["holes"] == 1
    px, py = rect["point"]
    assert math.hypot(px - 5, py - 5) > 2 and 0 < py < 10        # inside point avoids the hole
    assert by_layer["AIR"]["area_mm2"] == pytest.approx(math.pi * 4, rel=1e-4)
    assert by_layer["AIR"]["guess"] == "airgap-like"           # layer name overrides geometry
    assert by_layer["MAGNET"]["area_mm2"] == pytest.approx(50.0)
    assert by_layer["MAGNET"]["guess"] == "magnet-like" and by_layer["MAGNET"]["confidence"] == 0.6
    assert r["diagnostics"]["skipped_entities"] == {"SPLINE": 1} and any("SPLINE" in w for w in r["warnings"])


def test_t_junction_open_end_and_duplicates(tmp_path):
    d = dxf.Dxf()
    d.polyline([(0, 0), (10, 0), (10, 10), (0, 10)])
    d.polyline([(0, 0), (10, 0), (10, 10), (0, 10)])          # drawn twice: merged
    d.line((5, 0), (5, 10))                                    # T-junctions on both long sides: two regions
    d.line((20, 0), (25, 0))                                   # dangling edge: open ends, no region
    r = dxf.inspect(d.save(str(tmp_path / "t.dxf")))
    assert sorted(x["area_mm2"] for x in r["regions"]) == pytest.approx([50.0, 50.0])
    assert r["diagnostics"]["open_ends"] == 2 and any("open end" in w for w in r["warnings"])


def test_errors():
    with pytest.raises(dxf_inspect.DxfReadError, match="cannot read"):
        dxf.inspect("no/such/file.dxf")
    with pytest.raises(dxf_inspect.DxfReadError, match="group code"):
        dxf_inspect.parse("0\nSECTION\nnot-a-code\nX\n")
