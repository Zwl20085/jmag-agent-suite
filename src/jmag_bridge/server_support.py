"""Pure helpers behind server.py: envelopes, the per-session verification record, check verdicts, physics-check
defaults, solver status without a bridge-owned job, and the dq <-> phase kinds of jmag_estimate.

Nothing here talks to JMAG; server.py feeds it facts and op results. Unit-tested in tests/test_server_support.py
and through the tools in tests/test_server_tools.py.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable

from . import experiments, fielddata, health, solver_monitor

# =========================================================================== envelope

def wrap(result: Any = None, *, ok: bool = True, error: dict | None = None, t0: float | None = None,
         **extra: Any) -> dict:
    """The standard tool envelope {ok, result | error, seconds} for tools that do not go through a worker op.
    ``seconds`` is the wall time since t0 (0.0 when t0 is None)."""
    out: dict = {"ok": bool(ok)}
    if result is not None:
        out["result"] = result
    if error:
        out["error"] = error
    out.update(extra)
    out["seconds"] = round(time.time() - t0, 3) if t0 is not None else 0.0
    return out


# =========================================================================== verification record

POST_CHECK = "post-check"
PHYSICS_CHECK = "physics-check"


class VerifyRecord:
    """Which studies had jmag_check(stage='post') and jmag_physics_check run on their current results this session.

    In memory only. Keyed by (model or "", study) inside the current project - JMAG allows the same study name in
    several models; a call without model is keyed "" (the current model). ``clear()`` runs when a project is
    opened, created or JMAG is (re)connected; ``invalidate(study, model)`` when the study is solved again (old
    checks no longer describe the new results) - model None invalidates that study name in every model, a named
    model also drops the "" entry (it may have meant that model)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], dict] = {}

    @staticmethod
    def _key(study: str, model: str | None) -> tuple[str, str]:
        return (model or "", study)

    def clear(self) -> None:
        self._rows = {}

    def invalidate(self, study: str | None, model: str | None = None) -> None:
        if not study:
            return
        # model None may be any model; a named model may also be the one recorded as "" (current model)
        drop = (lambda k: k[1] == study) if model is None else (lambda k: k[1] == study and k[0] in (model, ""))
        self._rows = {k: v for k, v in self._rows.items() if not drop(k)}

    def _row(self, study: str, model: str | None) -> dict:
        return self._rows.get(self._key(study, model)) or {"post": None, "physics": []}

    def mark_post(self, study: str, model: str | None, verdict: str | None) -> None:
        row = self._row(study, model)
        self._rows = {**self._rows, self._key(study, model): {**row, "post": {"verdict": verdict, "t": time.time()}}}

    def mark_physics(self, study: str, model: str | None, record: dict) -> None:
        row = self._row(study, model)
        self._rows = {**self._rows, self._key(study, model): {**row, "physics": [*row["physics"], dict(record)]}}

    def post(self, study: str, model: str | None = None) -> dict | None:
        return self._row(study, model)["post"]

    def physics(self, study: str, model: str | None = None) -> list[dict]:
        return list(self._row(study, model)["physics"])

    def unverified(self, study: str | None, model: str | None = None, case: int | None = None) -> list[str]:
        """What has not run on these results: [] when both checks ran. A design-table case other than the
        current one (case is not None) is never covered: both checks look at the current case only."""
        if not study or case is not None:
            return [POST_CHECK, PHYSICS_CHECK]
        row = self._row(study, model)
        return ([] if row["post"] else [POST_CHECK]) + ([] if row["physics"] else [PHYSICS_CHECK])


# =========================================================================== verdicts

def check_verdict(summary: dict) -> dict:
    """passed / verdict / hint of a health summary {verdict, counts, findings}.

    passed is True when there is no error finding (verdict PASS or CHECK); CHECK still means: read the warnings."""
    verdict = summary.get("verdict", "FAIL")
    findings = summary.get("findings") or []
    out: dict = {"passed": verdict != "FAIL", "verdict": verdict, "counts": summary.get("counts")}
    level = "error" if verdict == "FAIL" else ("warning" if verdict == "CHECK" else None)
    if level:
        fixes = []
        for f in findings:
            if f.get("level") == level:
                item = f"{f.get('code')}: {f.get('fix') or f.get('message')}"
                if item not in fixes:
                    fixes.append(item)
        out["hint"] = ("fix these before trusting any number - " if level == "error" else
                       "review these warnings before reporting numbers - ") + "; ".join(fixes[:8])
    return out


