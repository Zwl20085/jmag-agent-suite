"""Engineering health checks: is this simulation set up right, and are the results plausible?

Pure Python (no JMAG import) so every rule is unit-testable. The worker gathers
facts (``study_facts``); this module turns facts and results into findings:

    {"level": "error"|"warning"|"info", "code": "...", "message": "...", "fix": "..."}

Levels: *error* = the run is wrong or will fail; *warning* = likely wrong or
inaccurate; *info* = context worth knowing.
"""
from __future__ import annotations

import cmath
import math
import re
from typing import Iterable, Sequence

COIL_CONDITIONS = ("Current", "FEMCoil")

# Single source of truth for the numeric limits used by the checks (and quoted in the docs).
# These are FAIL limits; healthy runs are usually far inside them (see README feedback ladder).
THRESHOLDS = {
    "kvl_rel": 1e-3,            # max |R i + dpsi/dt - v| / max|v|
    "energy_rel": 0.05,         # |1/2 sum(i psi) - W| / W   (linear-circuit identity)
    "power_balance_rel": 0.05,  # |<sum i dpsi/dt> - <T> omega| / max of the two
    "steady_rel": 0.02,         # last two periods differ by less than this
    "min_steps_per_period": 20,
}


def _f(level: str, code: str, message: str, fix: str = "") -> dict:
    d = {"level": level, "code": code, "message": message}
    if fix:
        d["fix"] = fix
    return d


def _prop(row: dict, name: str):
    for p in row.get("properties", []) or []:
        if p.get("name") == name:
            return p.get("value")
    return None


def summarize(findings: list[dict]) -> dict:
    counts = {lvl: sum(1 for f in findings if f["level"] == lvl) for lvl in ("error", "warning", "info")}
    verdict = "FAIL" if counts["error"] else ("CHECK" if counts["warning"] else "PASS")
    return {"verdict": verdict, "counts": counts, "findings": findings}


# --------------------------------------------------------------------------- preflight

