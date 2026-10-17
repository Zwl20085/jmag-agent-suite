"""Standard machine experiments on top of the worker ops: cogging, back-EMF, load point, static inductance.

Two layers:

* pure metric math (unit-tested with synthetic waveforms in tests/test_experiments.py): period planning,
  cogging peak-to-peak and dominant order, back-EMF fundamental/THD from flux linkage, torque ripple,
  Park transform and Ld/Lq from static flux linkages;
* ``run_experiment`` orchestration, which receives the server's ``call(op, timeout, **args)`` and
  ``solve(study, wait_seconds, electrical_frequency_hz)`` coroutines so it never imports the server.

Conventions: angles in degrees, speeds in rpm, currents as peak amplitudes [A], flux linkage [Wb],
torque [Nm]. ``poles`` is the number of poles (2p).
"""
from __future__ import annotations

import cmath
import math
from typing import Any, Awaitable, Callable, Sequence

from . import health

Call = Callable[..., Awaitable[dict]]


# =========================================================================== pure helpers

def electrical_frequency(rpm: float, poles: int) -> float:
    return rpm / 60.0 * poles / 2.0


def cogging_period_deg(slots: int, poles: int) -> float:
    """Mechanical angle of one cogging period: 360 / lcm(Q, 2p)."""
    return 360.0 / math.lcm(int(slots), int(poles))


def step_plan_for_span(rpm: float, span_mech_deg: float, steps_per_span: int) -> dict:
    """Time step and step count that cover ``span_mech_deg`` of rotation with ``steps_per_span`` intervals."""
    if rpm <= 0 or span_mech_deg <= 0 or steps_per_span < 4:
        raise ValueError("rpm > 0, span > 0 and steps_per_span >= 4 are required")
    t_span = span_mech_deg / (6.0 * rpm)          # rpm * 360/60 deg/s
    dt = t_span / steps_per_span
    return {"time_step": dt, "steps": int(steps_per_span) + 1, "end_time": t_span, "span_mech_deg": span_mech_deg}


def _fundamental_phasor(y: Sequence[float], k: int = 1) -> complex:
    n = len(y)
    return 2.0 / n * sum(y[i] * cmath.exp(-2j * math.pi * k * i / n) for i in range(n))


def _window(x: Sequence[float], y: Sequence[float], period: float) -> tuple[list[float], list[float]]:
    px, py = health.last_period(x, y, period)
    return list(px), list(py)


