"""Unit conversions engineers keep re-deriving. Pure Python.

Quantities and units (case-insensitive):
- speed:         rpm, rad/s (mechanical), rps (mechanical Hz), Hz (electrical, needs poles), rad/s_e (needs poles)
- length:        m, cm, mm, um, in
- flux_density:  T, mT, G, kG
- angle:         deg (mechanical), rad (mechanical), deg_e, rad_e (electrical, need poles)
- area:          m2, mm2, cm2, kcmil, AWG (wire gauge number, solid round wire)
- per_unit:      SI <-> pu with an explicit base value
"""
from __future__ import annotations

import math

_LINEAR = {
    "length": {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6, "in": 0.0254},
    "flux_density": {"t": 1.0, "mt": 1e-3, "g": 1e-4, "kg": 0.1},
    "area": {"m2": 1.0, "cm2": 1e-4, "mm2": 1e-6, "kcmil": 0.506707479e-6},
}
_SPEED = {"rpm", "rad/s", "rps", "hz", "rad/s_e"}
_ANGLE = {"deg", "rad", "deg_e", "rad_e"}


def awg_to_mm2(awg: float) -> float:
    """Solid round wire area: d = 0.127 mm * 92^((36 - n)/39). AWG 0000 = -3."""
    d = 0.127 * 92 ** ((36 - float(awg)) / 39)
    return math.pi / 4 * d * d


def mm2_to_awg(mm2: float) -> float:
    """Inverse of awg_to_mm2 (real-valued gauge; round up to the next integer for a wire at least this big)."""
    if mm2 <= 0:
        raise ValueError("area must be > 0")
    d = math.sqrt(4 * mm2 / math.pi)
    return 36 - 39 * math.log(d / 0.127, 92)


def _pp(poles: int | None, what: str) -> float:
    if not poles or int(poles) != poles or poles % 2:
        raise ValueError(f"{what} needs poles = number of poles (even integer, e.g. poles=8)")
    return poles / 2


def _speed_to_rads(v: float, u: str, poles: int | None) -> float:
    if u == "rpm":
        return v * 2 * math.pi / 60
    if u == "rad/s":
        return v
    if u == "rps":
        return v * 2 * math.pi
    if u == "hz":
        return v * 2 * math.pi / _pp(poles, "electrical Hz")
    return v / _pp(poles, "electrical rad/s")  # rad/s_e


def _rads_to_speed(w: float, u: str, poles: int | None) -> float:
    return w / _speed_to_rads(1.0, u, poles)


def _angle_to_rad(v: float, u: str, poles: int | None) -> float:
    if u == "deg":
        return math.radians(v)
    if u == "rad":
        return v
    if u == "deg_e":
        return math.radians(v) / _pp(poles, "electrical degrees")
    return v / _pp(poles, "electrical radians")


def quantity_of(unit: str) -> str:
    u = unit.lower()
    if u in _SPEED:
        return "speed"
    if u in _ANGLE:
        return "angle"
    if u == "awg":
        return "area"
    for q, table in _LINEAR.items():
        if u in table:
            return q
    if u in ("si", "pu"):
        return "per_unit"
    raise ValueError(f"unknown unit {unit!r}; known: {sorted(_SPEED | _ANGLE | {'awg', 'si', 'pu'} | {k for t in _LINEAR.values() for k in t})}")


def convert(value: float, from_unit: str, to_unit: str, poles: int | None = None, base: float | None = None) -> dict:
    """Convert value; returns {"value", "unit", "quantity", "formula"}."""
    f, t = from_unit.lower(), to_unit.lower()
    q = quantity_of(f)
    if quantity_of(t) != q:
        raise ValueError(f"cannot convert {from_unit} ({q}) to {to_unit} ({quantity_of(t)})")
    v = float(value)
    if q == "speed":
        out = _rads_to_speed(_speed_to_rads(v, f, poles), t, poles)
        note = "Hz and rad/s_e are electrical: f_e = rpm/60 * poles/2"
    elif q == "angle":
        out = _angle_to_rad(v, f, poles) / _angle_to_rad(1.0, t, poles)
        note = "electrical angle = mechanical angle * poles/2"
    elif q == "per_unit":
        if not base:
            raise ValueError("per_unit needs base=<base value in SI>, e.g. rated current")
        out = v / base if (f, t) == ("si", "pu") else v * base if (f, t) == ("pu", "si") else v
        note = "pu = SI / base"
    elif q == "area" and "awg" in (f, t):
        mm2 = awg_to_mm2(v) if f == "awg" else v * _LINEAR["area"][f] / 1e-6
        out = mm2_to_awg(mm2) if t == "awg" else mm2 * 1e-6 / _LINEAR["area"][t]
        note = "AWG: d = 0.127 mm * 92^((36-n)/39), solid round wire"
    else:
        table = _LINEAR[q]
        out = v * table[f] / table[t]
        note = "linear scale"
    return {"value": out, "unit": to_unit, "quantity": q, "from": {"value": v, "unit": from_unit}, "formula": note}


def base_values(v_ll_rms: float, s_va: float, f_hz: float | None = None) -> dict:
    """Per-unit bases of a 3-phase machine from rated line voltage (rms) and apparent power.

    V_base = V_ll/sqrt(3) (phase), I_base = S/(3 V_base), Z_base = V_base/I_base, L_base = Z_base/omega_base.
    """
    if v_ll_rms <= 0 or s_va <= 0:
        raise ValueError("v_ll_rms and s_va must be > 0")
    vb = v_ll_rms / math.sqrt(3)
    ib = s_va / (3 * vb)
    zb = vb / ib
    out = {"V_base_phase_rms": vb, "I_base_rms": ib, "Z_base_ohm": zb, "S_base_VA": s_va}
    if f_hz:
        out["L_base_H"] = zb / (2 * math.pi * f_hz)
        out["psi_base_Wb"] = vb * math.sqrt(2) / (2 * math.pi * f_hz)
    return out