def preflight(facts: dict, electrical_frequency_hz: float | None = None) -> list[dict]:
    out: list[dict] = []
    stype = facts.get("type", "")
    transient = stype.startswith("Transient")
    dim = facts.get("dimension")
    parts = facts.get("parts", [])
    conds = facts.get("conditions", [])
    circuit = facts.get("circuit") or {}
    comps = circuit.get("components", []) if circuit.get("has_circuit") else []
    id2name = {p["id"]: p["name"] for p in parts}

    # materials
    missing = [p["name"] for p in parts if not p.get("material")]
    if missing:
        out.append(_f("error", "NO_MATERIAL", f"parts without material: {missing}",
                      "assign materials with jmag_set_material (air pockets: 'Air', or suppress the part)"))

    # stack length (2D)
    if dim == 2 and facts.get("model_thickness") is not None and abs(float(facts["model_thickness"]) - 1.0) < 1e-12:
        out.append(_f("warning", "DEFAULT_STACK_LENGTH",
                      "ModelThickness is 1 (JMAG default). Torque, flux linkage and EMF scale with it.",
                      "set the real stack length: jmag_set_properties(kind='study', values={'ModelThickness': L_mm})"))

    # excitation
    coil_part_ids: set[int] = set()
    for c in conds:
        if c["type"] in COIL_CONDITIONS:
            coil_part_ids.update(c.get("parts") or [])
            for sc in c.get("subconditions", []) or []:
                coil_part_ids.update(sc.get("parts") or [])
    has_magnet = any(p.get("is_magnet") for p in parts)
    exc_types = {"Current", "FEMCoil", "FEMConductor", "CurrentDensity", "MagneticFlux"}
    if not any(c["type"] in exc_types for c in conds) and not has_magnet:
        out.append(_f("error", "NO_EXCITATION", "no current/coil condition and no magnet: the field will be zero",
                      "add jmag_add_current (static) or a circuit + jmag_add_fem_coil (transient)"))

    for c in conds:
        t = c["type"]
        if t == "Current":
            if not c.get("parts"):
                out.append(_f("error", "COND_NO_PARTS", f"Current condition '{c['name']}' has no parts"))
            turns, cur = _prop(c, "Turn"), _prop(c, "Current")
            if turns is not None and float(turns) == 1.0:
                out.append(_f("warning", "DEFAULT_TURNS", f"Current condition '{c['name']}' has Turn=1 (default)"))
            if cur is not None and float(cur) == 0.0:
                out.append(_f("warning", "ZERO_CURRENT", f"Current condition '{c['name']}' has Current=0"))
        if t in ("FEMCoil", "FEMConductor"):
            link = c.get("link")
            names = [k["name"] for k in comps]
            if not link:
                out.append(_f("error", "UNLINKED", f"{t} '{c['name']}' is not linked to a circuit component",
                              "pass component=<name> to jmag_add_fem_coil"))
            elif link not in names:
                out.append(_f("error", "BAD_LINK", f"{t} '{c['name']}' links to missing component '{link}'",
                              f"components: {names}"))
            subs = c.get("subconditions", []) or []
            for sc in subs:
                if not sc.get("parts"):
                    out.append(_f("error", "COND_NO_PARTS", f"{t} '{c['name']}' side '{sc['name']}' has no parts"))
            if t == "FEMCoil" and dim == 2 and len(subs) == 2:
                d = [_prop(sc, "Direction2D") for sc in subs]
                if d[0] is not None and d[0] == d[1]:
                    out.append(_f("warning", "SAME_DIRECTION",
                                  f"FEM coil '{c['name']}' has both sides with the same Direction2D; "
                                  "a go/return pair is normally up/down", "check the winding layout"))

    # coils should not carry eddy currents
    eddy = [p["name"] for p in parts if p["id"] in coil_part_ids and p.get("eddy_current")]
    if eddy:
        out.append(_f("warning", "COIL_EDDY", f"coil parts with eddy-current calculation on: {eddy}",
                      "stranded coils: set EddyCurrentCalculation=0 on their material"))

    # circuit sanity
    if transient and any(c["type"] in ("FEMCoil", "FEMConductor") for c in conds):
        types = [k["type"] for k in comps]
        if not any("Source" in t for t in types):
            out.append(_f("error", "NO_SOURCE", "circuit has no voltage/current source"))
        if "Ground" not in types:
            out.append(_f("warning", "NO_GROUND", "circuit has no Ground; node potentials may be undefined"))
        for k in comps:
            if k["type"] == "Coil":
                turns = _prop(k, "Turn")
                if turns is not None and float(turns) == 1.0:
                    out.append(_f("warning", "DEFAULT_TURNS", f"coil component '{k['name']}' has Turn=1 (default)"))

    # step control
    step = facts.get("step") or {}
    if transient:
        n = step.get("Step")
        if n is not None and int(n) <= 1:
            out.append(_f("error", "ONE_STEP", "transient study has only one step"))
        dt = step.get("TimeStep") if step.get("StepType") == 0 else None
        if electrical_frequency_hz and dt:
            spp = 1.0 / (float(electrical_frequency_hz) * float(dt))
            if spp < THRESHOLDS["min_steps_per_period"]:
                out.append(_f("warning", "COARSE_TIME_STEP",
                              f"only {spp:.1f} steps per electrical period", "use >= 30-60 steps/period for EMF and torque"))
            span = float(dt) * (int(n) - 1) * float(electrical_frequency_hz) if n else 0
            if span < 1.0:
                out.append(_f("warning", "SHORT_SPAN", f"simulated span is {span:.2f} electrical periods",
                              "simulate >= 1 period (more if the transient start matters)"))
        motion = [c for c in conds if c["type"] == "RotationMotion"]
        for c in motion:
            if not c.get("parts"):
                out.append(_f("error", "COND_NO_PARTS", f"RotationMotion '{c['name']}' has no parts"))
            w = _prop(c, "AngularVelocity")
            if w is not None and float(w) == 0.0 and not _prop(c, "LoadType"):
                out.append(_f("warning", "ZERO_SPEED", f"RotationMotion '{c['name']}' has AngularVelocity=0 (rotor will not move)",
                              "jmag_set_properties(kind='condition', name='<motion>', values={'AngularVelocity': rpm})"))
        if motion and not any(c["type"] in ("Torque", "Torque_Surface", "LorentzTorque") for c in conds):
            out.append(_f("info", "NO_TORQUE_COND", "rotation motion without a Torque condition: no torque table will be produced"))

    # mesh
    if facts.get("has_mesh") and facts.get("elements") is not None and facts["elements"] < 500:
        out.append(_f("warning", "COARSE_MESH", f"mesh has only {facts['elements']} elements",
                      "add local sizes (jmag_mesh_size) in air gap / thin features and compare a refined run"))
    if not facts.get("has_mesh"):
        out.append(_f("info", "NO_MESH_YET", "no mesh yet; it will be generated at run time"))
    if has_magnet:
        out.append(_f("info", "CHECK_MAGNETIZATION", "magnets present: verify the magnetization pattern with a B contour after the run"))
    return out


