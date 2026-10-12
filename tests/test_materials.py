import math

import pytest

from jmag_bridge import materials as M


def test_valid_curve_passes_and_reports_ends():
    out = M.check_bh_table([[0, 0], [100, 1.0], [1000, 1.5], [1e5, 2.1]])
    assert out["table"][0] == [0.0, 0.0] and out["H_max"] == 1e5 and out["B_max"] == 2.1
    assert out["warnings"] == []


def test_origin_is_prepended_with_a_warning():
    out = M.check_bh_table([[100, 1.0], [1000, 1.5], [1e5, 2.1]])
    assert out["table"][0] == [0.0, 0.0] and len(out["table"]) == 4
    assert any("origin" in w for w in out["warnings"])


def test_short_curve_warns_about_mu0_extrapolation():
    out = M.check_bh_table([[0, 0], [100, 1.0], [1000, 1.5]])
    assert any("extrapolates" in w for w in out["warnings"])


@pytest.mark.parametrize("bh, match", [
    ([[0, 0], [100, 1.0], [100, 1.2]], "increase strictly"),
    ([[0, 0], [100, 1.0], [200, 0.9]], "increase strictly"),
    ([[0, 0], [100, 1.0]], "at least 3"),
    ([[0, 0], ["x", 1.0], [200, 1.1]], "pairs"),
    ([[0, 0], [100, math.nan], [200, 1.1]], "NaN"),
    ([[0, 0], [100, 1.0], [1e6, 1.0 + 0.5 * 4e-7 * math.pi * 1e6]], "slope below mu0"),
    ([[-1, 0], [100, 1.0], [200, 1.1]], "not \\(0, 0\\)"),
])
def test_unusable_curves_are_rejected_with_a_hint(bh, match):
    with pytest.raises(M.MaterialInputError, match=match) as e:
        M.check_bh_table(bh)
    assert e.value.hint


def test_loss_defaults_beta_and_rejects_unknown_keys():
    assert M.check_loss({"Kh": 0.01, "Ke": 1e-4, "alpha": 2}) == {"Kh": 0.01, "Ke": 1e-4, "alpha": 2.0, "beta": 2.0}
    assert M.check_loss(None) is None
    with pytest.raises(M.MaterialInputError, match="unknown"):
        M.check_loss({"Kh": 0.01, "Ke": 1e-4, "alpha": 2, "gamma": 1})
    with pytest.raises(M.MaterialInputError, match=">= 0"):
        M.check_loss({"Kh": -0.01, "Ke": 1e-4, "alpha": 2})
