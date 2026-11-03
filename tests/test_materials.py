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


# --------------------------------------------------------------------------- permanent magnets

def test_linear_magnet_temperature_scaling():
    out = M.check_magnet(br=1.2, mu_r=1.05, temperature=120, br_temp_coeff=-0.0012)
    assert out["kind"] == "linear" and out["br_20"] == 1.2 and abs(out["br"] - 1.056) < 1e-12
    assert M.check_magnet(br=1.2, mu_r=1.05)["br"] == 1.2
    assert M.check_magnet(br=1.2, mu_r=1.05, temperature=20, br_temp_coeff=-0.0012)["br"] == 1.2
    assert M.check_magnet(br=1.2, mu_r=1.05, br_temp_coeff=-0.0012)["warnings"]


@pytest.mark.parametrize("kw, match", [
    ({"br": 1.7, "mu_r": 1.05}, r"outside \(0, 1.6\]"),
    ({"br": 0.0, "mu_r": 1.05}, "outside"),
    ({"br": 1.2, "mu_r": 0.9}, r"outside \[1, 1.3\]"),
    ({"br": 1.2, "mu_r": 1.4}, "outside"),
    ({"br": 1.2}, "needs br"),
    ({"br": 1.2, "mu_r": 1.05, "temperature": 100}, "br_temp_coeff"),
    ({"br": 1.2, "mu_r": 1.05, "temperature": 100, "br_temp_coeff": 0.5}, "implausible"),
    ({"br": 1.2, "mu_r": 1.05, "bh": [[-1, 0], [0, 1], [1, 1.1]]}, "either bh"),
])
def test_bad_linear_magnets(kw, match):
    with pytest.raises(M.MaterialInputError, match=match) as e:
        M.check_magnet(**kw)
    assert e.value.hint


def test_demagnetisation_curve():
    out = M.check_magnet(bh=[[-950000, 0.0], [-900000, 0.08], [-800000, 0.2], [0, 1.2], [100000, 1.33]])
    assert out["kind"] == "nonlinear" and out["br"] == 1.2 and out["hcb"] == -950000.0 and not out["warnings"]
    assert M.check_magnet(bh=[[-800000, 0.2], [0, 1.2], [1e5, 1.33]])["warnings"]   # does not reach B = 0


@pytest.mark.parametrize("bh, match", [
    ([[-9e5, 0.0], [-8e5, 0.2], [-8e5, 0.3], [0, 1.2]], "increase strictly"),
    ([[-9e5, 0.3], [-8e5, 0.2], [0, 1.2]], "increase strictly"),
    ([[1, 0.1], [2, 0.2], [3, 0.3]], "span H = 0"),
    ([[-9e5, 0.0], [-8e5, 0.2], [0, 1.8]], "outside"),
    ([[-9e5, 0.0], [0, 1.2]], "at least 3"),
])
def test_bad_demagnetisation_curves(bh, match):
    with pytest.raises(M.MaterialInputError, match=match):
        M.check_magnet(bh=bh)
