"""Pure helpers for comparing two studies and summarising the project tree (no JMAG import).

The server fetches ``study_facts`` / ``get_properties`` / result statistics for two studies; this module
flattens them into ``{key: value}`` and reports what differs, so the agent sees *why* two results differ
(stack length, mesh, step, material, turns ...) next to *how much* they differ.
"""
from __future__ import annotations

import math
from typing import Any


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


def flatten_facts(facts: dict) -> dict:
    """study_facts -> flat {"type": .., "material[Core]": .., "condition[Phase].Turn": .., "step.Step": ..}."""
    out: dict = {}
    for k in ("type", "dimension", "model_thickness", "elements", "has_mesh", "has_result", "length_unit",
              "nonlinear_max_iteration"):
        if k in facts:
            out[k] = facts[k]
    for p in facts.get("parts", []) or []:
        out[f"material[{p.get('name')}]"] = p.get("material")
    for c in facts.get("conditions", []) or []:
        key = f"condition[{c.get('name')}]"
        out[key + ".type"] = c.get("type")
        if c.get("link"):
            out[key + ".link"] = c["link"]
        for pr in c.get("properties", []) or []:
            out[f"{key}.{pr.get('name')}"] = pr.get("value")
        for sc in c.get("subconditions", []) or []:
            skey = f"{key}.sub[{sc.get('name', sc.get('index'))}]"
            out[skey + ".parts"] = sorted(sc.get("parts") or [])
            for pr in sc.get("properties", []) or []:
                out[f"{skey}.{pr.get('name')}"] = pr.get("value")
    circ = facts.get("circuit") or {}
    for comp in circ.get("components", []) or []:
        for pr in comp.get("properties", []) or []:
            out[f"circuit[{comp.get('name')}].{pr.get('name')}"] = pr.get("value")
    for k, v in (facts.get("step") or {}).items():
        out[f"step.{k}"] = v
    for k, v in (facts.get("equations") or {}).items():
        out[f"equation.{k}"] = v.get("value", v.get("expression")) if isinstance(v, dict) else v
    return out


def flatten_properties(props: list[dict], prefix: str = "study") -> dict:
    """get_properties result rows [{name, value}] -> {"study.<name>": value}."""
    return {f"{prefix}.{r.get('name')}": r.get("value") for r in props or []}


def diff_flat(a: dict, b: dict, rel_tol: float = 1e-9) -> dict:
    """Keys that differ, are only in a, or only in b. Numbers equal within rel_tol count as equal."""
    changed = []
    for k in sorted(set(a) & set(b)):
        va, vb = a[k], b[k]
        na, nb = _num(va), _num(vb)
        if na is not None and nb is not None:
            if abs(na - nb) <= rel_tol * max(abs(na), abs(nb), 1e-300):
                continue
            row = {"key": k, "a": va, "b": vb, "delta": nb - na}
            if na != 0:
                row["delta_pct"] = round(100 * (nb - na) / abs(na), 4)
            changed.append(row)
        elif va != vb:
            changed.append({"key": k, "a": va, "b": vb})
    return {"changed": changed, "only_in_a": {k: a[k] for k in sorted(set(a) - set(b))},
            "only_in_b": {k: b[k] for k in sorted(set(b) - set(a))}}


STATS = ("mean", "rms", "min", "max", "pk_pk", "ripple_pct_of_mean")


def diff_stats(a: dict, b: dict) -> list[dict]:
    """a, b: {table: {column: waveform_stats}} -> rows {table, column, stat, a, b, delta, delta_pct}.

    Only tables/columns present in both are compared; missing ones are listed by compare_results.
    """
    rows = []
    for t in sorted(set(a) & set(b)):
        for c in sorted(set(a[t]) & set(b[t])):
            for s in STATS:
                va, vb = _num(a[t][c].get(s)), _num(b[t][c].get(s))
                if va is None or vb is None:
                    continue
                row = {"table": t, "column": c, "stat": s, "a": va, "b": vb, "delta": vb - va}
                row["delta_pct"] = round(100 * (vb - va) / abs(va), 4) if va else (0.0 if vb == va else None)
                rows.append(row)
    return rows


def compare_results(a: dict, b: dict, threshold_pct: float = 1.0) -> dict:
    """Wrap diff_stats with the tables/columns that exist on one side only and the rows above threshold_pct."""
    rows = diff_stats(a, b)
    big = [r for r in rows if r["delta_pct"] is None or abs(r["delta_pct"]) >= threshold_pct]
    missing = {"tables_only_in_a": sorted(set(a) - set(b)), "tables_only_in_b": sorted(set(b) - set(a))}
    return {"rows": rows, "significant": big, "threshold_pct": threshold_pct, **missing}


# --------------------------------------------------------------------------- inventory

def inventory_flags(models: list[dict]) -> list[str]:
    """Human-readable flags for a project tree: unsolved studies, studies without conditions, empty models."""
    flags = []
    for m in models:
        if not m.get("studies"):
            flags.append(f"model {m.get('name')!r} has no studies")
        for s in m.get("studies", []):
            where = f"{m.get('name')}/{s.get('name')}"
            if not s.get("solved"):
                flags.append(f"{where}: not solved")
            if s.get("conditions") == 0:
                flags.append(f"{where}: no conditions (no excitation/motion/torque)")
            if s.get("error"):
                flags.append(f"{where}: could not be inspected ({s['error']})")
    return flags