#: Likely causes of a failed invariant (from jmag-troubleshooting/references/finding-codes.md and feedback.md).
INVARIANT_HINTS = {
    "kvl": ("KVL residual too large: coil/probe pairing or probe polarity wrong (probe across the wrong nodes), "
            "the resistance passed is not the coil component's Resistance, the FEM coil is linked to another "
            "component (UNLINKED / BAD_LINK in jmag_check(stage='pre')), or the time step is too coarse for the "
            "backward difference (COARSE_TIME_STEP)"),
    "energy": ("W != 1/2 sum(i psi): iron is saturated (the identity is exact only for linear iron - a few % is "
               "normal then), a coil or Current condition is missing from the sum, magnets add energy at zero "
               "current, or the Stored Energy output is from another step"),
    "power_balance": ("power balance off: speed_rpm or electrical_frequency_hz wrong (f_e = poles/2 * rpm/60), "
                      "Torque condition not on all rotor parts, not steady state (NOT_STEADY: simulate more "
                      "periods), eddy losses in magnets/conducting parts (difference = loss), a coarse time step "
                      "(COARSE_TIME_STEP), or the winding/FEM-coil directions disagree with the circuit"),
}


def invariant_verdict(check: str, res: dict) -> dict:
    """Split a health.* invariant result: its "ok" (physics held) becomes passed / verdict / hint."""
    body = {k: v for k, v in res.items() if k != "ok"}
    passed = bool(res.get("ok"))
    body.update({"check": check, "passed": passed, "verdict": "PASS" if passed else "FAIL"})
    if not passed:
        body["hint"] = res.get("reason") or INVARIANT_HINTS.get(check, "read the numbers above")
    return body


# =========================================================================== physics-check defaults

def physics_defaults(facts: dict, check: str, coil: str | None, probe: str | None, resistance: float | None,
                     speed_rpm: float | None, f_e: float | None) -> tuple[dict, dict]:
    """Fill omitted jmag_physics_check arguments from study_facts. Explicit arguments always win.

    Returns (values, used) where used names each default and where it came from."""
    vals = {"coil": coil, "probe": probe, "resistance": resistance, "speed_rpm": speed_rpm,
            "electrical_frequency_hz": f_e}
    used: dict = {}
    circuit = facts.get("circuit") or {}
    comps = circuit.get("components", []) if circuit.get("has_circuit") else []
    if check == "kvl":
        coils = [k for k in comps if k.get("type") == "Coil"]
        probes = [k["name"] for k in comps if k.get("type") == "VoltageProbe"]
        if vals["coil"] is None and len(coils) == 1:
            vals["coil"] = coils[0]["name"]
            used["coil"] = "the study's only circuit Coil component"
        if vals["probe"] is None and vals["coil"]:
            if f"VP_{vals['coil']}" in probes:
                vals["probe"] = f"VP_{vals['coil']}"
                used["probe"] = "jmag_circuit_quick probe name VP_<coil>"
            elif len(probes) == 1:
                vals["probe"] = probes[0]
                used["probe"] = "the study's only VoltageProbe"
        if vals["resistance"] is None and vals["coil"]:
            comp = next((k for k in coils if k["name"] == vals["coil"]), None)
            r = health._prop(comp, "Resistance") if comp else None
            if r is not None:
                vals["resistance"] = float(r)
                used["resistance"] = f"Resistance of circuit component {vals['coil']!r}"
    if check == "power_balance":
        if vals["speed_rpm"] is None:
            rpm = health.motion_speed_rpm(facts)
            if rpm:
                vals["speed_rpm"] = rpm
                used["speed_rpm"] = "AngularVelocity of the RotationMotion condition"
        if vals["electrical_frequency_hz"] is None:
            d = health.derive_electrical_frequency(facts)
            if d:
                vals["electrical_frequency_hz"] = d["hz"]
                used["electrical_frequency_hz"] = d["source"]
    return vals, used


# =========================================================================== solver status without a job

def solver_status(job_folder: str | None = None,
                  list_procs: Callable[..., list] | None = None,
                  progress: Callable[[str], dict] | None = None,
                  stall_s: float = 600.0) -> dict:
    """ExecSolver/jam processes on this machine, the age of the newest jmonitor and a stall flag.

    The calc folder comes from the bridge's job, else from the ExecSolver command lines (`ExecSolver.exe
    <calc folder>/Designer.jcf`). stalled = a solver process exists and its jmonitor has not been written for
    more than stall_s seconds."""
    list_procs = list_procs or solver_monitor.solver_processes
    progress = progress or solver_monitor.progress
    procs = list_procs(None, names=solver_monitor.SOLVER_EXES)
    folders = [job_folder] if job_folder else []
    for p in procs:
        f = solver_monitor.folder_from_command(p.get("command", ""))
        if f and f not in folders:
            folders.append(f)
    ages = []
    for f in folders:
        pr = progress(f)
        if pr.get("last_write_age_s") is not None:
            ages.append((pr["last_write_age_s"], f, pr.get("steps_done")))
    newest = min(ages) if ages else None
    out = {"solver_processes": [{"pid": p["pid"], "name": p.get("name", "ExecSolver.exe")} for p in procs],
           "calc_folders": folders, "newest_monitor_age_s": newest[0] if newest else None,
           "newest_monitor_folder": newest[1] if newest else None,
           "stalled": bool(procs) and newest is not None and newest[0] > stall_s}
    return out


