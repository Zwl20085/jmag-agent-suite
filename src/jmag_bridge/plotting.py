"""Server-side PNG plots of results, sweeps, maps and FE fields (matplotlib, optional extra ``plots``).

Every function writes a PNG and returns ``{"path", "annotations"}``: the numbers drawn on the figure, so an
agent without vision can still check them. Inputs are plain lists/dicts - the shape jmag_get_result returns
(``x`` + ``series``/``columns`` {name: [values]}) - or a CSV written by jmag_export_results. Field plots
(``field``, ``mesh``) take the plain-dict mesh/field dataset described in ``validate_field_data``.

All figures share one house style (``_style``), applied through ``plt.rc_context`` so nothing leaks into
global rcParams: white opaque surface with a hairline border, recessive grid, top/right spines off, fixed
categorical colour order (never cycled: series 9+ are grey), frameless small legends, dark-grey text.
"""
from __future__ import annotations

import contextlib
import csv
import math
from typing import Sequence

from . import fielddata, health

INSTALL_HINT = 'pip install "jmag-agent-suite[plots]"  (installs matplotlib into the bridge\'s Python)'

# ----------------------------------------------------------------------------------------------- house style
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
OTHER_COLOR = "#a9a8a2"          # every series / part past the 8th
INK = "#0b0b0b"                   # primary text
INK2 = "#52514e"                  # secondary text (labels, ticks, legends)
MUTED = "#898781"                 # reference lines, annotations of secondary importance
AXIS = "#c3c2b7"                  # spines / ticks
GRID = "#b4b2ab"                  # drawn at alpha 0.35
BORDER = "#d6d5ce"                # hairline figure border
DPI = 160
FIG_W = 7.2                       # inches: every line chart has this width
FIG_H = 4.0
FIELD_CMAP = "magma"              # perceptually uniform, sequential
# flux lines: thin dark line on a faint light halo, so they read on both ends of the colour map
FLUX_STYLE = {"color": "#1a1a1a", "lw": 0.45, "alpha": 0.75, "halo": (1.2, "white", 0.25)}
MAP_RAMP = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")  # one-hue blue


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


def _rc() -> dict:
    from cycler import cycler  # ships with matplotlib
    return {
        "figure.facecolor": "white", "figure.edgecolor": BORDER, "figure.dpi": 100,
        "figure.constrained_layout.use": True,
        "figure.constrained_layout.h_pad": 0.06, "figure.constrained_layout.w_pad": 0.08,
        "savefig.facecolor": "white", "savefig.edgecolor": BORDER, "savefig.dpi": DPI,
        "font.family": "sans-serif", "font.sans-serif": ["Segoe UI", "Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 9, "text.color": INK,
        "axes.facecolor": "white", "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
        "axes.labelcolor": INK2, "axes.labelsize": 9, "axes.titlesize": 10, "axes.titlelocation": "left",
        "axes.titlecolor": INK, "axes.titlepad": 8,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "axes.axisbelow": True,
        "axes.prop_cycle": cycler(color=list(SERIES_COLORS)),
        "axes.formatter.limits": (-4, 5), "axes.formatter.use_mathtext": True, "axes.formatter.useoffset": False,
        "grid.color": GRID, "grid.alpha": 0.35, "grid.linewidth": 0.6, "grid.linestyle": "-",
        "xtick.color": AXIS, "ytick.color": AXIS, "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "xtick.major.size": 3, "ytick.major.size": 3,
        "xtick.major.width": 0.8, "ytick.major.width": 0.8,
        "lines.linewidth": 1.8, "lines.markersize": 5.5,
        "lines.solid_capstyle": "round", "lines.solid_joinstyle": "round",
        "legend.frameon": False, "legend.fontsize": 8, "legend.labelcolor": INK2, "legend.handlelength": 1.6,
        "legend.borderaxespad": 0.3, "legend.columnspacing": 1.4,
        "contour.negative_linestyle": "solid", "image.cmap": FIELD_CMAP,
        "patch.linewidth": 0.0,
    }


@contextlib.contextmanager
def _style():
    """Yield pyplot with the house style active; rcParams are restored on exit."""
    plt = _plt()
    with plt.rc_context(_rc()):
        yield plt


def _color(i: int) -> str:
    """Categorical colour for series i (fixed order, never cycled)."""
    return SERIES_COLORS[i] if i < len(SERIES_COLORS) else OTHER_COLOR


def _r(v: float, n: int = 5) -> float:
    return float(f"{v:.{n}g}") if isinstance(v, float) and math.isfinite(v) else v


def _g(v, n: int = 4) -> str:
    return f"{v:.{n}g}" if isinstance(v, (int, float)) and math.isfinite(v) else str(v)


def _check_series(x: Sequence[float], series: dict) -> None:
    if not series:
        raise ValueError("no data series given (expected {name: [values]})")
    for name, ys in series.items():
        if len(ys) != len(x):
            raise ValueError(f"series {name!r} has {len(ys)} points but x has {len(x)}")


def _figure(plt, w: float = FIG_W, h: float = FIG_H, **kw):
    fig, ax = plt.subplots(figsize=(w, h), **kw)
    fig.patch.set_linewidth(1.0)
    return fig, ax


def _save(fig, plt, path: str, dpi: int = DPI) -> str:
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def _legend_above(ax, n: int) -> None:
    """Frameless legend in a row just above the plot area (never over the data)."""
    if n == 0:
        return
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=min(n, 3), borderaxespad=0.2,
              handlelength=1.4, fontsize=8)


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


# ----------------------------------------------------------------------------------------------- line charts

