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