# --------------------------------------------------------------------------- waveforms

def finite(xs: Iterable[float]) -> bool:
    return all(isinstance(v, (int, float)) and math.isfinite(v) for v in xs)


def waveform_stats(y: Sequence[float]) -> dict:
    n = len(y)
    if n == 0:
        return {"n": 0}
    mean = sum(y) / n
    rms = math.sqrt(sum(v * v for v in y) / n)
    lo, hi = min(y), max(y)
    st = {"n": n, "mean": mean, "rms": rms, "min": lo, "max": hi, "pk_pk": hi - lo}
    if abs(mean) > 0.1 * rms:  # ripple is only meaningful for DC-like signals (torque, DC link)
        st["ripple_pct_of_mean"] = 100.0 * (hi - lo) / abs(mean)
    return st


def last_period(x: Sequence[float], y: Sequence[float], period: float) -> tuple[list[float], list[float]]:
    """Samples of the last full period, endpoint excluded (assumes uniform steps)."""
    if len(x) < 3 or period <= 0:
        return list(x), list(y)
    dt = x[1] - x[0]
    m = int(round(period / dt)) if dt > 0 else 0
    if m < 2 or m >= len(x):
        return list(x), list(y)
    return list(x[-m:]), list(y[-m:])


def harmonics(y: Sequence[float], orders: int = 15) -> dict:
    """DFT of exactly one period of samples (endpoint excluded)."""
    n = len(y)
    if n < 4:
        return {}
    amps = []
    for k in range(0, min(orders, n // 2) + 1):
        s = sum(y[i] * cmath.exp(-2j * math.pi * k * i / n) for i in range(n))
        amps.append(abs(s) / n * (1 if k == 0 else 2))
    fund = amps[1] if len(amps) > 1 else 0.0
    out = {"dc": amps[0], "fundamental": fund, "amplitudes": amps}
    # THD only means something for AC quantities (EMF, current, flux); torque is DC + 6k harmonics.
    if fund > 1e-15 and fund >= amps[0]:
        out["thd_pct"] = math.sqrt(sum(a * a for a in amps[2:])) / fund * 100
    return out


def periodicity(x: Sequence[float], y: Sequence[float], period: float) -> dict | None:
    """Compare the last period with the one before it (steady-state indicator)."""
    if len(x) < 3:
        return None
    dt = x[1] - x[0]
    m = int(round(period / dt)) if dt > 0 else 0
    if m < 2 or 2 * m > len(y):
        return None
    a, b = y[-m:], y[-2 * m:-m]
    scale = max(max(abs(v) for v in a), 1e-15)
    diff = max(abs(p - q) for p, q in zip(a, b)) / scale
    return {"max_rel_diff_last_two_periods": diff, "steady": diff < THRESHOLDS["steady_rel"]}


# --------------------------------------------------------------------------- physics invariants

def kvl_residual(t: Sequence[float], i: Sequence[float], v: Sequence[float], psi: Sequence[float],
                 resistance: float) -> dict:
    """v = R i + dpsi/dt with backward differences (JMAG uses implicit Euler).

    A small residual proves circuit polarity, connectivity and the FEM-coil link.
    """
    res = [abs(resistance * i[k] + (psi[k] - psi[k - 1]) / (t[k] - t[k - 1]) - v[k]) for k in range(1, len(t))]
    vmax = max((abs(x) for x in v), default=0.0) or 1e-15
    rel = max(res) / vmax if res else None
    return {"max_abs_residual_V": max(res) if res else None, "max_rel_residual": rel, "ok": rel is not None and rel < THRESHOLDS["kvl_rel"]}


def inductance_consistency(flux_linkages: Sequence[float], currents: Sequence[float], energy: float) -> dict:
    """Linear-circuit identity W = 1/2 sum(i_k psi_k). Holds closely unless iron saturates."""
    w_flux = 0.5 * sum(i * p for i, p in zip(currents, flux_linkages))
    rel = abs(w_flux - energy) / max(abs(energy), 1e-15)
    return {"energy_from_fields_J": energy, "energy_from_flux_J": w_flux, "rel_diff": rel,
            "ok": rel < THRESHOLDS["energy_rel"]}


def power_balance(t: Sequence[float], currents: dict, fluxes: dict, torque: Sequence[float], speed_rpm: float,
                  period: float) -> dict:
    """Electromechanical power balance over the last electrical period.

    mean(sum_k i_k dpsi_k/dt) = mean(T) * omega_mech + (eddy losses in conducting parts), because the
    stored magnetic energy returns to the same value after one period. Agreement within a few percent proves
    that torque, flux linkage, winding layout and motion are mutually consistent.
    """
    _, tt = last_period(t, list(t), period)
    m = len(tt)
    if m < 4:
        return {"ok": False, "reason": "need at least one electrical period of samples"}
    n = len(t)
    p_em = []
    for k in range(n - m, n):
        s = 0.0
        for name, i in currents.items():
            psi = fluxes[name]
            s += i[k] * (psi[k] - psi[k - 1]) / (t[k] - t[k - 1])
        p_em.append(s)
    omega = speed_rpm * 2 * math.pi / 60.0
    t_mean = sum(torque[n - m:n]) / m
    p_mech = t_mean * omega
    p_e = sum(p_em) / m
    ref = max(abs(p_e), abs(p_mech), 1e-12)
    rel = abs(p_e - p_mech) / ref
    return {"mean_torque_Nm": t_mean, "mechanical_power_W": p_mech, "electromagnetic_power_W": p_e,
            "rel_diff": rel, "ok": rel < THRESHOLDS["power_balance_rel"], "samples": m,
            "note": "difference = eddy-current losses in conducting parts + discretisation; sign: + = motoring"}


# Solver messages seen in practice, with what they mean. (pattern, level, code, explanation)
KNOWN_MESSAGES = [
    ("step control has been automatically updated", "info", "STEP_AUTO_UPDATED",
     "JMAG changed step count / time step to match the motion (slide mesh). Normal. Use the returned time axis."),
    ("calculation area of the force condition or the torque condition was extended", "info", "TORQUE_AREA_EXTENDED",
     "JMAG widened the torque integration region into the air for accuracy. Normal."),
    ("leakage inductance of fem-coil is ignored", "info", "LEAKAGE_L_IGNORED",
     "Coil leakage inductance is zero/too small and was skipped. Normal unless you meant to model end-winding inductance."),
    ("not converged", "warning", "NONLINEAR_NOT_CONVERGED",
     "Newton-Raphson hit its iteration limit at some steps. If those steps are current zero-crossings (B~0) it is "
     "usually harmless; otherwise raise NonlinearMaxIteration, refine the mesh where B changes fast, reduce the "
     "time step, try the direct solver, and check the BH curve is monotonic."),
    ("negative or zero", "error", "NEGATIVE_ELEMENT_VOLUME",
     "Distorted elements, usually moving parts on a normal mesh. Rotating machines: mesh MeshType=1 (slide), "
     "AutoSlide=1, CircumferentialDivision so one step moves an integer number of elements, and make sure no user "
     "part bridges rotor and stator (suppress an air-gap region)."),
]


def classify_message(text: str, default_level: str) -> tuple[str, str, str]:
    low = text.lower()
    for pat, level, code, why in KNOWN_MESSAGES:
        if pat in low:
            return level, code, why
    return default_level, "SOLVER_" + default_level.upper(), ""


# Messages meaning the solver did not finish (lowercase regexes). Seen on JMAG-Designer 23.0 in failed runs:
# "The solver module jam was aborted." / "The solver module mg2dnv was aborted." (run errors), "jam.exe failed to
# execute.", Designer.log "The solver module was aborted." / "fail to execute the solver.", and jam 20286
# "Size of point array is zero" (a magnet without a solver curve).
SOLVER_ABORT_PATTERNS = (
    r"solver module(?: \S+)? was aborted",
    r"\bfail(?:ed)? to execute\b",
    r"size of point array is zero",
)


def solver_abort_messages(run_env: dict, log_entries: Iterable[dict] | None = None) -> list[str]:
    """The messages of a finished run that say the solver aborted (empty list = no sign of an abort).

    Looks at the errors the run op collected (``result.errors`` or ``error.data.errors``), the JMAG messages logged
    during the run (``jmag_messages``) and the Error entries of the solver's Designer.log (``log_entries`` from
    ``solver_monitor.log_messages``). Warnings are ignored.
    """
    texts: list[str] = []
    for part in (run_env.get("result"), (run_env.get("error") or {}).get("data")):
        if isinstance(part, dict):
            texts += [str(t) for t in part.get("errors") or []]
    texts += [str(t) for t in run_env.get("jmag_messages") or []]
    texts += [str(e.get("text", "")) for e in log_entries or [] if e.get("status") == "Error"]
    out: list[str] = []
    for t in texts:
        flat = " ".join(t.split())
        if flat not in out and any(re.search(p, flat.lower()) for p in SOLVER_ABORT_PATTERNS):
            out.append(flat)
    return out


def postflight(run_info: dict, convergence: dict | None = None, tables: dict | None = None,
               electrical_frequency_hz: float | None = None, configured_steps: int | None = None) -> list[dict]:
    """run_info: has_result/errors/warnings; tables: {title: {"x": [...], "columns": {name: [...]}}}."""
    out: list[dict] = []
    if configured_steps and tables:
        rows = max((len(tb.get("x", [])) for tb in tables.values()), default=0)
        if rows and rows != int(configured_steps):
            xs = next(iter(tables.values())).get("x", [])
            dt = (xs[1] - xs[0]) if len(xs) > 1 and isinstance(xs[0], (int, float)) else None
            out.append(_f("warning", "STEPS_CHANGED",
                          f"results have {rows} rows but the study was set to {configured_steps} steps"
                          + (f" (actual time step {dt:.6g} s)" if dt else ""),
                          "JMAG adjusts the time step for slide meshes or the run stopped early; "
                          "use the returned x values, not your nominal dt, for FFTs and averages"))
    if not run_info.get("has_result"):
        out.append(_f("error", "NO_RESULT", "the study has no results",
                      "check solver errors below, jmag_dialogs, and the run log"))
    for e in run_info.get("errors", []):
        level, code, why = classify_message(e, "error")
        out.append(_f(level, code, " ".join(e.split())[:500], why))
    for w in run_info.get("warnings", []):
        level, code, why = classify_message(w, "warning")
        out.append(_f(level, code, " ".join(w.split())[:300], why))
    if convergence and convergence.get("n_steps_at_limit"):
        out.append(_f("warning", "ITERATION_LIMIT",
                      f"{convergence['n_steps_at_limit']} step(s) hit the nonlinear iteration limit: "
                      f"{convergence.get('steps_at_iteration_limit', [])[:10]}",
                      "results at those steps are less accurate"))
    period = 1.0 / electrical_frequency_hz if electrical_frequency_hz else None
    for title, tb in (tables or {}).items():
        cols = tb.get("columns", {})
        if title.lower().startswith(("circuit current", "line current")) and cols:
            # a balanced 3-phase source column is legitimately zero, so only flag an all-zero table
            if all(ys and max(abs(v) for v in ys) == 0.0 for ys in cols.values()):
                # Same intent as preflight: zero sources are a legitimate cogging / open-circuit run, so only
                # a warning, unless the flux-linkage tables are all zero too (then nothing is excited at all).
                flux_tables = [tb2.get("columns", {}) for t2, tb2 in (tables or {}).items()
                               if "flux" in t2.lower() and tb2.get("columns")]
                flux_zero = bool(flux_tables) and all(
                    all(ys2 and max(abs(v) for v in ys2) == 0.0 for ys2 in c2.values()) for c2 in flux_tables)
                out.append(_f("error" if flux_zero else "warning", "ZERO_CURRENT",
                              f"{title}: every circuit current is identically zero"
                              + (" and so is the flux linkage" if flux_zero else ""),
                              "source not connected, wrong wiring or zero amplitude; "
                              "expected for cogging-torque / open-circuit runs"))
        for col, ys in cols.items():
            if not finite(ys):
                out.append(_f("error", "NON_FINITE", f"{title} / {col} contains NaN or Inf"))
                continue
            if period and len(ys) > 4 and title.lower().startswith(("torque", "coil flux", "fem coil flux", "circuit current")):
                p = periodicity(tb["x"], ys, period)
                if p and not p["steady"]:
                    out.append(_f("info", "NOT_STEADY",
                                  f"{title} / {col}: last two periods differ by {100 * p['max_rel_diff_last_two_periods']:.1f}%",
                                  "simulate more periods or use time-periodic (TP-EEC) settings"))
    return out