def waveform(x: Sequence[float], series: dict, path: str, title: str | None = None, x_label: str = "time [s]",
             y_label: str = "") -> dict:
    """Lines vs x with a dashed mean line per DC-like series (|mean| > 1 % of pk-pk); legend shows mean and rms.
    Annotations = mean/rms/min/max/pk-pk per series."""
    _check_series(x, series)
    ann = {}
    with _style() as plt:
        fig, ax = _figure(plt)
        for i, (name, ys) in enumerate(series.items()):
            st = health.waveform_stats(ys)
            c = _color(i)
            span = st["max"] - st["min"]
            mean = st["mean"] if abs(st["mean"]) > 1e-9 * max(st["rms"], 1e-300) else 0.0  # float noise -> 0
            ax.plot(x, ys, color=c, label=f"{name}   mean {_g(mean)}   rms {_g(st['rms'])}", zorder=3)
            if abs(mean) > 0.01 * span:  # mean line only where it tells something (DC-like signals)
                ax.axhline(mean, color=c, ls=(0, (4, 3)), lw=0.9, alpha=0.8, zorder=2)
            ann[name] = {k: _r(v) for k, v in st.items()}
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.margins(x=0)
        _legend_above(ax, len(series))
        rows = (len(series) + 2) // 3
        ax.set_title(title or "waveform", loc="left", pad=8 + 14 * rows)
        return {"path": _save(fig, plt, path), "annotations": ann}


def _top_harmonics(amps: Sequence[float], k: int = 2) -> list[int]:
    """Orders >= 2 with the k largest amplitudes (ignoring numerically-zero ones)."""
    peak = max(amps) if amps else 0.0
    cand = [(a, o) for o, a in enumerate(amps) if o >= 2 and a > 1e-6 * peak]
    return [o for _, o in sorted(cand, reverse=True)[:k]]


def fft(series: dict, path: str, x: Sequence[float] | None = None, electrical_frequency_hz: float | None = None,
        orders: int = 15, title: str | None = None) -> dict:
    """Harmonic bar chart. The samples must span exactly one period (endpoint excluded); with x and
    electrical_frequency_hz the last electrical period is cut out first (uniform steps assumed).
    The fundamental and the two largest harmonics of the first series are labelled (order: amplitude)."""
    if not series:
        raise ValueError("no data series given")
    ann = {}
    with _style() as plt:
        fig, ax = _figure(plt)
        n = len(series)
        group = 0.45 if n == 1 else 0.8
        width = group / n
        top = 0.0
        labels = []
        for i, (name, ys) in enumerate(series.items()):
            y = list(ys)
            if x is not None and electrical_frequency_hz:
                _, y = health.last_period(x, y, 1.0 / electrical_frequency_hz)
            h = health.harmonics(y, orders)
            if not h:
                raise ValueError(f"series {name!r}: need at least 4 samples in the analysed period")
            amps = h["amplitudes"]
            off = (i - (n - 1) / 2) * width
            xs = [k + off for k in range(len(amps))]
            # white edge = the 2 px surface gap between touching bars of a group
            peak = max(amps) or 1.0
            drawn = [a if a > 1e-9 * peak else float("nan") for a in amps]  # no zero-height stubs on the axis
            ax.bar(xs, drawn, width=width, color=_color(i), edgecolor="white", linewidth=0.9 if n > 1 else 0.0,
                   label=name, zorder=3)
            top = max(top, max(amps))
            if i == 0:  # direct labels for the first series only: labels of side-by-side bars collide
                chosen = ([1] if len(amps) > 1 and amps[1] > 1e-6 * peak else []) + _top_harmonics(amps)
                labels += [(xs[o], amps[o], o) for o in chosen]
            ann[name] = {"dc": _r(h["dc"]), "fundamental": _r(h["fundamental"]), "thd_pct": _r(h.get("thd_pct")),
                         "amplitudes": [_r(a, 4) for a in amps], "samples": len(y)}
        for xb, a, o in labels:
            ax.annotate(f"{o}: {_g(a, 3)}", (xb, a), xytext=(0, 3), textcoords="offset points", ha="center",
                        va="bottom", fontsize=7, color=INK2, zorder=4)
        ax.set_ylim(0, top * 1.12 if top > 0 else 1)
        kmax = max(len(v["amplitudes"]) for v in ann.values())
        ax.set_xticks(range(kmax))
        ax.set_xticklabels(["DC"] + [str(k) for k in range(1, kmax)])
        ax.set_xlim(-0.6, kmax - 0.4)
        ax.grid(False, axis="x")
        ax.tick_params(axis="x", length=0)
        ax.set_xlabel("harmonic order (of the analysed period)")
        ax.set_ylabel("amplitude")
        if n > 1:
            _legend_above(ax, n)
        ax.set_title(title or "harmonics", loc="left", pad=22 if n > 1 else 8)
        return {"path": _save(fig, plt, path), "annotations": ann}


