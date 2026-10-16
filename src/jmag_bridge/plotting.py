"""Server-side PNG plots of results, sweeps and maps (matplotlib, optional extra ``plots``).

Every function writes a PNG and returns ``{"path", "annotations"}``: the numbers drawn on the figure, so an
agent without vision can still check them. Inputs are plain lists/dicts - the shape jmag_get_result returns
(``x`` + ``series``/``columns`` {name: [values]}) - or a CSV written by jmag_export_results.
"""
from __future__ import annotations

import csv
import math
from typing import Sequence

from . import health

INSTALL_HINT = 'pip install "jmag-mcp-bridge[plots]"  (installs matplotlib into the bridge\'s Python)'


class PlottingUnavailable(ImportError):
    hint = INSTALL_HINT


def _plt():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as e:
        raise PlottingUnavailable(f"matplotlib is not installed ({e})") from None
    return plt


def _r(v: float, n: int = 5) -> float:
    return float(f"{v:.{n}g}") if isinstance(v, float) and math.isfinite(v) else v


def _check_series(x: Sequence[float], series: dict) -> None:
    if not series:
        raise ValueError("no data series given (expected {name: [values]})")
    for name, ys in series.items():
        if len(ys) != len(x):
            raise ValueError(f"series {name!r} has {len(ys)} points but x has {len(x)}")


def _save(fig, plt, path: str) -> str:
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def load_csv(path: str) -> dict:
    """Read a CSV (first column = x, the rest = numeric series; header row required).

    Returns {"x_name", "x", "columns": {name: [...]}}. Works with jmag_export_results files.
    """
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError(f"{path}: needs a header row and at least one data row")
    head = [h.strip() for h in rows[0]]
    data = [[float(v) for v in r] for r in rows[1:] if r and any(c.strip() for c in r)]
    cols = {head[j]: [r[j] for r in data] for j in range(1, len(head))}
    return {"x_name": head[0], "x": [r[0] for r in data], "columns": cols}


def waveform(x: Sequence[float], series: dict, path: str, title: str | None = None, x_label: str = "time [s]",
             y_label: str = "") -> dict:
    """Lines vs x with a dashed mean line per series; annotations = mean/rms/min/max/pk-pk per series."""
    _check_series(x, series)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ann = {}
    for name, ys in series.items():
        st = health.waveform_stats(ys)
        line, = ax.plot(x, ys, label=f"{name}  mean={st['mean']:.4g} rms={st['rms']:.4g}")
        ax.axhline(st["mean"], color=line.get_color(), ls="--", lw=0.8)
        ann[name] = {k: _r(v) for k, v in st.items()}
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title or "waveform")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    return {"path": _save(fig, plt, path), "annotations": ann}


def fft(series: dict, path: str, x: Sequence[float] | None = None, electrical_frequency_hz: float | None = None,
        orders: int = 15, title: str | None = None) -> dict:
    """Harmonic bar chart. The samples must span exactly one period (endpoint excluded); with x and
    electrical_frequency_hz the last electrical period is cut out first (uniform steps assumed)."""
    if not series:
        raise ValueError("no data series given")
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ann = {}
    width = 0.8 / len(series)
    for i, (name, ys) in enumerate(series.items()):
        y = list(ys)
        if x is not None and electrical_frequency_hz:
            _, y = health.last_period(x, y, 1.0 / electrical_frequency_hz)
        h = health.harmonics(y, orders)
        if not h:
            raise ValueError(f"series {name!r}: need at least 4 samples in the analysed period")
        amps = h["amplitudes"]
        ax.bar([k + i * width for k in range(len(amps))], amps, width=width, label=name)
        ann[name] = {"dc": _r(h["dc"]), "fundamental": _r(h["fundamental"]), "thd_pct": _r(h.get("thd_pct")),
                     "amplitudes": [_r(a, 4) for a in amps], "samples": len(y)}
    ax.set_xlabel("harmonic order (of the analysed period)")
    ax.set_ylabel("amplitude")
    ax.set_title(title or "harmonics")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=8)
    return {"path": _save(fig, plt, path), "annotations": ann}


def sweep(x: Sequence[float], series: dict, path: str, title: str | None = None, x_label: str = "",
          y_label: str = "") -> dict:
    """x vs y with markers (parameter sweeps); annotations = min/max and their x per series."""
    _check_series(x, series)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ann = {}
    for name, ys in series.items():
        ax.plot(x, ys, "o-", label=name)
        kmax = max(range(len(ys)), key=lambda k: ys[k])
        kmin = min(range(len(ys)), key=lambda k: ys[k])
        ann[name] = {"max": _r(ys[kmax]), "x_at_max": _r(x[kmax]), "min": _r(ys[kmin]), "x_at_min": _r(x[kmin]),
                     "points": len(ys)}
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title or "sweep")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    return {"path": _save(fig, plt, path), "annotations": ann}


