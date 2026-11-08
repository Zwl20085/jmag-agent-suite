"""Read a 2D DXF the way JMAG's Geometry Editor will see it: closed loops -> regions (pure Python, no JMAG).

Used by ``jmag_dxf_inspect`` for user-supplied geometry: which regions the import will create, their area,
centroid, a point strictly inside each (for ``jmag_rename_parts``), radii about the origin and layer, plus a
draft labels JSON (schema: jmag-geometry/references/labels-schema.md) whose role guesses carry a confidence.

Entities: LINE, ARC, CIRCLE, LWPOLYLINE and POLYLINE/VERTEX (bulges become arcs). Lines/arcs that end on another
line/arc (T-junctions) are split there, duplicate edges are merged, and the planar faces of the edge graph are the
regions; a loop nested inside another region (shaft bore, magnet pocket) is subtracted as a hole.
Not handled: edges that cross without an end point at the crossing, SPLINE/ELLIPSE/blocks (reported as skipped).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

ARC_SEG_DEG = 1.0          # arc sampling for areas/centroids (area error ~0.005 % of an arc sector)


class DxfReadError(ValueError):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# =========================================================================== parsing

def _pairs(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    out = []
    for k in range(0, len(lines) - 1, 2):
        try:
            out.append((int(lines[k].strip()), lines[k + 1].strip()))
        except ValueError:
            raise DxfReadError(f"line {k + 1} is not a DXF group code: {lines[k]!r}",
                               "save the drawing as ASCII DXF (R12 or later); binary DXF is not supported") from None
    return out


def _entities(pairs: list[tuple[int, str]]) -> list[tuple[str, list[tuple[int, str]]]]:
    """Entities of the ENTITIES section (whole file if there is no section) as (type, [(code, value), ...])."""
    start, end = 0, len(pairs)
    for k, (c, v) in enumerate(pairs):
        if c == 2 and v == "ENTITIES" and k > 0 and pairs[k - 1] == (0, "SECTION"):
            start = k + 1
            for j in range(start, len(pairs)):
                if pairs[j] == (0, "ENDSEC"):
                    end = j
                    break
            break
    ents: list[tuple[str, list[tuple[int, str]]]] = []
    for c, v in pairs[start:end]:
        if c == 0:
            ents.append((v, []))
        elif ents:
            ents[-1][1].append((c, v))
    return ents


def _get(data: list[tuple[int, str]], code: int, default: float | str | None = None) -> Any:
    for c, v in data:
        if c == code:
            return v
    return default


def _bulge_arc(a: tuple, b: tuple, bulge: float) -> tuple | None:
    """Arc primitive for a polyline segment a->b with bulge = tan(theta/4) (positive = CCW)."""
    if abs(bulge) < 1e-12:
        return None
    theta = 4 * math.atan(bulge)
    chord = math.hypot(b[0] - a[0], b[1] - a[1])
    if chord == 0:
        return None
    r = chord / (2 * math.sin(abs(theta) / 2))
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    d = math.sqrt(max(r * r - (chord / 2) ** 2, 0.0))
    ux, uy = -(b[1] - a[1]) / chord, (b[0] - a[0]) / chord      # left normal of a->b
    sgn = 1.0 if (bulge > 0) == (abs(theta) < math.pi) else -1.0
    cx, cy = mx + sgn * d * ux, my + sgn * d * uy
    s, e = (a, b) if bulge > 0 else (b, a)                       # store CCW
    return ("arc", (cx, cy), r, math.degrees(math.atan2(s[1] - cy, s[0] - cx)),
            math.degrees(math.atan2(e[1] - cy, e[0] - cx)))


def _poly_prims(pts: list[tuple], bulges: list[float], closed: bool, layer: str) -> list[tuple]:
    out = []
    n = len(pts)
    segs = n if closed else n - 1
    for k in range(segs):
        a, b = pts[k], pts[(k + 1) % n]
        arc = _bulge_arc(a, b, bulges[k] if k < len(bulges) else 0.0)
        out.append((arc + (layer,)) if arc else ("line", a, b, layer))
    return out


def _extrusion(d: list[tuple[int, str]]) -> str:
    """'z' (default 0,0,1), 'mirror' (0,0,-1: the entity's OCS has x -> -x) or 'other' (a general extrusion)."""
    nx, ny, nz = (float(_get(d, c, dflt)) for c, dflt in ((210, 0.0), (220, 0.0), (230, 1.0)))
    if abs(nx) < 1e-9 and abs(ny) < 1e-9:
        return "z" if nz > 0 else "mirror"
    return "other"


def _mirror(p: tuple) -> tuple:
    """x -> -x of a primitive (OCS with extrusion 0,0,-1 seen from +z); arcs stay CCW."""
    if p[0] == "line":
        return ("line", (-p[1][0], p[1][1]), (-p[2][0], p[2][1]), p[3])
    sw = _sweep(p[3], p[4])
    a0 = 180.0 - (p[3] + sw)
    return ("arc", (-p[1][0], p[1][1]), p[2], a0, a0 + sw, p[5])


def header_units(text: str) -> tuple[str, int | None]:
    """$INSUNITS of the HEADER: ('mm', 4), ('in', 1), else ('unknown', code or None)."""
    pairs = _pairs(text)
    for k, (c, v) in enumerate(pairs[:-1]):
        if c == 9 and v == "$INSUNITS" and pairs[k + 1][0] == 70:
            try:
                code = int(float(pairs[k + 1][1]))
            except ValueError:
                return "unknown", None
            return {4: "mm", 1: "in"}.get(code, "unknown"), code
    return "unknown", None


def parse(text: str) -> tuple[list[tuple], dict]:
    """DXF text -> primitives [("line", a, b, layer) | ("arc", c, r, a0_deg, a1_deg, layer)] (arcs CCW), skipped.

    ARC / CIRCLE / LWPOLYLINE / POLYLINE coordinates are in the entity's OCS: extrusion (0, 0, -1) (210/220/230)
    is mirrored (x -> -x, bulges negated); any other non-Z extrusion is skipped and counted in `skipped` as
    '<TYPE> (non-Z extrusion)'. LINE end points are WCS."""
    ents = _entities(_pairs(text))
    prims: list[tuple] = []
    skipped: Counter = Counter()
    k = 0
    while k < len(ents):
        typ, d = ents[k]
        layer = str(_get(d, 8, "0"))
        f = lambda code, dflt=0.0: float(_get(d, code, dflt))  # noqa: E731
        ext = _extrusion(d) if typ in ("ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE") else "z"
        new: list[tuple] = []
        if ext == "other":
            skipped[f"{typ} (non-Z extrusion)"] += 1
            if typ == "POLYLINE":
                k += 1
                while k < len(ents) and ents[k][0] == "VERTEX":
                    k += 1
                continue
        elif typ == "LINE":
            new.append(("line", (f(10), f(20)), (f(11), f(21)), layer))
        elif typ == "ARC":
            new.append(("arc", (f(10), f(20)), f(40), f(50), f(51), layer))
        elif typ == "CIRCLE":
            new.append(("arc", (f(10), f(20)), f(40), 0.0, 360.0, layer))
        elif typ == "LWPOLYLINE":
            pts, bul = [], []
            for c, v in d:
                if c == 10:
                    pts.append([float(v), 0.0])
                    bul.append(0.0)
                elif c == 20 and pts:
                    pts[-1][1] = float(v)
                elif c == 42 and bul:
                    bul[-1] = float(v)
            closed = bool(int(float(_get(d, 70, 0))) & 1)
            new += _poly_prims([tuple(p) for p in pts], bul, closed, layer)
        elif typ == "POLYLINE":
            closed = bool(int(float(_get(d, 70, 0))) & 1)
            pts, bul = [], []
            k += 1
            while k < len(ents) and ents[k][0] == "VERTEX":
                vd = ents[k][1]
                pts.append((float(_get(vd, 10, 0.0)), float(_get(vd, 20, 0.0))))
                bul.append(float(_get(vd, 42, 0.0)))
                k += 1
            new += _poly_prims(pts, bul, closed, layer)
            prims += [_mirror(p) for p in new] if ext == "mirror" else new
            continue  # k already at SEQEND (or the next entity)
        elif typ not in ("SEQEND", "VERTEX"):
            skipped[typ] += 1
        prims += [_mirror(p) for p in new] if ext == "mirror" else new
        k += 1
    return prims, dict(skipped)


# =========================================================================== geometry helpers

def _arc_pt(c: tuple, r: float, deg: float) -> tuple:
    return (c[0] + r * math.cos(math.radians(deg)), c[1] + r * math.sin(math.radians(deg)))


def _sweep(a0: float, a1: float) -> float:
    s = (a1 - a0) % 360.0
    return 360.0 if s < 1e-9 else s


def _ends(p: tuple) -> tuple[tuple, tuple]:
    if p[0] == "line":
        return p[1], p[2]
    return _arc_pt(p[1], p[2], p[3]), _arc_pt(p[1], p[2], p[3] + _sweep(p[3], p[4]))


def _param(p: tuple, q: tuple, tol: float) -> float | None:
    """Parameter 0..1 of point q on primitive p if q lies on it (within tol), else None."""
    if p[0] == "line":
        (ax, ay), (bx, by) = p[1], p[2]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        if L2 == 0:
            return None
        t = ((q[0] - ax) * dx + (q[1] - ay) * dy) / L2
        px, py = ax + t * dx, ay + t * dy
        return t if math.hypot(q[0] - px, q[1] - py) <= tol else None
    c, r, a0 = p[1], p[2], p[3]
    if abs(math.hypot(q[0] - c[0], q[1] - c[1]) - r) > tol:
        return None
    ang = math.degrees(math.atan2(q[1] - c[1], q[0] - c[0]))
    return ((ang - a0) % 360.0) / _sweep(a0, p[4])


def _sub(p: tuple, t0: float, t1: float) -> tuple:
    if p[0] == "line":
        (ax, ay), (bx, by) = p[1], p[2]
        return ("line", (ax + t0 * (bx - ax), ay + t0 * (by - ay)), (ax + t1 * (bx - ax), ay + t1 * (by - ay)), p[3])
    sw = _sweep(p[3], p[4])
    return ("arc", p[1], p[2], p[3] + t0 * sw, p[3] + t1 * sw, p[5])


def _samples(p: tuple) -> list[tuple]:
    if p[0] == "line":
        return [p[1], p[2]]
    sw = _sweep(p[3], p[4])
    n = max(2, int(math.ceil(sw / ARC_SEG_DEG)))
    return [_arc_pt(p[1], p[2], p[3] + sw * k / n) for k in range(n + 1)]


def _mid(p: tuple) -> tuple:
    """Geometric midpoint of a primitive (dedup key of shared edges drawn twice)."""
    if p[0] == "line":
        return ((p[1][0] + p[2][0]) / 2, (p[1][1] + p[2][1]) / 2)
    return _arc_pt(p[1], p[2], p[3] + _sweep(p[3], p[4]) / 2)


def _area_centroid(poly: list[tuple]) -> tuple[float, float, float]:
    a = cx = cy = 0.0
    n = len(poly)
    for k in range(n):
        x0, y0 = poly[k]
        x1, y1 = poly[(k + 1) % n]
        w = x0 * y1 - x1 * y0
        a += w
        cx += (x0 + x1) * w
        cy += (y0 + y1) * w
    a /= 2
    return (a, cx / (6 * a), cy / (6 * a)) if a else (0.0, poly[0][0], poly[0][1])


def _inside(pt: tuple, poly: list[tuple]) -> bool:
    x, y = pt
    inside = False
    n = len(poly)
    for k in range(n):
        x0, y0 = poly[k]
        x1, y1 = poly[(k + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


# =========================================================================== regions

def _snap(points: list[tuple], tol: float) -> tuple[list[tuple], list[int]]:
    """Merge points closer than tol; returns (nodes, node index per input point)."""
    grid: dict[tuple[int, int], list[int]] = {}
    nodes: list[tuple] = []
    idx = []
    for p in points:
        gx, gy = int(math.floor(p[0] / tol)), int(math.floor(p[1] / tol))
        hit = None
        for ix in (gx - 1, gx, gx + 1):
            for iy in (gy - 1, gy, gy + 1):
                for n in grid.get((ix, iy), []):
                    if math.hypot(nodes[n][0] - p[0], nodes[n][1] - p[1]) <= tol:
                        hit = n
                        break
                if hit is not None:
                    break
            if hit is not None:
                break
        if hit is None:
            hit = len(nodes)
            nodes.append(p)
            grid.setdefault((gx, gy), []).append(hit)
        idx.append(hit)
    return nodes, idx


def _split(prims: list[tuple], tol: float) -> list[tuple]:
    """Split primitives at end points of other primitives lying on them (T-junctions)."""
    ends = [q for p in prims for q in _ends(p)]
    out = []
    for p in prims:
        sm = _samples(p)
        pad = tol + (p[2] * (1 - math.cos(math.radians(ARC_SEG_DEG))) if p[0] == "arc" else 0.0)  # chord sag
        lo = (min(q[0] for q in sm) - pad, min(q[1] for q in sm) - pad)
        hi = (max(q[0] for q in sm) + pad, max(q[1] for q in sm) + pad)
        ts = sorted({t for q in ends if lo[0] <= q[0] <= hi[0] and lo[1] <= q[1] <= hi[1]
                     for t in [_param(p, q, tol)] if t is not None and 1e-9 < t < 1 - 1e-9})
        cuts = [0.0] + ts + [1.0]
        out += [_sub(p, a, b) for a, b in zip(cuts, cuts[1:]) if b - a > 1e-12]
    return out


def _faces(prims: list[tuple], tol: float) -> tuple[list[dict], int]:
    """Planar faces of the edge graph: [{"poly", "area" (signed), "layers"}], plus the count of open ends."""
    edges = []
    pts = []
    for p in prims:
        s = _samples(p)
        edges.append((s, p[-1], _mid(p)))
        pts += [s[0], s[-1]]
    nodes, idx = _snap(pts, tol)
    seen = set()
    half: list[tuple[int, int, list[tuple], str]] = []      # (from, to, samples, layer)
    for k, (s, layer, mid) in enumerate(edges):
        u, v = idx[2 * k], idx[2 * k + 1]
        key = (min(u, v), max(u, v), round(mid[0] / tol), round(mid[1] / tol))
        if key in seen or (u == v and len(s) <= 2):
            continue
        seen.add(key)
        s = [nodes[u]] + s[1:-1] + [nodes[v]]
        half.append((u, v, s, layer))
        half.append((v, u, s[::-1], layer))
    out_at: dict[int, list[int]] = {}
    for h, (u, _, s, _) in enumerate(half):
        out_at.setdefault(u, []).append(h)
    ang = {h: math.atan2(half[h][2][1][1] - half[h][2][0][1], half[h][2][1][0] - half[h][2][0][0]) for h in range(len(half))}
    for u in out_at:
        out_at[u].sort(key=lambda h: ang[h])
    open_ends = sum(1 for u, hs in out_at.items() if len(hs) == 1)
    used = [False] * len(half)
    faces = []
    for h0 in range(len(half)):
        if used[h0]:
            continue
        poly, layers, h = [], [], h0
        for _ in range(len(half) + 1):
            if used[h]:
                break
            used[h] = True
            u, v, s, layer = half[h]
            poly += s[:-1]
            layers.append(layer)
            twin = h ^ 1
            ring = out_at[v]
            h = ring[(ring.index(twin) - 1) % len(ring)]
        if len(poly) >= 3:
            a, cx, cy = _area_centroid(poly)
            faces.append({"poly": poly, "area": a, "centroid": (cx, cy), "layers": layers})
    return faces, open_ends


def _interior_point(outer: list[tuple], holes: list[list[tuple]], guess: tuple) -> tuple:
    def ok(p: tuple) -> bool:
        return _inside(p, outer) and not any(_inside(p, h) for h in holes)

    if ok(guess):
        return guess
    ys = sorted(q[1] for q in outer)
    best, width = guess, -1.0
    for frac in (0.5, 0.3, 0.7, 0.15, 0.85, 0.4, 0.6):
        y = ys[0] + (ys[-1] - ys[0]) * frac
        xs = []
        for poly in [outer] + holes:
            n = len(poly)
            for k in range(n):
                (x0, y0), (x1, y1) = poly[k], poly[(k + 1) % n]
                if (y0 > y) != (y1 > y):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for a, b in zip(xs, xs[1:]):
            m = ((a + b) / 2, y)
            if b - a > width and ok(m):
                best, width = m, b - a
    return best


def regions(prims: list[tuple], tol: float | None = None) -> tuple[list[dict], dict]:
    """Regions JMAG would create: [{id, area_mm2, centroid, point, r_min, r_max, layer, holes}], diagnostics."""
    if not prims:
        return [], {"open_ends": 0}
    allp = [q for p in prims for q in _samples(p)]
    ext = max(max(q[0] for q in allp) - min(q[0] for q in allp), max(q[1] for q in allp) - min(q[1] for q in allp))
    tol = tol or max(1e-9, 1e-7 * ext)
    split = _split(prims, tol * 10)
    faces, open_ends = _faces(split, tol * 10)
    area_tol = (ext * ext) * 1e-12
    bounded = [f for f in faces if f["area"] > area_tol]
    outers = [f for f in faces if f["area"] < -area_tol]       # outer boundary of each connected component
    holes: dict[int, list[dict]] = {i: [] for i in range(len(bounded))}
    for o in outers:
        probe = o["poly"][0]
        # the component's own faces share this vertex; a containing face of another component strictly contains it
        own = {id(f) for f in bounded if any(math.hypot(q[0] - probe[0], q[1] - probe[1]) <= tol * 10 for q in f["poly"])}
        parents = [(f["area"], i) for i, f in enumerate(bounded) if id(f) not in own and _inside(probe, f["poly"])]
        if parents:
            holes[min(parents)[1]].append(o)
    out = []
    for i, f in enumerate(bounded):
        a, cx, cy = f["area"], f["centroid"][0] * f["area"], f["centroid"][1] * f["area"]
        for h in holes[i]:
            ha = -h["area"]
            a -= ha
            cx -= h["centroid"][0] * ha
            cy -= h["centroid"][1] * ha
        c = (cx / a, cy / a) if a > 0 else f["centroid"]
        pt = _interior_point(f["poly"], [h["poly"] for h in holes[i]], c)
        rs = [math.hypot(q[0], q[1]) for q in f["poly"] + [v for h in holes[i] for v in h["poly"]]]
        contains_origin = _inside((0.0, 0.0), f["poly"]) and not any(_inside((0.0, 0.0), h["poly"]) for h in holes[i])
        out.append({"id": len(out) + 1, "area_mm2": a, "centroid": [c[0], c[1]], "point": [pt[0], pt[1]],
                    "r_min": 0.0 if contains_origin else min(rs), "r_max": max(rs),
                    "angle_deg": math.degrees(math.atan2(c[1], c[0])),
                    "layer": Counter(f["layers"]).most_common(1)[0][0], "holes": len(holes[i]),
                    "contains_origin": contains_origin})
    return out, {"open_ends": open_ends, "tolerance": tol * 10, "edges": len(split)}


# =========================================================================== role guesses

_LAYER_ROLES = (("magnet", "magnet"), ("mag", "magnet"), ("pm", "magnet"), ("slot", "slot"), ("coil", "slot"),
                ("wind", "slot"), ("stator", "stator"), ("rotor", "rotor"), ("shaft", "shaft"), ("air", "air"),
                ("gap", "air"))


def _layer_role(layer: str) -> str | None:
    low = layer.lower()
    for key, role in _LAYER_ROLES:
        if key in low:
            return role
    return None


def classify(regs: list[dict]) -> None:
    """Add guess and confidence (0..1) to each region, in place. Machine cross-sections about the origin (a
    stator-like ring reaching the outermost radius exists): 'stator-like' | 'rotor-like' | 'slot-like' |
    'magnet-like' | 'airgap-like' | 'shaft-like'; otherwise (inductors, actuators): 'core-like' (largest region) |
    'coil-like' (groups of equal regions). 'unknown' for the rest. Layer names (magnet, slot, coil, stator, rotor,
    shaft, air, gap) confirm or override a guess."""
    if not regs:
        return
    rel = lambda a, b: abs(a - b) <= 0.01 * max(abs(a), abs(b), 1e-12)  # noqa: E731
    groups: list[list[dict]] = []
    r_out = max(r["r_max"] for r in regs)
    machine = any(r["r_max"] >= 0.98 * r_out and r["r_min"] > 0.2 * r_out for r in regs)
    for r in sorted(regs, key=lambda r: r["area_mm2"]):
        if groups and rel(groups[-1][0]["area_mm2"], r["area_mm2"]) and (
                not machine or rel(groups[-1][0]["r_max"], r["r_max"])):
            groups[-1].append(r)
        else:
            groups.append([r])
    rep = {id(r): len(g) for g in groups for r in g}
    for r in regs:
        r.update(guess="unknown", confidence=0.2)
    singles = [r for r in regs if rep[id(r)] == 1]
    stator = max((r for r in singles if r["r_max"] >= 0.98 * r_out and r["r_min"] > 0.2 * r_out),
                 key=lambda r: r["area_mm2"], default=None)
    if stator:
        stator.update(guess="stator-like", confidence=0.7)
        _classify_machine(regs, groups, singles, stator)
    else:  # not a machine cross-section about the origin (inductor, transformer, actuator)
        core = max(regs, key=lambda r: r["area_mm2"])
        core.update(guess="core-like", confidence=0.4)
        for g in groups:
            if len(g) >= 2:
                for r in g:
                    r.update(guess="coil-like", confidence=0.4)
    _apply_layers(regs)


def _classify_machine(regs: list[dict], groups: list[list[dict]], singles: list[dict], stator: dict) -> None:
    bore = stator["r_min"]
    inner = [r for r in singles if r is not stator and r["r_max"] <= bore * 1.001]
    rotor = max((r for r in inner if not (r["r_min"] > 0.9 * r["r_max"])), key=lambda r: r["area_mm2"], default=None)
    if rotor:
        rotor.update(guess="rotor-like", confidence=0.6)
    rotor_out = rotor["r_max"] if rotor else 0.0
    for r in inner:
        if r is rotor:
            continue
        ring = r["holes"] > 0 or r["r_min"] > 0.85 * r["r_max"]
        if ring and r["r_min"] >= rotor_out * 0.98 and r["r_max"] <= bore * 1.001 and r["contains_origin"] is False:
            r.update(guess="airgap-like", confidence=0.5)
        elif r["contains_origin"] or (rotor and r["r_max"] <= rotor["r_min"] * 1.001):
            r.update(guess="shaft-like", confidence=0.5)
    for g in groups:
        if len(g) < 2:
            continue
        r0 = g[0]
        if r0["r_min"] >= bore * 0.999 and r0["r_max"] <= stator["r_max"]:
            role, conf = "slot-like", 0.6
        elif r0["r_max"] <= bore * 1.001 and r0["r_min"] >= 0.5 * rotor_out:
            role, conf = "magnet-like", 0.5
        else:
            role, conf = "unknown", 0.2
        for r in g:
            r.update(guess=role, confidence=conf)


def _apply_layers(regs: list[dict]) -> None:
    for r in regs:  # layer names confirm or override a geometric guess
        lr = _layer_role(r["layer"])
        if not lr:
            continue
        want = {"stator": "stator-like", "rotor": "rotor-like", "slot": "slot-like", "magnet": "magnet-like",
                "shaft": "shaft-like", "air": "airgap-like"}[lr]
        r["confidence"] = min(0.9, r["confidence"] + 0.2) if r["guess"] == want else 0.6
        r["guess"] = want


def _names(regs: list[dict]) -> None:
    by_angle = lambda rs: sorted(rs, key=lambda r: r["angle_deg"] % 360.0)  # noqa: E731
    base = {"stator-like": "StatorCore", "rotor-like": "RotorCore", "airgap-like": "GapAir", "shaft-like": "Shaft",
            "core-like": "Core"}
    count: Counter = Counter()
    for r in regs:
        if r["guess"] in base:
            count[r["guess"]] += 1
            n = count[r["guess"]]
            r["name"] = base[r["guess"]] + ("" if n == 1 else str(n))
    for guess, stem in (("slot-like", "Slot%02d"), ("magnet-like", "Magnet%d"), ("coil-like", "Coil%d")):
        for k, r in enumerate(by_angle([r for r in regs if r["guess"] == guess]), start=1):
            r["name"] = stem % k
    for r in regs:
        r.setdefault("name", f"Region{r['id']}")


def inspect(path: str) -> dict:
    """Regions, role guesses and a draft labels JSON for one DXF file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as e:
        raise DxfReadError(f"cannot read {path}: {e}", "pass the absolute path of an existing .dxf file") from None
    prims, skipped = parse(text)
    units, units_code = header_units(text)
    if not prims:
        raise DxfReadError(f"{path} has no LINE/ARC/CIRCLE/LWPOLYLINE/POLYLINE entities (skipped: {skipped})",
                           "explode blocks / convert splines to polylines in the CAD tool and export again")
    regs, diag = regions(prims)
    classify(regs)
    _names(regs)
    warnings = []
    if diag["open_ends"]:
        warnings.append(f"{diag['open_ends']} open end point(s): edges that do not close a loop create no region")
    if skipped:
        warnings.append(f"entities not read: {skipped} (splines/ellipses/blocks: convert to polylines)")
    if not regs:
        warnings.append("no closed region found")
    if units == "in":
        warnings.append("$INSUNITS says inches, but jmag_import_dxf reads the numbers in the model length unit (mm by "
                        "default): scale the drawing by 25.4 or confirm the unit; areas here are in drawing units^2")
    elif units == "unknown":
        warnings.append(f"drawing units unknown ($INSUNITS {'missing' if units_code is None else units_code}): the "
                        "numbers are read as the model length unit (mm by default); areas here are in drawing units^2")
    rnd = lambda v: float(f"{v:.6g}")  # noqa: E731
    rows = [{"id": r["id"], "name": r["name"], "guess": r["guess"], "confidence": r["confidence"],
             "area_mm2": rnd(r["area_mm2"]), "centroid": [rnd(v) for v in r["centroid"]],
             "point": [rnd(v) for v in r["point"]], "r_min": rnd(r["r_min"]), "r_max": rnd(r["r_max"]),
             "layer": r["layer"], "holes": r["holes"]} for r in regs]
    magnets = sorted((r for r in regs if r["guess"] == "magnet-like"), key=lambda r: r["angle_deg"] % 360.0)
    rotor_parts = [r["name"] for r in regs if r["guess"] in ("rotor-like", "magnet-like", "shaft-like")]
    labels: dict[str, Any] = {
        "dxf": str(path), "units": units, "draft": True,
        "parts": [{"name": r["name"], "point": r["point"], "confidence": r["confidence"]} for r in rows],
        "magnets": [{"name": r["name"], "center_deg": rnd(r["angle_deg"] % 360.0),
                     "polarity": "outward" if k % 2 == 0 else "inward", "confidence": 0.3}
                    for k, r in enumerate(magnets)],
        "rotor_parts": rotor_parts,
        "suppress": [r["name"] for r in regs if r["guess"] == "airgap-like"],
        "expected": {"part_count": len(rows), "areas": {r["name"]: r["area_mm2"] for r in rows}},
        "note": "draft from geometry only: confirm every name/role (confidence < 0.7 = a guess), add 'coils' from "
                "jmag_winding_plan(...)['labels_coils'] and 'parameters.stack_mm' before use",
    }
    if not labels["magnets"]:
        labels.pop("magnets")
    return {"path": str(path), "units": units, "regions": rows, "region_count": len(rows),
            "guesses": dict(Counter(r["guess"] for r in rows)), "labels_draft": labels,
            "diagnostics": {**diag, "primitives": len(prims), "skipped_entities": skipped}, "warnings": warnings}