def probe(x: Sequence[float], y: Sequence[float | None], path: str, name: str = "", unit: str = "",
          title: str | None = None, x_label: str = "angle [deg]", bars: dict | None = None,
          bars_label: str = "electrical harmonic order", dpi: int = DPI) -> dict:
    """Field values along a probe path: one waveform panel, plus (bars = {order: amplitude}) a harmonic bar
    panel below it in the same figure. Points outside the mesh (None) are gaps. The fundamental (order 1 of
    bars) and the two largest other orders are labelled. Annotations: min/max/mean/rms of the defined points,
    points, defined, and the bars drawn."""
    if len(y) != len(x):
        raise ValueError(f"probe values have {len(y)} points but x has {len(x)}")
    ys = [float("nan") if v is None else float(v) for v in y]
    fin = [v for v in ys if math.isfinite(v)]
    ann: dict = {"points": len(ys), "defined": len(fin)}
    if fin:
        st = health.waveform_stats(fin)
        ann.update({k: _r(st[k]) for k in ("min", "max", "mean", "rms")})
    label = f"{name} [{unit}]" if unit else name
    with _style() as plt:
        if bars:
            fig, (ax, bx) = _figure(plt, FIG_W, FIG_H * 1.6, nrows=2, gridspec_kw={"height_ratios": [1.3, 1.0]})
        else:
            fig, ax = _figure(plt)
            bx = None
        ax.plot(x, ys, color=_color(0), zorder=3)
        ax.axhline(0.0, color=MUTED, lw=0.7, zorder=2)
        ax.margins(x=0)
        ax.set_xlabel(x_label)
        ax.set_ylabel(label)
        ax.set_title(title or f"{name} along the probe path", loc="left", pad=16 if fin else 8)
        if fin:
            ax.text(0.0, 1.015, f"max {_g(ann['max'])}   min {_g(ann['min'])}   rms {_g(ann['rms'])}"
                    + (f"   ({len(ys) - len(fin)} points outside the mesh)" if len(fin) < len(ys) else ""),
                    transform=ax.transAxes, ha="left", va="bottom", fontsize=7.5, color=INK2)
        if bx is not None:
            orders = sorted(int(k) for k in bars)
            amps = [float(bars[k] if k in bars else bars[str(k)]) for k in orders]
            peak = max(amps) if amps else 1.0
            bx.bar(orders, [a if a > 1e-9 * peak else float("nan") for a in amps], width=0.55, color=_color(0),
                   zorder=3)
            ranked = sorted((o for o in orders if o != 1), key=lambda o: -amps[orders.index(o)])[:2]
            for o in ([1] if 1 in orders else []) + ranked:
                a = amps[orders.index(o)]
                bx.annotate(f"{o}: {_g(a, 3)}", (o, a), xytext=(0, 3), textcoords="offset points", ha="center",
                            va="bottom", fontsize=7, color=INK2, zorder=4)
            bx.set_ylim(0, peak * 1.15 if peak > 0 else 1)
            bx.set_xticks(orders)
            bx.set_xlim(min(orders) - 0.6, max(orders) + 0.6)
            bx.grid(False, axis="x")
            bx.tick_params(axis="x", length=0)
            bx.set_xlabel(bars_label)
            bx.set_ylabel(f"amplitude [{unit}]" if unit else "amplitude")
            ann["bars"] = {str(o): _r(a, 4) for o, a in zip(orders, amps)}
        return {"path": _save(fig, plt, path, dpi), "annotations": ann}


def sweep(x: Sequence[float], series: dict, path: str, title: str | None = None, x_label: str = "",
          y_label: str = "") -> dict:
    """x vs y with markers (parameter sweeps); annotations = min/max and their x per series."""
    _check_series(x, series)
    ann = {}
    with _style() as plt:
        fig, ax = _figure(plt)
        for i, (name, ys) in enumerate(series.items()):
            ax.plot(x, ys, "-o", color=_color(i), markeredgecolor="white", markeredgewidth=1.0, label=name,
                    zorder=3, clip_on=False)
            kmax = max(range(len(ys)), key=lambda k: ys[k])
            kmin = min(range(len(ys)), key=lambda k: ys[k])
            ann[name] = {"max": _r(ys[kmax]), "x_at_max": _r(x[kmax]), "min": _r(ys[kmin]),
                         "x_at_min": _r(x[kmin]), "points": len(ys)}
        if len(series) == 1:  # one series: label its maximum directly, no legend box
            (name, ys), = series.items()
            a = ann[name]
            ax.annotate(f"max {_g(a['max'])} at {_g(a['x_at_max'])}", (a["x_at_max"], a["max"]),
                        xytext=(0, 7), textcoords="offset points", ha="center", va="bottom", fontsize=7.5,
                        color=INK2, zorder=4)
            lo, hi = min(ys), max(ys)
            ax.set_ylim(top=hi + 0.15 * ((hi - lo) or abs(hi) or 1.0))
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label if y_label or len(series) != 1 else next(iter(series)))
        if len(series) > 1:
            _legend_above(ax, len(series))
        ax.set_title(title or "sweep", loc="left", pad=22 if len(series) > 1 else 8)
        return {"path": _save(fig, plt, path), "annotations": ann}


def _ramp_cmap(colors: Sequence[str], name: str = "house_blue"):
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(name, list(colors))


