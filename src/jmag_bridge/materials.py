"""Pure checks for user-defined materials handed to JMAG (no JMAG needed, unit-tested).

JMAG accepts almost any table and then extrapolates or solves something else, so the
bridge validates a B-H curve before it reaches ``jmag_create_material``.
"""
from __future__ import annotations

import math
from typing import Sequence

MU0 = 4e-7 * math.pi
#: keys of the iron-loss formula accepted by jmag_create_material (W/kg: Kh f B^alpha + Ke f^2 B^beta)
LOSS_KEYS = ("Kh", "Ke", "alpha", "beta")


class MaterialInputError(ValueError):
    def __init__(self, message: str, hint: str):
        super().__init__(message)
        self.hint = hint


def check_bh_table(bh: Sequence[Sequence[float]]) -> dict:
    """Validate a soft-magnetic B-H curve ``[[H A/m, B T], ...]``.

    Returns ``{"table": [[H, B], ...], "warnings": [...], "B_max": .., "H_max": ..}``; the table
    gets a leading ``(0, 0)`` row if it was missing. Raises :class:`MaterialInputError` when the
    curve is not usable (non-numeric, fewer than 3 points, H or B not strictly increasing,
    negative values, a slope below mu0).
    """
    try:
        rows = [(float(r[0]), float(r[1])) for r in bh]
    except (TypeError, ValueError, IndexError):
        raise MaterialInputError("bh must be a list of [H, B] pairs (A/m, T)",
                                 "example: [[0, 0], [100, 1.0], [1000, 1.5], [10000, 1.8]]") from None
    if any(not (math.isfinite(h) and math.isfinite(b)) for h, b in rows):
        raise MaterialInputError("bh contains NaN or infinite values", "send finite numbers only")
    warnings: list[str] = []
    if rows and rows[0] != (0.0, 0.0):
        if rows[0][0] <= 0.0 or rows[0][1] <= 0.0:
            raise MaterialInputError(f"first point {rows[0]} is not (0, 0) and not positive",
                                     "start the curve at (0, 0) or at a positive (H, B)")
        rows.insert(0, (0.0, 0.0))
        warnings.append("inserted the origin (0, 0) as first point")
    if len(rows) < 3:
        raise MaterialInputError(f"bh has {len(rows)} points; need at least 3", "send the full magnetisation curve")
    for (h0, b0), (h1, b1) in zip(rows, rows[1:]):
        if not (h1 > h0 and b1 > b0):
            raise MaterialInputError(f"H and B must both increase strictly; ({h0}, {b0}) -> ({h1}, {b1})",
                                     "sort by H and drop duplicate / falling points")
        if (b1 - b0) / (h1 - h0) < MU0 * (1 - 1e-6):
            raise MaterialInputError(f"slope below mu0 between H={h0} and H={h1} A/m",
                                     "a magnetisation curve never rises slower than mu0 (B = mu0 H + J, J >= 0 rising)")
    h_max, b_max = rows[-1]
    j_end = b_max - MU0 * h_max
    if h_max < 2e4:
        warnings.append(f"curve ends at H = {h_max:g} A/m; JMAG extrapolates with slope mu0 beyond it - "
                        f"extend to >= 1e5 A/m for deep saturation (tooth tips, ribs)")
    return {"table": [[h, b] for h, b in rows], "warnings": warnings, "H_max": h_max, "B_max": b_max,
            "J_end": j_end}


def check_loss(loss: dict | None) -> dict | None:
    """Validate ``{"Kh", "Ke", "alpha", "beta"}`` (beta defaults to 2)."""
    if loss is None:
        return None
    loss = dict(loss)
    loss.setdefault("beta", 2.0)
    unknown = sorted(set(loss) - set(LOSS_KEYS))
    missing = sorted(set(LOSS_KEYS) - set(loss))
    if unknown or missing:
        raise MaterialInputError(f"loss keys: unknown {unknown}, missing {missing}",
                                 f"use exactly {list(LOSS_KEYS)} (W/kg: Kh f B^alpha + Ke f^2 B^beta)")
    vals = {k: float(v) for k, v in loss.items()}
    if any(not math.isfinite(v) or v < 0 for v in vals.values()):
        raise MaterialInputError(f"loss coefficients must be finite and >= 0, got {vals}", "check signs and units")
    return vals


#: Typical reversible Br temperature coefficients [1/K] from magnet datasheets (cited as typical values only;
#: use the grade's own datasheet): sintered NdFeB about -0.0012, SmCo about -0.0003, AlNiCo about -0.0002.
TYPICAL_BR_TEMP_COEFF = {"NdFeB": -0.0012, "SmCo": -0.0003, "AlNiCo": -0.0002}
BR_RANGE = (0.0, 1.6)       # T, exclusive / inclusive: no commercial magnet exceeds ~1.5 T
MU_R_RANGE = (1.0, 1.3)     # recoil permeability of permanent magnets (ferrite ~1.05-1.2, NdFeB ~1.03-1.1)