def cogging_metrics(t: Sequence[float], torque: Sequence[float], rpm: float, span_mech_deg: float,
                    orders: int = 12) -> dict:
    """Cogging torque over the last ``span_mech_deg`` of rotation: pk-pk, mean, dominant order per revolution."""
    _, y = _window(t, torque, span_mech_deg / (6.0 * rpm))
    if len(y) < 4:
        return {"error": "fewer than 4 samples in one cogging period"}
    h = health.harmonics(y, orders=min(orders, len(y) // 2))
    amps = h.get("amplitudes", [0.0])
    k = max(range(1, len(amps)), key=lambda j: amps[j]) if len(amps) > 1 else 0
    mean = sum(y) / len(y)
    return {"pk_pk_Nm": max(y) - min(y), "mean_Nm": mean,
            "rms_ac_Nm": math.sqrt(sum((v - mean) ** 2 for v in y) / len(y)),
            "dominant_order_per_rev": round(k * 360.0 / span_mech_deg, 3) if k else None,
            "dominant_amplitude_Nm": amps[k] if k else None,
            "harmonics_per_period_Nm": [float(f"{a:.4g}") for a in amps[1:]],
            "samples": len(y), "span_mech_deg": span_mech_deg}


def back_emf_metrics(t: Sequence[float], fluxes: dict[str, Sequence[float]], f_e: float,
                     voltages: dict[str, Sequence[float]] | None = None, orders: int = 15) -> dict:
    """Open-circuit EMF per phase from flux linkage over the last electrical period.

    E_k = k * omega_e * psi_k (exact for a periodic psi), so the EMF spectrum and THD come from psi without
    numerical differentiation. If measured terminal voltages are given for the same phases, their fundamental is
    compared with omega_e * psi_1 (the E = omega psi check)."""
    w = 2 * math.pi * f_e
    phases: dict[str, dict] = {}
    angles = {}
    for name, psi in fluxes.items():
        _, y = _window(t, psi, 1.0 / f_e)
        h = health.harmonics(y, orders=min(orders, len(y) // 2))
        if not h:
            continue
        psi_k = h["amplitudes"]
        e_k = [k * w * a for k, a in enumerate(psi_k)]
        e1 = e_k[1] if len(e_k) > 1 else 0.0
        row = {"psi1_peak_Wb": psi_k[1] if len(psi_k) > 1 else None, "E1_peak_V": e1, "E1_rms_V": e1 / math.sqrt(2),
               "thd_pct": (math.sqrt(sum(e * e for e in e_k[2:])) / e1 * 100) if e1 > 0 else None,
               "E_harmonics_peak_V": [float(f"{e:.4g}") for e in e_k[1:]]}
        angles[name] = math.degrees(cmath.phase(_fundamental_phasor(y)))
        if voltages and name in voltages:
            _, v = _window(t, voltages[name], 1.0 / f_e)
            hv = health.harmonics(v, orders=3)
            if hv:
                row["V1_measured_peak_V"] = hv["fundamental"]
                row["E_equals_omega_psi_rel_diff"] = abs(hv["fundamental"] - e1) / e1 if e1 else None
        phases[name] = row
    out: dict[str, Any] = {"electrical_frequency_Hz": f_e, "phases": phases}
    e1s = [r["E1_peak_V"] for r in phases.values() if r["E1_peak_V"]]
    if len(e1s) > 1:
        mean = sum(e1s) / len(e1s)
        out["phase_imbalance_pct"] = (max(e1s) - min(e1s)) / mean * 100 if mean else None
    if len(angles) > 1:
        names = list(angles)
        out["phase_shift_deg"] = {f"{a}->{b}": round((angles[a] - angles[b]) % 360.0, 2)
                                  for a, b in zip(names, names[1:])}
    return out


def load_point_metrics(t: Sequence[float], torque: Sequence[float], f_e: float) -> dict:
    """Torque mean, pk-pk and ripple over the last electrical period."""
    _, y = _window(t, torque, 1.0 / f_e)
    if not y:
        return {"error": "no torque samples"}
    mean = sum(y) / len(y)
    pp = max(y) - min(y)
    h = health.harmonics(y, orders=min(13, len(y) // 2)) if len(y) >= 4 else {}
    return {"mean_Nm": mean, "pk_pk_Nm": pp, "ripple_pct": pp / abs(mean) * 100 if mean else None,
            "harmonics_per_electrical_period_Nm": [float(f"{a:.4g}") for a in h.get("amplitudes", [])[1:]],
            "samples": len(y)}


_TWO_THIRDS_PI = 2 * math.pi / 3


def dq_currents(amplitude: float, theta_e_deg: float, axis: str) -> tuple[float, float, float]:
    """Phase currents (a, b, c) that put ``amplitude`` on the d or q axis at electrical angle theta_e (d axis
    measured from the phase-a axis): i_x = I cos(theta - shift) for d, -I sin(theta - shift) for q."""
    th = math.radians(theta_e_deg)
    out = []
    for shift in (0.0, _TWO_THIRDS_PI, -_TWO_THIRDS_PI):
        out.append(amplitude * (math.cos(th - shift) if axis == "d" else -math.sin(th - shift)))
    return out[0], out[1], out[2]


def park(abc: Sequence[float], theta_e_deg: float) -> tuple[float, float]:
    """Amplitude-invariant Park transform of phase quantities (a, b, c) -> (d, q)."""
    th = math.radians(theta_e_deg)
    shifts = (0.0, _TWO_THIRDS_PI, -_TWO_THIRDS_PI)
    d = 2.0 / 3.0 * sum(v * math.cos(th - s) for v, s in zip(abc, shifts))
    q = -2.0 / 3.0 * sum(v * math.sin(th - s) for v, s in zip(abc, shifts))
    return d, q


def ld_lq(psi_zero: Sequence[float], psi_d_test: Sequence[float], psi_q_test: Sequence[float], amplitude: float,
          theta_e_deg: float) -> dict:
    """Ld, Lq from three static solves (zero current, I on d, I on q) at the same rotor position."""
    d0, q0 = park(psi_zero, theta_e_deg)
    dd, _ = park(psi_d_test, theta_e_deg)
    _, qq = park(psi_q_test, theta_e_deg)
    ld = (dd - d0) / amplitude
    lq = (qq - q0) / amplitude
    return {"psi_pm_d_Wb": d0, "psi_pm_q_Wb": q0, "Ld_H": ld, "Lq_H": lq, "saliency_Lq_over_Ld": lq / ld if ld else None,
            "check": "psi_pm_q should be ~0 when theta_e really is the d-axis position (|q0| << |d0|)"}


# =========================================================================== orchestration

KINDS = ("cogging", "back_emf", "load_point", "inductance_static")


def _need(params: dict, *names: str) -> None:
    missing = [n for n in names if params.get(n) is None]
    if missing:
        raise ValueError(f"missing params {missing}")


def _ok(env: dict, what: str) -> dict:
    if not env.get("ok"):
        err = env.get("error") or {}
        raise RuntimeError(f"{what}: {err.get('message')} | hint: {err.get('hint')}")
    return env.get("result") or {}


def _estimate(params: dict) -> dict | None:
    spec = params.get("estimate")
    if not spec:
        return None
    if not isinstance(spec, dict) or "kind" not in spec:
        return {"note": "pass estimate={'kind': 'emf_constant' | 'torque_dq' | 'dq_inductance' | ..., 'params': {...}} "
                        "(see jmag_estimate) to attach an analytic expectation"}
    try:
        from . import estimate  # written alongside; optional
    except ImportError:
        return {"note": "jmag_bridge.estimate is not available in this build"}
    try:
        return estimate.run(spec["kind"], spec.get("params") or {})
    except Exception as e:  # report, don't fail the experiment
        return {"error": str(e)}


#: JMAG internal result keys -> the display names jmag_list_results reports (result-keys.md, verified 23.0)
DISPLAY_NAMES = {"FEMCoilFlux": "Coil Flux-Linkage", "LineCurrent": "Circuit Current", "TerminalVoltage": "Circuit Voltage"}


async def _list_tables(call: Call, study: str, model: str | None) -> list[dict]:
    env = await call("list_results", 120, study=study, model=model)
    if not env.get("ok"):
        return []
    return list((env.get("result") or {}).get("tables") or [])


async def _table(call: Call, study: str, names: Sequence[str], model: str | None, columns=None,
                 tables: list[dict] | None = None) -> dict | None:
    """First of ``names`` that the study really has, read with get_result.

    Picks from the list_results names instead of trying each candidate with get_result: a missing name falls
    through to ResultTable.GetDataFromName, which makes JMAG log an error (a probe)."""
    if tables is None:
        tables = await _list_tables(call, study, model)
    by_key: dict[str, dict] = {}
    for r in tables:
        for k in (r.get("name"), r.get("title")):
            if k:
                by_key.setdefault(str(k).strip().lower(), r)
    for n in names:
        r = by_key.get(str(n).strip().lower()) or by_key.get(DISPLAY_NAMES.get(n, "").lower())
        if r is None:
            continue
        env = await call("get_result", 300, study=study, table=r.get("name") or r.get("title"), columns=columns,
                         model=model)
        if env.get("ok"):
            return env["result"]
    return None


async def _prepare(call: Call, kind: str, study: str, params: dict, model: str | None) -> tuple[str, dict, dict]:
    """Duplicate the study (unless duplicate=False) and apply sources/speed/steps for the experiment."""
    poles = int(params["poles"])
    rpm = float(params.get("rpm") or (60.0 if kind == "cogging" else 1000.0))
    f_e = electrical_frequency(rpm, poles)
    if kind == "cogging":
        _need(params, "slots")
        periods = float(params.get("periods", 1))
        spp = int(params.get("steps_per_span", 90))  # samples per cogging period (analyze windows one period)
        span = cogging_period_deg(int(params["slots"]), poles) * periods
        plan = step_plan_for_span(rpm, span, int(round(spp * periods)))
        plan["steps_per_span"] = spp
        zero, amp = True, None
    else:
        periods = float(params.get("periods", 1 if kind == "back_emf" else 2))
        spp = int(params.get("steps_per_period", 60))
        plan = step_plan_for_span(rpm, 360.0 / (poles / 2) * periods, int(round(spp * periods)))
        plan["steps_per_period"] = spp
        zero = kind == "back_emf"
        amp = None if zero else float(params["current_peak"])
    target = study
    if params.get("duplicate", True):
        target = params.get("new_study") or f"{study}_{kind}"
        _ok(await call("duplicate_study", 300, study=study, new_name=target, model=model), "duplicate study")
    setup = _ok(await call("experiment_setup", 300, study=target, zero_current=zero, current_peak=amp,
                           phase_u_deg=params.get("phase_u_deg"), frequency=f_e, rpm=rpm,
                           steps=plan["steps"], time_step=plan["time_step"], source=params.get("source"),
                           motion=params.get("motion"), model=model), "experiment setup")
    return target, {"rpm": rpm, "electrical_frequency_Hz": f_e, **plan}, setup


async def analyze(call: Call, kind: str, study: str, params: dict, model: str | None = None) -> dict:
    """Post-process a solved experiment study."""
    poles = int(params["poles"])
    rpm = float(params.get("rpm") or (60.0 if kind == "cogging" else 1000.0))
    f_e = electrical_frequency(rpm, poles)
    out: dict[str, Any] = {"kind": kind, "study": study, "rpm": rpm, "electrical_frequency_Hz": f_e}
    if kind not in ("cogging", "back_emf", "load_point"):
        raise ValueError(f"analyze does not apply to {kind!r}")
    if kind == "cogging":
        _need(params, "slots")
    tables = await _list_tables(call, study, model)
    if kind == "cogging":
        tq = await _table(call, study, ["Torque"], model, tables=tables)
        if not tq:
            raise RuntimeError("no 'Torque' table: add a Torque condition on the rotor parts and re-run")
        col = params.get("torque_column") or list(tq["columns"])[0]
        span = cogging_period_deg(int(params["slots"]), poles)
        out["metrics"] = cogging_metrics(tq["x"], tq["columns"][col], rpm, span)
        out["torque_column"] = col
    elif kind == "back_emf":
        psi = await _table(call, study, ["FEMCoilFlux", "Coil Flux-Linkage"], model, tables=tables)
        if not psi:
            raise RuntimeError("no coil flux-linkage table (FEMCoilFlux): back-EMF needs FEM coils linked to a circuit")
        volt = await _table(call, study, ["TerminalVoltage", "Circuit Voltage"], model, tables=tables)
        vmap = None
        if volt:  # probes are named VP_<coil> by jmag_circuit_quick
            vmap = {c: volt["columns"][f"VP_{c}"] for c in psi["columns"] if f"VP_{c}" in volt["columns"]} or None
        out["metrics"] = back_emf_metrics(psi["x"], psi["columns"], f_e, vmap)
    elif kind == "load_point":
        tq = await _table(call, study, ["Torque"], model, tables=tables)
        if not tq:
            raise RuntimeError("no 'Torque' table")
        col = params.get("torque_column") or list(tq["columns"])[0]
        out["metrics"] = load_point_metrics(tq["x"], tq["columns"][col], f_e)
        cur = await _table(call, study, ["LineCurrent", "Circuit Current"], model, tables=tables)
        psi = await _table(call, study, ["FEMCoilFlux", "Coil Flux-Linkage"], model, tables=tables)
        if cur and psi:
            coils = [c for c in psi["columns"] if c in cur["columns"]]
            if coils:
                out["power_balance"] = health.power_balance(
                    cur["x"], {c: cur["columns"][c] for c in coils}, {c: psi["columns"][c] for c in coils},
                    tq["columns"][col], rpm, 1.0 / f_e)
    est = _estimate(params)
    if est is not None:
        out["estimate"] = est
    return out


async def _inductance(call: Call, study: str, params: dict, model: str | None) -> dict:
    """Static solves with 'Current' conditions: L = psi/I, or Ld/Lq from zero/d/q tests.

    Works on ``study`` itself (static solves are cheap; no duplicate). Every Current condition is set back to
    0 A afterwards - also when a solve or a read fails - so the study is never left energised."""
    _need(params, "current_peak")
    amp = float(params["current_peak"])
    if amp == 0:
        raise ValueError("current_peak must be non-zero")
    table = "Magnetic Flux of Current Condition"

    phases = params.get("phases")
    if not params.get("conditions") and (not phases or len(phases) != 3 or params.get("d_axis_elec_deg") is None):
        raise ValueError("inductance_static needs conditions=[...] (single coil L) or phases={'U': [conds], 'V': [...], "
                         "'W': [...]} plus d_axis_elec_deg (Ld/Lq)")
    lc = _ok(await call("list_conditions", 120, study=study, model=model), "list conditions")
    all_current = [c["name"] for c in lc.get("conditions", []) if c.get("type") == "Current"]
    if not all_current:
        raise RuntimeError(f"study {study!r} has no 'Current' conditions: add them with jmag_add_current (Static study)")
    mkw = {"model": model} if model else {}

    def target(cond: str) -> dict:
        return {"study": study, "kind": "condition", "name": cond, **mkw}

    if params.get("conditions"):
        conds = list(params["conditions"])
        missing = [c for c in conds if c not in all_current]
        if missing:
            raise ValueError(f"not Current conditions of {study!r}: {missing}; Current conditions: {all_current}")
    else:
        names = list(phases)
        theta = float(params["d_axis_elec_deg"])
        missing = [c for ph in names for c in phases[ph] if c not in all_current]
        if missing:
            raise ValueError(f"not Current conditions of {study!r}: {missing}; Current conditions: {all_current}")

    async def set_currents(currents: dict[str, float]) -> None:
        for cond, val in currents.items():
            _ok(await call("set_properties", 120, target=target(cond), values={"Current": val}),
                f"set current of {cond}")

    async def solve_with(currents: dict[str, float]) -> dict[str, float]:
        await set_currents({**{c: 0.0 for c in all_current}, **currents})  # every other coil carries 0 A
        _ok(await call("run", float(params.get("timeout_s", 3600)), study=study, overwrite=True, model=model), "static run")
        r = _ok(await call("get_result", 300, study=study, table=table, model=model), "flux of current conditions")
        return {c: v[-1] for c, v in r["columns"].items()}

    async def reset() -> list[str]:
        """Zero every Current condition; report (not raise) failures so the original error is not masked."""
        failed = []
        for c in all_current:
            env = await call("set_properties", 120, target=target(c), values={"Current": 0.0})
            if not env.get("ok"):
                failed.append(c)
        return failed

    try:
        if params.get("conditions"):  # one coil: all its Current conditions carry the same current
            psi0 = await solve_with({})
            psi = await solve_with({c: amp for c in conds})
            total = sum(psi.get(c, 0.0) for c in conds)
            base = sum(psi0.get(c, 0.0) for c in conds)
            out = {"kind": "inductance_static", "study": study, "current_A": amp,
                   "L_H": (total - base) / amp, "flux_linkage_Wb": total, "flux_linkage_at_zero_current_Wb": base,
                   "L_secant_incl_pm_H": total / amp, "per_condition_Wb": psi,
                   "note": "L = (psi(I) - psi(0)) / I: magnet flux removed; all Current conditions reset to 0 A "
                           "(the stored results are those of the I test)"}
        else:
            def per_phase(i_abc: Sequence[float]) -> dict[str, float]:
                return {c: i for ph, i in zip(names, i_abc) for c in phases[ph]}

            def phase_psi(psi: dict[str, float]) -> list[float]:
                return [sum(psi.get(c, 0.0) for c in phases[ph]) for ph in names]

            zero = phase_psi(await solve_with(per_phase((0.0, 0.0, 0.0))))
            dtest = phase_psi(await solve_with(per_phase(dq_currents(amp, theta, "d"))))
            qtest = phase_psi(await solve_with(per_phase(dq_currents(amp, theta, "q"))))
            out = None
    finally:
        failed = await reset()
    if failed:
        raise RuntimeError(f"could not reset Current conditions {failed} of {study!r} to 0 A: set them with "
                           "jmag_set_properties before using the study")
    if out is not None:
        return out
    res = ld_lq(zero, dtest, qtest, amp, theta)
    if abs(res["psi_pm_q_Wb"]) > 0.1 * abs(res["psi_pm_d_Wb"]) or res["psi_pm_d_Wb"] < 0:
        res["warning"] = ("the magnet flux is not on +d at this d_axis_elec_deg (q0 not ~0 or d0 < 0): shift the "
                          "angle by the electrical angle of the magnet flux, e.g. +-90 or 180 deg, and repeat")
    return {"kind": "inductance_static", "study": study, "current_A": amp, "d_axis_elec_deg": theta,
            "psi_abc_Wb": {"zero": zero, "d": dtest, "q": qtest}, **res,
            "note": "Current conditions reset to 0 A afterwards; the stored results are those of the q-axis test"}


async def run_experiment(kind: str, study: str, params: dict, call: Call,
                         solve: Callable[..., Awaitable[dict]], model: str | None = None,
                         action: str = "run") -> dict:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; kinds: {list(KINDS)}")
    params = dict(params or {})
    if kind == "inductance_static":
        out = await _inductance(call, study, params, model)
        est = _estimate(params)
        if est is not None:
            out["estimate"] = est
        return out
    _need(params, "poles")
    if kind == "load_point":
        _need(params, "current_peak")
    if kind == "cogging":
        _need(params, "slots")
    if action == "analyze":
        return await analyze(call, kind, study, params, model)
    target, plan, setup = await _prepare(call, kind, study, params, model)
    st = await solve(target, float(params.get("wait_seconds", 600)), plan["electrical_frequency_Hz"])
    out: dict[str, Any] = {"kind": kind, "study": target, "plan": plan, "setup": setup}
    if st.get("state") == "finished":
        out.update(await analyze(call, kind, target, params, model))
        out["run_health"] = ((st.get("post") or {}).get("health") or {}).get("verdict")
        return out
    out["run"] = {k: st.get(k) for k in ("state", "elapsed_s", "percent", "eta_s", "run", "error") if k in st}
    out["next"] = (f"poll jmag_run_status; when finished call jmag_experiment(kind={kind!r}, study={target!r}, "
                   f"params=<same params, duplicate=false>, action='analyze')")
    return out