# =========================================================================== dq <-> phase (jmag_estimate kinds)

DQ_KINDS = {
    "dq_to_phase": {"required": ["id", "iq", "theta_e_deg"], "optional": {},
                    "doc": "Phase currents from i_d, i_q at electrical angle theta_e (amplitude-invariant Park; "
                           "d axis measured from the phase-U axis) plus jmag_circuit_quick source arguments."},
    "phase_to_dq": {"required": ["abc", "theta_e_deg"], "optional": {},
                    "doc": "d, q from three phase values [a, b, c] at electrical angle theta_e (amplitude-invariant "
                           "Park)."},
}


def _num(params: dict, name: str) -> float:
    v = params.get(name)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
        raise ValueError(f"{name} must be a finite number, got {v!r}")
    return float(v)


def dq_estimate(kind: str, params: dict | None) -> dict:
    """jmag_estimate kinds dq_to_phase / phase_to_dq (same output shape as estimate.run)."""
    p = dict(params or {})
    spec = DQ_KINDS[kind]
    allowed = set(spec["required"]) | set(spec["optional"])
    unknown, missing = sorted(set(p) - allowed), [r for r in spec["required"] if r not in p]
    if unknown or missing:
        raise ValueError(f"{kind}: " + "; ".join(x for x in (f"unknown parameters {unknown}" if unknown else "",
                                                             f"missing {missing}" if missing else "") if x)
                         + f". required={spec['required']}")
    th = _num(p, "theta_e_deg")
    if kind == "dq_to_phase":
        i_d, i_q = _num(p, "id"), _num(p, "iq")
        amp = math.hypot(i_d, i_q)
        gamma = math.degrees(math.atan2(i_q, i_d)) if amp else 0.0
        a, b, c = experiments.dq_to_abc(i_d, i_q, th)
        # i_U = I cos(theta_e + gamma) = I sin(theta_e + gamma + 90 deg)
        phase_u = (th + gamma + 90.0 + 180.0) % 360.0 - 180.0
        return {"kind": kind, "value": amp, "unit": "A (peak)",
                "formula": "i_x = i_d cos(theta - s_x) - i_q sin(theta - s_x), s = 0, +120, -120 deg; "
                           "I = hypot(i_d, i_q), gamma = atan2(i_q, i_d)",
                "assumptions": ["amplitude-invariant Park transform (same as jmag_experiment's dq helpers)",
                                "d axis at theta_e from the phase-U axis, U-V-W positive sequence",
                                "circuit_source assumes JMAG's 3-phase source is I sin(2 pi f t + PhaseU) with "
                                "theta_e(t=0) = theta_e_deg - unverified live; check the current at t=0 in "
                                "jmag_get_result"],
                "expected_fea_tolerance_pct": 0.0,
                "details": {"phase_currents_A": {"U": a, "V": b, "W": c}, "amplitude_A": amp,
                            "current_angle_from_d_deg": gamma,
                            "circuit_source": {"type": "sin", "amplitude": amp, "phase": round(phase_u, 6)}}}
    abc = p.get("abc")
    if not isinstance(abc, (list, tuple)) or len(abc) != 3:
        raise ValueError("abc must be three phase values [a, b, c]")
    vals = [_num({"v": v}, "v") for v in abc]
    d, q = experiments.park(vals, th)
    return {"kind": kind, "value": math.hypot(d, q), "unit": "same unit as abc (amplitude)",
            "formula": "d = 2/3 sum x_k cos(theta - s_k), q = -2/3 sum x_k sin(theta - s_k)",
            "assumptions": ["amplitude-invariant Park transform", "d axis at theta_e from the phase-U axis",
                            "zero-sequence part a + b + c is dropped"],
            "expected_fea_tolerance_pct": 0.0,
            "details": {"d": d, "q": q, "zero_sequence": sum(vals) / 3.0}}


# =========================================================================== pure helpers moved from server.py (0.4.0)

def _round(d: dict, n: int = 6) -> dict:
    return {k: (float(f"{v:.{n}g}") if isinstance(v, float) else v) for k, v in d.items()}


def _sig(v: Any, n: int = 6) -> Any:
    """Round floats (also inside dicts/lists) to n significant digits for compact envelopes."""
    if isinstance(v, float):
        return float(f"{v:.{n}g}") if v == v and abs(v) != float("inf") else None
    if isinstance(v, dict):
        return {k: _sig(x, n) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sig(x, n) for x in v]
    return v