def _luminance(rgba) -> float:
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgba[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def heatmap(x: Sequence[float], y: Sequence[float], z: Sequence[Sequence[float]], path: str, title: str | None = None,
            x_label: str = "", y_label: str = "", z_label: str = "", levels: Sequence[float] | None = None) -> dict:
    """2D map (e.g. efficiency over speed x torque) with filled colours and labelled contour lines.

    z[i][j] belongs to (x[j], y[i]); NaN/None cells (infeasible points) are left blank. One-hue sequential
    colour (light = low, dark = high); the best (max) point is marked and labelled.
    """
    if len(z) != len(y) or any(len(row) != len(x) for row in z):
        raise ValueError(f"z must be len(y)={len(y)} rows of len(x)={len(x)} values")
    zz = [[float("nan") if v is None else float(v) for v in row] for row in z]
    cells = [(zz[i][j], x[j], y[i]) for i in range(len(y)) for j in range(len(x)) if math.isfinite(zz[i][j])]
    if not cells:
        raise ValueError("z has no finite values")
    best, worst = max(cells), min(cells)
    with _style() as plt:
        from matplotlib.colors import Normalize
        cmap = _ramp_cmap(MAP_RAMP)
        fig, ax = _figure(plt, FIG_W, 4.6)
        ax.grid(False)
        lo, hi = worst[0], best[0]
        norm = Normalize(lo, hi if hi > lo else lo + 1e-12)
        cf = ax.contourf(x, y, zz, levels=16, cmap=cmap, norm=norm)
        cb = fig.colorbar(cf, ax=ax, label=z_label, fraction=0.04, pad=0.02, aspect=30)
        cb.outline.set_visible(False)
        cb.ax.tick_params(length=2, labelsize=7.5, color=AXIS)
        if levels:
            lv = sorted(levels)
        else:
            from matplotlib.ticker import MaxNLocator
            lv = [v for v in MaxNLocator(8).tick_values(lo, hi) if lo < v < hi]
        if lv:
            # line + label colour per level from the fill's luminance: readable on light and dark fills
            lcol = [("white" if _luminance(cmap(norm(v))) < 0.25 else INK) for v in lv]
            cs = ax.contour(x, y, zz, levels=lv, colors=lcol, linewidths=0.7, alpha=0.75)
            ax.clabel(cs, fontsize=7, inline=True, fmt=lambda v: _g(v, 4))
        ax.plot(best[1], best[2], marker="*", ms=11, mfc="white", mec=INK, mew=0.8, ls="none", zorder=5,
                clip_on=False)
        xr = (max(x) - min(x)) or 1.0
        right = best[1] > min(x) + 0.7 * xr
        ax.annotate(f"max {_g(best[0])}", (best[1], best[2]), xytext=(-8 if right else 8, 8),
                    textcoords="offset points", ha="right" if right else "left", va="bottom", fontsize=7.5,
                    color=INK, zorder=6,
                    bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none", "alpha": 0.85})
        for s in ax.spines.values():
            s.set_visible(False)
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title or "map", loc="left")
        ann = {"z_max": _r(best[0]), "at_max": [_r(best[1]), _r(best[2])], "z_min": _r(worst[0]),
               "at_min": [_r(worst[1]), _r(worst[2])], "finite_cells": len(cells),
               "blank_cells": len(x) * len(y) - len(cells)}
        return {"path": _save(fig, plt, path), "annotations": ann}


def torque_speed(speed_rpm: Sequence[float], torque_nm: Sequence[float], path: str, title: str | None = None,
                 power_w: Sequence[float] | None = None) -> dict:
    """Torque-speed envelope (top) and power (bottom, computed as T*omega if not given), sharing the speed axis.

    Annotations: max torque, base (corner) speed = highest speed with T >= 98 % of max, max power and its speed.
    """
    _check_series(speed_rpm, {"torque": torque_nm})
    if power_w is None:
        power_w = [t * n * 2 * math.pi / 60 for t, n in zip(torque_nm, speed_rpm)]
    tmax = max(torque_nm)
    base = max((n for n, t in zip(speed_rpm, torque_nm) if t >= 0.98 * tmax), default=None)
    kp = max(range(len(power_w)), key=lambda k: power_w[k])
    ann = {"max_torque_Nm": _r(tmax), "base_speed_rpm": _r(base), "max_power_W": _r(power_w[kp]),
           "speed_at_max_power_rpm": _r(speed_rpm[kp]), "max_speed_rpm": _r(max(speed_rpm))}
    with _style() as plt:
        fig, (ax, ax2) = _figure(plt, FIG_W, 5.2, nrows=2, sharex=True, gridspec_kw={"height_ratios": [1, 1]})
        ax.plot(speed_rpm, torque_nm, color=_color(0), zorder=3)
        ax.set_ylabel("torque [Nm]")
        ax.set_ylim(bottom=min(0.0, min(torque_nm)), top=tmax * 1.15 if tmax > 0 else None)
        pk = [p / 1e3 for p in power_w]
        ax2.plot(speed_rpm, pk, color=_color(1), zorder=3)
        ax2.set_ylabel("power [kW]")
        ax2.set_xlabel("speed [rpm]")
        ax2.set_ylim(bottom=min(0.0, min(pk)), top=max(pk) * 1.18 if max(pk) > 0 else None)
        ax.margins(x=0)
        xr = (max(speed_rpm) - min(speed_rpm)) or 1.0
        if base is not None:
            for a in (ax, ax2):
                a.axvline(base, color=MUTED, lw=0.8, zorder=2)
            ax.annotate(f"base {_g(base)} rpm", (base, tmax), xytext=(4, 5), textcoords="offset points",
                        ha="left", va="bottom", fontsize=7.5, color=INK2)
        right = speed_rpm[kp] > min(speed_rpm) + 0.75 * xr
        ax2.plot(speed_rpm[kp], pk[kp], "o", color=_color(1), mec="white", mew=1.0, zorder=4, clip_on=False)
        ax2.annotate(f"max {_g(pk[kp], 3)} kW at {_g(speed_rpm[kp])} rpm", (speed_rpm[kp], pk[kp]),
                     xytext=(-6 if right else 6, 6), textcoords="offset points",
                     ha="right" if right else "left", va="bottom", fontsize=7.5, color=INK2)
        ax.set_title(title or "torque-speed envelope", loc="left")
        return {"path": _save(fig, plt, path), "annotations": ann}


# ----------------------------------------------------------------------------------------------- field data

def _np():
    return fielddata.import_numpy(PlottingUnavailable)  # numpy ships with matplotlib


def _floats(np, seq, what: str):
    try:
        return np.array([np.nan if v is None else v for v in seq], dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{what}: values must be numbers ({e})") from None


def _prepare(data: dict) -> dict:
    """Validate a field dataset and convert it to numpy arrays (internal form used by field/mesh)."""
    np = _np()
    if not isinstance(data, dict):
        raise ValueError(f"field data must be a dict, got {type(data).__name__}")
    dim = data.get("dimension", 2)
    if dim != 2:
        raise ValueError(f"only 2D field data can be drawn (dimension={dim!r}); 3D needs a section first")
    for k in ("nodes", "elements", "element_part", "parts"):
        if k not in data:
            raise ValueError(f"field data has no {k!r} (needs nodes, elements, element_part, parts, values)")
    try:
        nodes = np.array(data["nodes"], dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError(f"nodes must be [[x, y], ...] numbers ({e})") from None
    if nodes.ndim != 2 or nodes.shape[0] < 3 or nodes.shape[1] < 2:
        raise ValueError(f"nodes must be a list of at least 3 [x, y] rows, got shape {list(nodes.shape)}")
    nodes = nodes[:, :2]
    if not np.all(np.isfinite(nodes)):
        raise ValueError(f"nodes: row {int(np.flatnonzero(~np.isfinite(nodes).all(1))[0])} is not finite")
    n = nodes.shape[0]
    try:
        el = np.array(data["elements"], dtype=float)
    except (TypeError, ValueError) as e:
        raise ValueError(f"elements must be [[i, j, k], ...] node indices ({e})") from None
    if el.ndim != 2 or el.shape[0] == 0 or el.shape[1] != 3:
        raise ValueError(f"elements must be a non-empty list of [i, j, k] triangles (split quads first), "
                         f"got shape {list(el.shape)}")
    if not np.all(np.isfinite(el)) or np.any(el != np.round(el)):
        raise ValueError("elements: node indices must be integers")
    tris = el.astype(np.int64)
    bad = np.flatnonzero((tris < 0).any(1) | (tris >= n).any(1))
    if bad.size:
        raise ValueError(f"elements[{int(bad[0])}] = {tris[bad[0]].tolist()} references a node outside "
                         f"0..{n - 1} ({bad.size} bad element(s); indices are 0-based)")
    e = tris.shape[0]
    parts = [str(p) for p in data["parts"]]
    if not parts:
        raise ValueError("parts is empty")
    ep = _floats(np, data["element_part"], "element_part")
    if ep.shape != (e,):
        raise ValueError(f"element_part has {ep.size} entries but there are {e} elements")
    if not np.all(np.isfinite(ep)) or np.any(ep != np.round(ep)):
        raise ValueError("element_part: entries must be integer indices into parts")
    epart = ep.astype(np.int64)
    bad = np.flatnonzero((epart < 0) | (epart >= len(parts)))
    if bad.size:
        raise ValueError(f"element_part[{int(bad[0])}] = {int(epart[bad[0]])} is not an index into parts "
                         f"(0..{len(parts) - 1})")
    values = {}
    for name, vals in (data.get("values") or {}).items():
        arr = _floats(np, vals, f"values[{name!r}]")
        if arr.ndim != 1 or arr.size not in (n, e):
            raise ValueError(f"values[{name!r}] has {arr.size} entries; expected {n} (one per node) or "
                             f"{e} (one per element)")
        values[name] = (arr, "node" if arr.size == n else "element")  # n == e: treated as nodal
    return {"nodes": nodes, "tris": tris, "epart": epart, "parts": parts, "values": values,
            "quantities": dict(data.get("quantities") or {}), "length_unit": data.get("length_unit") or "",
            "step": data.get("step"), "time": data.get("time"), "case": data.get("case")}


def validate_field_data(data: dict) -> dict:
    """Check a field dataset; raise ValueError with a clear message, else return a summary.

    Format (JSON-serialisable; what the worker's field export produces)::

        {"dimension": 2, "length_unit": "mm",
         "nodes": [[x, y], ...],              # N rows
         "elements": [[i, j, k], ...],        # E triangles, 0-based node indices (quads split by the producer)
         "element_part": [p, ...],            # len E, index into parts
         "parts": ["Stator_core", ...],
         "values": {"B": [...], "Az": [...]}, # each len N (nodal) or len E (elemental), detected by length
         "quantities": {"B": {"result_type": ..., "component": ..., "unit": "T"}},
         "step": 60, "time": 0.0198, "case": null}

    When N == E a value list is taken as nodal. Summary: {"nodes", "elements", "parts", "values":
    {name: "node"|"element"}, "bbox"}.
    """
    fd = _prepare(data)
    np = _np()
    xy = fd["nodes"]
    return {"nodes": int(xy.shape[0]), "elements": int(fd["tris"].shape[0]), "parts": fd["parts"],
            "values": {k: loc for k, (_, loc) in fd["values"].items()},
            "bbox": [float(v) for v in (*np.min(xy, 0), *np.max(xy, 0))]}


def _select(fd: dict, parts) -> object:
    """Boolean element mask for the requested part names (None = all)."""
    np = _np()
    if parts is None:
        return np.ones(fd["tris"].shape[0], dtype=bool)
    names = [parts] if isinstance(parts, str) else list(parts)
    unknown = [p for p in names if p not in fd["parts"]]
    if unknown:
        raise ValueError(f"parts {unknown} not in the field data; parts: {fd['parts']}")
    idx = [fd["parts"].index(p) for p in names]
    sel = np.isin(fd["epart"], idx)
    if not sel.any():
        raise ValueError(f"parts {names} have no elements")
    return sel


def _edges(np, tris, epart):
    """Unique triangle edges (M, 2) and a mask of outline edges: on the mesh boundary (used once) or between
    elements of different parts."""
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e.sort(axis=1)
    p = np.tile(epart, 3)
    uniq, inv, counts = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    pmin = np.full(len(uniq), np.iinfo(np.int64).max)
    pmax = np.full(len(uniq), -1)
    np.minimum.at(pmin, inv, p)
    np.maximum.at(pmax, inv, p)
    return uniq, (counts == 1) | (pmin != pmax)


def _wmean(np, v, w):
    ok = np.isfinite(v)
    if not ok.any():
        return None
    ww = w[ok]
    return float(np.sum(v[ok] * ww) / np.sum(ww)) if np.sum(ww) > 0 else float(np.mean(v[ok]))


def _nice(v: float) -> float:
    if v <= 0 or not math.isfinite(v):
        return 1.0
    e = 10 ** math.floor(math.log10(v))
    for m in (5, 2, 1):
        if m * e <= v:
            return m * e
    return e


def _field_axes(plt, bbox, extra_w: float, max_w: float = 6.0, max_h: float = 6.4):
    """Figure sized to the data aspect ratio so an equal-aspect plot has little dead space."""
    w = max(bbox[2] - bbox[0], 1e-12)
    h = max(bbox[3] - bbox[1], 1e-12)
    aw, ah = max_w, max_w * h / w
    if ah > max_h:
        aw, ah = max_h * w / h, max_h
    ah = max(ah, 2.2)
    fig, ax = _figure(plt, aw + extra_w, ah + 0.55)
    ax.set_aspect("equal")
    ax.set_axis_off()
    pad = 0.015 * max(w, h)
    ax.set_xlim(bbox[0] - pad, bbox[2] + pad)
    ax.set_ylim(bbox[1] - pad, bbox[3] + pad)
    return fig, ax


def _scale_bar(ax, bbox, unit: str) -> float:
    from matplotlib.font_manager import FontProperties
    from mpl_toolkits.axes_grid1.anchored_artists import AnchoredSizeBar
    w = bbox[2] - bbox[0]
    length = _nice(0.2 * w)
    sb = AnchoredSizeBar(ax.transData, length, f"{length:g} {unit}".strip(), "upper left",
                         bbox_to_anchor=(0.0, 0.0), bbox_transform=ax.transAxes, pad=0.1, borderpad=0.2, sep=3,
                         color=INK2, frameon=False, size_vertical=0, fontproperties=FontProperties(size=7))
    sb.size_bar.get_children()[0].set_linewidth(1.2) if sb.size_bar.get_children() else None
    ax.add_artist(sb)
    return length


def _default_title(q: str, unit: str, fd: dict) -> str:
    s = f"{q} [{unit}]" if unit else q
    if fd.get("step") is not None:
        s += f" - step {fd['step']}"
    if fd.get("time") is not None:
        s += f" (t = {_g(float(fd['time']), 4)} s)"
    if fd.get("case") is not None:
        s += f", case {fd['case']}"
    return s


def field(data: dict, path: str, quantity: str = "B", flux_lines: bool = True, flux_levels: int = 24,
          outlines: bool = True, cmap=None, vmin: float | None = None, vmax: float | None = None,
          title: str | None = None, parts: Sequence[str] | str | None = None, dpi: int = DPI,
          show_mesh: bool = False, smooth: bool = True) -> dict:
    """Contour plot of one field quantity on a 2D triangle mesh, with flux lines and part outlines.

    data: the dict described in validate_field_data. quantity: key of data["values"] (nodal values are
    shaded smoothly). smooth: elemental values are averaged onto the nodes of each part (area-weighted, so
    jumps at part boundaries stay sharp) and shaded smoothly - for the picture only: max/min/mean, per_part
    and the max marker stay elemental (annotations "values" / "shading" say so); smooth=False shades them
    flat per element. flux_lines: contour lines of "Az" (nodal, or
    elemental averaged to nodes) at flux_levels evenly spaced levels. outlines: mesh boundary + edges between
    different parts. parts: only draw (and summarise) these part names - e.g. leave out the air region.
    cmap: any matplotlib colormap name (default perceptually uniform "magma"). vmin/vmax clip the colour scale.

    Annotations: quantity, unit, max, min, mean (area-weighted over elements), at_max [x, y] (node, or element
    centroid for elemental values), part_at_max, per_part {name: {max, min, mean}} computed from element values
    (nodal values are averaged over each element's 3 nodes, so a part's max can sit below the global node max),
    nodes, elements (drawn), step, time, bbox [xmin, ymin, xmax, ymax] of the drawn parts, length_unit,
    flux_lines (levels drawn), outline_edges, scale_bar.
    """
    np = _np()
    fd = _prepare(data)
    if quantity not in fd["values"]:
        raise ValueError(f"quantity {quantity!r} not in values; values: {list(fd['values'])}")
    sel = _select(fd, parts)
    xy, tris, ep = fd["nodes"], fd["tris"][sel], fd["epart"][sel]
    vals, loc = fd["values"][quantity]
    areas = fielddata.tri_areas(xy, tris)
    used = np.unique(tris)
    if loc == "node":
        ev = vals[tris].mean(axis=1)
        nv = vals[used]
        if not np.isfinite(nv).any():
            raise ValueError(f"{quantity!r} has no finite values in the drawn parts")
        imax = int(used[np.nanargmax(nv)])
        gmax, gmin = float(np.nanmax(nv)), float(np.nanmin(nv))
        at_max = xy[imax]
        touching = np.flatnonzero((tris == imax).any(axis=1))
        tv = np.where(np.isfinite(ev[touching]), ev[touching], -np.inf)
        part_at_max = fd["parts"][int(ep[touching[int(np.argmax(tv))]])]
    else:
        ev = vals[sel]
        if not np.isfinite(ev).any():
            raise ValueError(f"{quantity!r} has no finite values in the drawn parts")
        k = int(np.nanargmax(ev))
        gmax, gmin = float(np.nanmax(ev)), float(np.nanmin(ev))
        at_max = xy[tris[k]].mean(axis=0)
        part_at_max = fd["parts"][int(ep[k])]
    per_part = {}
    for pi in sorted(set(ep.tolist())):
        m = ep == pi
        v = ev[m]
        fin = v[np.isfinite(v)]
        per_part[fd["parts"][pi]] = {"max": float(fin.max()) if fin.size else None,
                                     "min": float(fin.min()) if fin.size else None,
                                     "mean": _wmean(np, v, areas[m])}
    pxy = xy[used]
    bbox = [float(v) for v in (*pxy.min(0), *pxy.max(0))]
    qinfo = fd["quantities"].get(quantity) or {}
    unit = qinfo.get("unit") or ""
    ann = {"quantity": quantity, "unit": unit, "max": gmax, "min": gmin, "mean": _wmean(np, ev, areas),
           "at_max": [float(at_max[0]), float(at_max[1])], "part_at_max": part_at_max, "per_part": per_part,
           "nodes": int(used.size), "elements": int(tris.shape[0]), "step": fd["step"], "time": fd["time"],
           "bbox": bbox, "length_unit": fd["length_unit"], "value_location": loc, "values": loc,
           "shading": "nodal" if loc == "node" else ("nodal-averaged per part" if smooth else "flat per element")}

    with _style() as plt:
        import matplotlib.patheffects as pe
        from matplotlib.collections import LineCollection
        from matplotlib.tri import Triangulation
        fig, ax = _field_axes(plt, bbox, extra_w=1.15)
        tri = Triangulation(xy[:, 0], xy[:, 1], tris)
        lo = gmin if vmin is None else vmin
        hi = gmax if vmax is None else vmax
        if hi <= lo:
            hi = lo + (abs(lo) * 1e-6 or 1e-12)
        cm = cmap or FIELD_CMAP
        if loc == "node":
            zv = np.where(np.isfinite(vals), vals, lo)
            mappable = ax.tripcolor(tri, zv, shading="gouraud", cmap=cm, vmin=lo, vmax=hi, rasterized=True)
        elif smooth:
            sxy, stris, sv = _part_nodal_average(np, xy, tris, ep, ev, areas)
            zv = np.where(np.isfinite(sv), sv, lo)
            mappable = ax.tripcolor(Triangulation(sxy[:, 0], sxy[:, 1], stris), zv, shading="gouraud", cmap=cm,
                                    vmin=lo, vmax=hi, rasterized=True)
        else:
            mappable = ax.tripcolor(tri, facecolors=ev, cmap=cm, vmin=lo, vmax=hi, edgecolors="face",
                                    linewidth=0.15, rasterized=True)
        uniq, outl = _edges(np, tris, ep) if (outlines or show_mesh) else (None, None)
        if show_mesh:
            ax.add_collection(LineCollection(xy[uniq], colors="white", linewidths=0.25, alpha=0.3, zorder=3))
        n_levels = 0
        if flux_lines and flux_levels and "Az" in fd["values"]:
            az, azloc = fd["values"]["Az"]
            if azloc == "element":
                az = fielddata.element_to_nodal(xy.shape[0], tris, az[sel], areas)
            azu = az[used]
            if np.isfinite(azu).any():
                a0, a1 = float(np.nanmin(azu)), float(np.nanmax(azu))
                if a1 > a0:
                    levels = np.linspace(a0, a1, int(flux_levels) + 2)[1:-1]
                    azf = np.where(np.isfinite(az), az, a0)
                    cs = ax.tricontour(tri, azf, levels=levels, colors=FLUX_STYLE["color"],
                                       linewidths=FLUX_STYLE["lw"], alpha=FLUX_STYLE["alpha"],
                                       linestyles="solid", zorder=4)
                    if FLUX_STYLE.get("halo"):
                        cs.set_path_effects([pe.withStroke(linewidth=FLUX_STYLE["halo"][0],
                                                           foreground=FLUX_STYLE["halo"][1],
                                                           alpha=FLUX_STYLE["halo"][2])])
                    n_levels = len(levels)
        ann["flux_lines"] = n_levels
        if outlines:
            # butt caps: segments must not overlap, or the translucent halo shows dots at every node
            lc = LineCollection(xy[uniq[outl]], colors=INK, linewidths=0.6, zorder=5, capstyle="butt")
            # light halo under the dark outline: visible on both ends of the colour map
            lc.set_path_effects([pe.withStroke(linewidth=1.5, foreground="white", alpha=0.35, capstyle="butt")])
            ax.add_collection(lc)
            ann["outline_edges"] = int(outl.sum())
        ext = {(False, False): "neither", (True, False): "min", (False, True): "max", (True, True): "both"}[
            (vmin is not None and gmin < vmin, vmax is not None and gmax > vmax)]
        cb = fig.colorbar(mappable, ax=ax, fraction=0.045, pad=0.02, shrink=0.8, aspect=26, extend=ext)
        cb.outline.set_visible(False)
        cb.ax.tick_params(length=2, labelsize=7.5, color=AXIS, labelcolor=INK2)
        cb.set_label(f"{quantity} [{unit}]" if unit else quantity, color=INK2, fontsize=8)
        # max marker + label, offset towards the plot centre (away from the colorbar and the frame)
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        mx, my = ann["at_max"]
        right, up = mx > cx, my > cy
        ax.plot(mx, my, "o", ms=4.5, mfc="white", mec=INK, mew=0.9, zorder=7, clip_on=False)
        ax.annotate(f"max {_g(gmax, 4)} {unit}".rstrip(), (mx, my), xytext=(-9 if right else 9, -9 if up else 9),
                    textcoords="offset points", ha="right" if right else "left", va="top" if up else "bottom",
                    fontsize=7.5, color=INK, zorder=8,
                    bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "none", "alpha": 0.85})
        ann["scale_bar"] = _scale_bar(ax, bbox, fd["length_unit"])
        ax.set_title(title or _default_title(quantity, unit, fd), loc="left", fontsize=8.5, color=INK2, pad=4)
        return {"path": _save(fig, plt, path, dpi), "annotations": ann}


def _part_nodal_average(np, xy, tris, ep, ev, areas):
    """Duplicate every node once per part it touches and give it the area-weighted mean of the element values
    of that part around it. Returns (xy', tris', values per node')."""
    key = tris.astype(np.int64) * (int(ep.max()) + 1) + ep[:, None]       # (node, part) pairs
    uniq, inv = np.unique(key.reshape(-1), return_inverse=True)
    inv = inv.reshape(tris.shape)
    vals = fielddata.element_to_nodal(uniq.size, inv, ev, areas)
    return xy[uniq // (int(ep.max()) + 1)], inv, vals


def _min_angles(np, xy, tris):
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    la, lb, lc = (np.hypot(*(q - p).T) for p, q in ((b, c), (c, a), (a, b)))
    with np.errstate(invalid="ignore", divide="ignore"):
        ang = [np.arccos(np.clip((y * y + z * z - x * x) / (2 * y * z), -1, 1)) for x, y, z in
               ((la, lb, lc), (lb, lc, la), (lc, la, lb))]
    return np.degrees(np.nanmin(np.vstack(ang), axis=0))


def mesh(data: dict, path: str, title: str | None = None, parts: Sequence[str] | str | None = None,
         dpi: int = DPI) -> dict:
    """Mesh plot: thin element edges, faces tinted per part (fixed colour order, parts past the 8th grey),
    part outlines and a legend of part names. parts: only draw these part names.

    Colours follow the part's position in data["parts"] (parts 9+ share grey and one "other" legend entry).
    Annotations: nodes, elements (drawn), bbox, length_unit, per_part {name: {elements, area}} (area in
    length_unit^2), min_angle_deg (worst triangle), colours {part: hex}.
    """
    np = _np()
    fd = _prepare(data)
    sel = _select(fd, parts)
    xy, tris, ep = fd["nodes"], fd["tris"][sel], fd["epart"][sel]
    areas = fielddata.tri_areas(xy, tris)
    used = np.unique(tris)
    pxy = xy[used]
    bbox = [float(v) for v in (*pxy.min(0), *pxy.max(0))]
    present = sorted(set(ep.tolist()))
    # colour follows the part's index in data["parts"], so filtering with parts= never repaints a part
    colours = {fd["parts"][pi]: _color(pi) for pi in present}
    per_part = {fd["parts"][pi]: {"elements": int((ep == pi).sum()), "area": float(areas[ep == pi].sum())}
                for pi in present}
    angles = _min_angles(np, xy, tris)
    ann = {"nodes": int(used.size), "elements": int(tris.shape[0]), "bbox": bbox, "length_unit": fd["length_unit"],
           "per_part": per_part, "min_angle_deg": float(np.nanmin(angles)) if np.isfinite(angles).any() else None,
           "colours": colours}
    with _style() as plt:
        from matplotlib.collections import LineCollection, PolyCollection
        from matplotlib.colors import to_rgba
        from matplotlib.patches import Patch
        shown = [pi for pi in present if pi < len(SERIES_COLORS)]
        rest = [fd["parts"][pi] for pi in present if pi >= len(SERIES_COLORS)]
        other = "other: " + ", ".join(rest) if rest else ""
        if len(rest) > 4 or len(other) > 44:  # at most 12 names in the legend, and no runaway entry
            other = f"other ({len(rest)} parts)"
        labels = [fd["parts"][pi] for pi in shown] + ([other] if other else [])
        legend_w = 0.5 + 0.056 * min(48, max(len(t) for t in labels))  # inches, so the mesh keeps its size
        fig, ax = _field_axes(plt, bbox, extra_w=legend_w)
        face = np.array([to_rgba(colours[fd["parts"][p]], 0.32) for p in ep])
        ax.add_collection(PolyCollection(xy[tris], facecolors=face, edgecolors="none", zorder=2))
        uniq, outl = _edges(np, tris, ep)
        ax.add_collection(LineCollection(xy[uniq], colors=INK2, linewidths=0.25, alpha=0.55, zorder=3))
        ax.add_collection(LineCollection(xy[uniq[outl]], colors=INK, linewidths=0.7, zorder=4))
        handles = [Patch(facecolor=to_rgba(colours[fd["parts"][pi]], 0.45), edgecolor=colours[fd["parts"][pi]],
                         linewidth=0.8, label=fd["parts"][pi]) for pi in shown]
        if rest:
            handles.append(Patch(facecolor=to_rgba(OTHER_COLOR, 0.45), edgecolor=OTHER_COLOR, linewidth=0.8,
                                 label=other))
        fig.legend(handles=handles, loc="outside right upper", fontsize=7.5, handlelength=1.2, handleheight=1.0)
        ann["scale_bar"] = _scale_bar(ax, bbox, fd["length_unit"])
        default = f"mesh - {ann['elements']} elements, {ann['nodes']} nodes"
        ax.set_title(title or default, loc="left", fontsize=8.5, color=INK2, pad=4)
        return {"path": _save(fig, plt, path, dpi), "annotations": ann}


# ----------------------------------------------------------------------------------------------- dispatch

KINDS = ("waveform", "fft", "sweep", "map", "torque_speed", "field", "mesh")


def plot(kind: str, data: dict, path: str, x: str | None = None, y: list[str] | str | None = None,
         title: str | None = None, electrical_frequency_hz: float | None = None, **kwargs) -> dict:
    """Dispatch on kind with a jmag_get_result-like dict.

    data: {"x": [...], "series" | "columns": {name: [...]}}  (waveform, fft, sweep, torque_speed)
          or {"x": [...], "y": [...], "z": [[...]]}            (map)
          or the mesh/field dict of validate_field_data        (field, mesh)
    x: name of a series to use as the x axis instead of data["x"]; y: series name(s) to keep (default all).
    field: y (one name) selects the quantity unless quantity= is given; other keyword arguments go to field()
    / mesh() (flux_lines, flux_levels, outlines, cmap, vmin, vmax, parts, dpi, show_mesh).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown plot kind {kind!r}; kinds: {list(KINDS)}")
    if kind in ("field", "mesh"):
        if kind == "field" and y and "quantity" not in kwargs:
            names = [y] if isinstance(y, str) else list(y)
            if len(names) != 1:
                raise ValueError(f"field draws one quantity; got y={names}")
            kwargs["quantity"] = names[0]
        fn = field if kind == "field" else mesh
        return fn(data, path, title=title, **kwargs)
    if kwargs:
        raise ValueError(f"unexpected options {sorted(kwargs)} for kind {kind!r}")
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
