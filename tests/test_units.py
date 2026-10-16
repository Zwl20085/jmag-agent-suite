import math

import pytest

from jmag_bridge import units


def v(*a, **k):
    return units.convert(*a, **k)["value"]


def test_speed():
    assert v(3000, "rpm", "rad/s") == pytest.approx(100 * math.pi)
    assert v(3000, "rpm", "Hz", poles=8) == pytest.approx(200)
    assert v(200, "Hz", "rpm", poles=8) == pytest.approx(3000)
    assert v(50, "rps", "rpm") == pytest.approx(3000)
    assert v(100 * math.pi, "rad/s", "rad/s_e", poles=4) == pytest.approx(200 * math.pi)
    with pytest.raises(ValueError, match="poles"):
        v(3000, "rpm", "Hz")


def test_length_flux_angle():
    assert v(25.4, "mm", "in") == pytest.approx(1.0)
    assert v(10000, "G", "T") == pytest.approx(1.0)
    assert v(1.5, "T", "mT") == pytest.approx(1500)
    assert v(15, "deg", "deg_e", poles=8) == pytest.approx(60)
    assert v(math.pi, "rad_e", "deg", poles=4) == pytest.approx(90)


def test_awg():
    assert v(18, "AWG", "mm2") == pytest.approx(0.823, rel=2e-3)
    assert v(0.823, "mm2", "AWG") == pytest.approx(18, abs=0.01)
    assert units.awg_to_mm2(10) == pytest.approx(5.26, rel=2e-3)


def test_per_unit_and_bases():
    assert v(50, "SI", "pu", base=100) == 0.5
    assert v(0.5, "pu", "SI", base=100) == 50
    with pytest.raises(ValueError, match="base"):
        v(1, "SI", "pu")
    b = units.base_values(400, 10e3, 50)
    assert b["V_base_phase_rms"] == pytest.approx(400 / math.sqrt(3))
    assert b["I_base_rms"] * b["V_base_phase_rms"] * 3 == pytest.approx(10e3)


def test_mismatch_and_unknown():
    with pytest.raises(ValueError, match="cannot convert"):
        v(1, "mm", "T")
    with pytest.raises(ValueError, match="unknown unit"):
        v(1, "furlong", "m")