def _field_quantities(names: list[str]) -> list[str]:
    out = []
    for q in names:
        fielddata.parse_quantity(q)      # raises FieldDataError with a hint
        if q not in out:
            out.append(q)
    return out


def _field_summary(data: dict) -> dict:
    return {"nodes": len(data["nodes"]), "elements": len(data["elements"]), "parts": data["parts"],
            "part_materials": data.get("part_materials"), "step": data.get("step"), "time": data.get("time"),
            "case": data.get("case"), "length_unit": data.get("length_unit"),
            "solved_steps": (data.get("source") or {}).get("solved_steps"),
            "available_steps": data.get("available_steps")}


def _visible_parts(data: dict, parts: list[str] | None, hide_air: bool) -> list[str] | None:
    """Parts to draw. An explicit `parts` list wins over hide_air (so an air part can be asked for by name);
    unknown names raise ValueError listing the valid ones."""
    used = set(data["element_part"])
    names = [p for k, p in enumerate(data["parts"]) if k in used]
    if parts is not None:
        parts = [parts] if isinstance(parts, str) else list(parts)
        unknown = [p for p in parts if p not in names]
        if unknown:
            raise ValueError(f"parts {unknown} not in the field data; parts with elements: {names}")
        return parts
    if not hide_air:
        return None
    mats = data.get("part_materials") or [""] * len(data["parts"])
    return [p for k, (p, m) in enumerate(zip(data["parts"], mats)) if k in used and (m or "").lower() != "air"]


def _probe_harmonics(values: list, pole_pairs: int | None) -> dict | None:
    if any(v is None for v in values):
        return None
    n = len(values)
    p = int(pole_pairs) if pole_pairs else None
    h = health.harmonics(values, orders=min(n // 2, max(40, 15 * (p or 1))))
    amps = h.get("amplitudes") or []
    if len(amps) < 3:
        return None
    top = sorted(range(1, len(amps)), key=lambda k: amps[k], reverse=True)[:6]
    out: dict = {"dc": h["dc"], "top_orders": [[k, amps[k]] for k in top], "order_basis": "mechanical (per revolution)"}
    if p:
        fund = amps[p] if p < len(amps) else 0.0
        el = {k // p: amps[k] for k in range(p, len(amps), p)}
        out.update({"pole_pairs": p, "fundamental": fund, "fundamental_order": p,
                    "electrical_harmonics": {str(k): a for k, a in list(el.items())[:15]}})
        if fund > 0:
            out["thd_pct"] = math.sqrt(sum(a * a for k, a in el.items() if k >= 2)) / fund * 100
        sub = [[k, amps[k]] for k in range(1, len(amps)) if k % p and amps[k] > 0.01 * fund]
        if sub:
            out["non_multiple_orders"] = sub[:8]    # sub-harmonics / non-multiples of p: asymmetry, slotting
    else:
        k1 = top[0]
        out.update({"fundamental": amps[k1], "fundamental_order": k1})
        if amps[k1] > 0:
            out["thd_pct"] = math.sqrt(sum(amps[k] ** 2 for k in range(2 * k1, len(amps), k1))) / amps[k1] * 100
    return out


def _setup_table(f: dict) -> dict:
    step = f.get("step") or {}
    circ = f.get("circuit") or {}
    comps = (circ.get("components") or []) if circ.get("has_circuit") else []
    return {
        "model": f.get("model"), "type": f.get("type"), "dimension": f.get("dimension"),
        "stack length (ModelThickness)": f.get("model_thickness"), "length unit": f.get("length_unit"),
        "mesh elements": f.get("elements"),
        "materials": {p["name"]: p.get("material") for p in f.get("parts", [])},
        "conditions": [f"{c['name']} ({c['type']})" for c in f.get("conditions", [])],
        "circuit components": [f"{k['name']} ({k['type']})" for k in comps],
        "steps": {k: step[k] for k in ("Step", "StepType", "TimeStep", "EndPoint") if k in step} or None,
        "has result": f.get("has_result"),
    }


def folder_in_project(folder: str | None, project_path: str | None) -> bool:
    """True when a solver calc folder lies inside the project's '<name>.jfiles' folder (JMAG's calc-folder layout
    <project>.jfiles/<model>~N/<study>~M/CaseK). Unknown project or folder: False (never assume ownership)."""
    if not folder or not project_path:
        return False
    proj = str(project_path).replace("\\", "/").rstrip("/")
    if not proj.lower().endswith(".jproj"):
        return False
    jfiles = (proj[: -len(".jproj")] + ".jfiles").lower()
    f = str(folder).replace("\\", "/").rstrip("/").lower()
    return f == jfiles or f.startswith(jfiles + "/")