def heatmap(x: Sequence[float], y: Sequence[float], z: Sequence[Sequence[float]], path: str, title: str | None = None,
            x_label: str = "", y_label: str = "", z_label: str = "", levels: Sequence[float] | None = None) -> dict:
    """2D map (e.g. efficiency over speed x torque) with filled colours and labelled contour lines.

    z[i][j] belongs to (x[j], y[i]); NaN/None cells (infeasible points) are left blank.
    """
    if len(z) != len(y) or any(len(row) != len(x) for row in z):
        raise ValueError(f"z must be len(y)={len(y)} rows of len(x)={len(x)} values")
    plt = _plt()
    zz = [[float("nan") if v is None else float(v) for v in row] for row in z]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    cf = ax.contourf(x, y, zz, levels=20)
    fig.colorbar(cf, ax=ax, label=z_label)
    cs = ax.contour(x, y, zz, levels=list(levels) if levels else 8, colors="k", linewidths=0.6)
    ax.clabel(cs, fontsize=7)
    cells = [(zz[i][j], x[j], y[i]) for i in range(len(y)) for j in range(len(x)) if math.isfinite(zz[i][j])]
    if not cells:
        raise ValueError("z has no finite values")
    best, worst = max(cells), min(cells)
    ax.plot(best[1], best[2], "w*", ms=10)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title or "map")
    ann = {"z_max": _r(best[0]), "at_max": [_r(best[1]), _r(best[2])], "z_min": _r(worst[0]),
           "at_min": [_r(worst[1]), _r(worst[2])], "finite_cells": len(cells), "blank_cells": len(x) * len(y) - len(cells)}
    return {"path": _save(fig, plt, path), "annotations": ann}


def torque_speed(speed_rpm: Sequence[float], torque_nm: Sequence[float], path: str, title: str | None = None,
                 power_w: Sequence[float] | None = None) -> dict:
    """Torque-speed envelope with the power curve (computed as T*omega if not given) on a second axis.

    Annotations: max torque, base (corner) speed = highest speed with T >= 98 % of max, max power and its speed.
    """
    _check_series(speed_rpm, {"torque": torque_nm})
    if power_w is None:
        power_w = [t * n * 2 * math.pi / 60 for t, n in zip(torque_nm, speed_rpm)]
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(speed_rpm, torque_nm, "b-", label="torque")
    ax.set_xlabel("speed [rpm]")
    ax.set_ylabel("torque [Nm]", color="b")
    ax2 = ax.twinx()
    ax2.plot(speed_rpm, [p / 1e3 for p in power_w], "r--", label="power")
    ax2.set_ylabel("power [kW]", color="r")
    tmax = max(torque_nm)
    base = max((n for n, t in zip(speed_rpm, torque_nm) if t >= 0.98 * tmax), default=None)
    kp = max(range(len(power_w)), key=lambda k: power_w[k])
    if base is not None:
        ax.axvline(base, color="gray", ls=":", lw=0.8)
    ax.set_title(title or "torque-speed envelope")
    ax.grid(alpha=0.3)
    ann = {"max_torque_Nm": _r(tmax), "base_speed_rpm": _r(base), "max_power_W": _r(power_w[kp]),
           "speed_at_max_power_rpm": _r(speed_rpm[kp]), "max_speed_rpm": _r(max(speed_rpm))}
    return {"path": _save(fig, plt, path), "annotations": ann}


KINDS = ("waveform", "fft", "sweep", "map", "torque_speed")


def plot(kind: str, data: dict, path: str, x: str | None = None, y: list[str] | str | None = None,
         title: str | None = None, electrical_frequency_hz: float | None = None) -> dict:
    """Dispatch on kind with a jmag_get_result-like dict.

    data: {"x": [...], "series" | "columns": {name: [...]}}  (waveform, fft, sweep, torque_speed)
          or {"x": [...], "y": [...], "z": [[...]]}            (map)
    x: name of a series to use as the x axis instead of data["x"]; y: series name(s) to keep (default all).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown plot kind {kind!r}; kinds: {list(KINDS)}")
    if kind == "map":
        for k in ("x", "y", "z"):
            if k not in data:
                raise ValueError("map needs data={'x': [...], 'y': [...], 'z': [[...] per y]}")
        return heatmap(data["x"], data["y"], data["z"], path, title, data.get("x_label", ""), data.get("y_label", ""),
                       data.get("z_label", ""))
    series = dict(data.get("series") or data.get("columns") or {})
    xs = data.get("x")
    x_label = data.get("x_name", "x")
    if data.get("x_unit"):
        x_label += f" [{data['x_unit']}]"
    if x:
        if x not in series:
            raise ValueError(f"x={x!r} is not a series; series: {list(series)}")
        xs, x_label = series.pop(x), x
    if y:
        names = [y] if isinstance(y, str) else list(y)
        bad = [n for n in names if n not in series]
        if bad:
            raise ValueError(f"series {bad} not found; series: {list(series)}")
        series = {n: series[n] for n in names}
    if xs is None and kind != "fft":
        raise ValueError("data has no x values (pass data['x'] or x=<series name>)")
    y_label = data.get("unit", "")
    if kind == "waveform":
        return waveform(xs, series, path, title, x_label, y_label)
    if kind == "fft":
        return fft(series, path, xs, electrical_frequency_hz, title=title)
    if kind == "sweep":
        return sweep(xs, series, path, title, x_label, y_label)
    tq = next(iter(series.values()))
    pw = series.get("power") or series.get("power_W")
    return torque_speed(xs, tq, path, title, pw)