def check_magnet(br: float | None = None, mu_r: float | None = None, temperature: float | None = None,
                 br_temp_coeff: float | None = None, bh: Sequence[Sequence[float]] | None = None) -> dict:
    """Validate a permanent-magnet material for jmag_create_material(kind="magnet").

    Linear: br [T] at 20 degC and mu_r (recoil, 1..1.3); optional temperature [degC] with br_temp_coeff [1/K]
    gives the applied Br(T) = br * (1 + coeff * (T - 20)). Nonlinear: bh = demagnetisation curve [[H A/m, B T],
    ...], H and B strictly increasing (H may be negative), B(H=0) = Br in (0, 1.6] T.
    Returns {"kind": "linear", "br", "br_20", "mu_r", "temperature", "br_temp_coeff", "warnings"} or
    {"kind": "nonlinear", "table", "br", "hcb", "warnings"}.
    """
    warnings: list[str] = []
    if bh is not None:
        if br is not None or mu_r is not None or temperature is not None or br_temp_coeff is not None:
            raise MaterialInputError("give either bh (nonlinear demagnetisation curve) or br/mu_r (linear), not both",
                                     "temperature scaling is only offered for the linear model; scale the table yourself")
        try:
            rows = [(float(r[0]), float(r[1])) for r in bh]
        except (TypeError, ValueError, IndexError):
            raise MaterialInputError("bh must be a list of [H, B] pairs (A/m, T)",
                                     "example: [[-900000, 0], [-800000, 0.2], [0, 1.2], [100000, 1.33]]") from None
        if any(not (math.isfinite(h) and math.isfinite(b)) for h, b in rows):
            raise MaterialInputError("bh contains NaN or infinite values", "send finite numbers only")
        if len(rows) < 3:
            raise MaterialInputError(f"bh has {len(rows)} points; need at least 3", "send the demagnetisation curve")
        for (h0, b0), (h1, b1) in zip(rows, rows[1:]):
            if not (h1 > h0 and b1 > b0):
                raise MaterialInputError(f"H and B must both increase strictly; ({h0}, {b0}) -> ({h1}, {b1})",
                                         "sort the demagnetisation curve by H (most negative first)")
        if rows[0][0] >= 0 or rows[-1][0] < 0:
            raise MaterialInputError("the demagnetisation curve must span H = 0 (start at negative H, end at H >= 0)",
                                     "second quadrant: H from about -Hcb to 0 A/m, B from about 0 to Br")
        br_t = _interp_zero(rows)
        if not (BR_RANGE[0] < br_t <= BR_RANGE[1]):
            raise MaterialInputError(f"B at H = 0 (Br) is {br_t:.4g} T, outside (0, 1.6] T", "check units (T, A/m)")
        hcb = _h_at_b0(rows)
        if hcb is None:
            warnings.append("curve does not reach B = 0; JMAG extrapolates below the first point")
        return {"kind": "nonlinear", "table": [[h, b] for h, b in rows], "br": br_t, "hcb": hcb, "warnings": warnings}
    if br is None or mu_r is None:
        raise MaterialInputError("a linear magnet needs br (T) and mu_r", "e.g. br=1.2, mu_r=1.05 (generic NdFeB)")
    try:
        br, mu_r = float(br), float(mu_r)
    except (TypeError, ValueError):
        raise MaterialInputError("br and mu_r must be numbers", "br in T, mu_r dimensionless") from None
    if not (BR_RANGE[0] < br <= BR_RANGE[1]):
        raise MaterialInputError(f"br = {br} T outside (0, 1.6] T", "Br of commercial magnets is 0.2-1.5 T")
    if not (MU_R_RANGE[0] <= mu_r <= MU_R_RANGE[1]):
        raise MaterialInputError(f"mu_r = {mu_r} outside [1, 1.3]", "recoil permeability of PMs is 1.0-1.3")
    br_app = br
    if temperature is not None:
        if br_temp_coeff is None:
            raise MaterialInputError("temperature needs br_temp_coeff (1/K)",
                                     f"typical datasheet values: {TYPICAL_BR_TEMP_COEFF}")
        t, a = float(temperature), float(br_temp_coeff)
        if not (math.isfinite(t) and math.isfinite(a)) or abs(a) > 0.01:
            raise MaterialInputError(f"br_temp_coeff = {a} 1/K is implausible", "use about -0.0012 (NdFeB)")
        if t < -60 or t > 250:
            warnings.append(f"temperature {t} degC is outside the usual -60..250 degC; the linear Br(T) law and the "
                            f"magnet's irreversible-demagnetisation limit are not checked here")
        br_app = br * (1.0 + a * (t - 20.0))
        if not (BR_RANGE[0] < br_app <= BR_RANGE[1]):
            raise MaterialInputError(f"Br at {t} degC would be {br_app:.4g} T", "check temperature and coefficient")
    elif br_temp_coeff is not None:
        warnings.append("br_temp_coeff ignored without temperature")
    return {"kind": "linear", "br": br_app, "br_20": br, "mu_r": mu_r, "temperature": temperature,
            "br_temp_coeff": br_temp_coeff, "warnings": warnings}


def _interp_zero(rows: list[tuple[float, float]]) -> float:
    for (h0, b0), (h1, b1) in zip(rows, rows[1:]):
        if h0 <= 0.0 <= h1:
            return b0 + (b1 - b0) * (0.0 - h0) / (h1 - h0)
    return rows[-1][1]


def _h_at_b0(rows: list[tuple[float, float]]) -> float | None:
    for (h0, b0), (h1, b1) in zip(rows, rows[1:]):
        if b0 <= 0.0 <= b1:
            return h0 + (h1 - h0) * (0.0 - b0) / (b1 - b0)
    return None
