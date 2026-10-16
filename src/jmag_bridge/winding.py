"""Winding layout from slots/poles/phases: feasibility, slot-phase map, winding factors, MMF harmonics.

Pure Python (no JMAG). Method: star of slots with 2m phase belts.

Conventions
- ``poles`` is the number of poles (2p); ``pp`` = poles/2 pole pairs.
- Slots are numbered 1..Q counter-clockwise; slot k (0-based) sits at mechanical angle 2*pi*k/Q and its
  conductor EMF phasor at electrical angle pp*2*pi*k/Q.
- Phase j (U, V, W for m = 3) is centred at electrical angle j*2*pi/m, so U -> V -> W is the sequence
  that turns the field towards increasing slot number.
- Sign "+" = current out of the 2D plane ("up" in jmag_add_fem_coil), "-" = into the plane ("down").
- Double layer: a coil of pitch y (slots) has its "+"/top side in slot k (layer "a") and its return side in
  slot k+y (layer "b").  Single layer: one coil side per slot.
"""
from __future__ import annotations

import cmath
import math
from math import gcd


def phase_names(phases: int) -> list[str]:
    return list("UVW") if phases == 3 else [chr(ord("A") + j) for j in range(phases)]


def check_feasibility(slots: int, poles: int, phases: int = 3, layers: int = 2) -> dict:
    """Raise ValueError (with the reason and the fix) if the combination cannot be wound balanced.

    Rules: poles even; phases odd >= 3; Q divisible by m; Q / (m * t) integer with t = gcd(Q, pp)
    (balanced, every phase sees the same star-of-slots pattern); single layer additionally needs
    Q / (2 m) integer.  Returns the basic numbers (q, t, slots per pole per phase).
    """
    for name, v in (("slots", slots), ("poles", poles), ("phases", phases), ("layers", layers)):
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise ValueError(f"{name} must be a positive integer, got {v!r}")
    if poles % 2:
        raise ValueError(f"poles={poles} is odd; pass the number of poles (2p), e.g. 4 for a 4-pole machine")
    if phases < 3 or phases % 2 == 0:
        raise ValueError(f"phases={phases} not supported; use an odd phase count >= 3 (3, 5, ...)")
    if layers not in (1, 2):
        raise ValueError("layers must be 1 (single layer) or 2 (double layer)")
    pp = poles // 2
    t = gcd(slots, pp)
    if slots % phases:
        raise ValueError(f"{slots} slots is not divisible by {phases} phases; pick Q = k*{phases}")
    if (slots // t) % phases:
        raise ValueError(f"unbalanced: Q/(m*gcd(Q,p)) = {slots}/({phases}*{t}) is not an integer; "
                         "choose another slot/pole combination (e.g. 12s/10p, 12s/8p, 9s/8p, 24s/4p)")
    if layers == 1 and slots % (2 * phases):
        raise ValueError(f"single layer needs Q divisible by 2m = {2 * phases}; use layers=2")
    q = slots / (phases * poles)
    return {"slots": slots, "poles": poles, "pole_pairs": pp, "phases": phases, "layers": layers,
            "q": q, "periodicity_t": t, "slot_pitch_elec_deg": 360.0 * pp / slots}


def symmetry(slots: int, poles: int) -> dict:
    """Smallest model sector. t = gcd(Q, pp) repetitions; if Q/t is even the half-period is anti-periodic."""
    pp = poles // 2
    t = gcd(slots, pp)
    anti = (slots // t) % 2 == 0
    n = 2 * t if anti else t
    return {"periodicity_t": t, "sector_fraction": f"1/{n}", "sector_deg": 360.0 / n, "slots_in_sector": slots // n,
            "poles_in_sector": poles / n, "boundary": "anti-periodic" if anti else ("periodic" if n > 1 else "none (full model)"),
            "note": "JMAG: RotationPeriodicBoundary on the sector edges (anti-periodic when the boundary says so) "
                    "and scale torque/flux results by the sector count (unverified property names: check "
                    "jmag_get_properties(kind='condition'))."}


def _belt(k: int, slots: int, pp: int, m: int) -> tuple[int, int]:
    """Phase index and sign of the conductor in slot k (exact integer arithmetic; boundary ties go up)."""
    full = 4 * m * slots                    # 2*pi in units of 2*pi/(4 m Q)
    theta = (k * pp * 4 * m + slots) % full  # slot angle + half a belt
    b = theta // (2 * slots)                 # belt width pi/m = 2Q units
    if b % 2 == 0:
        return (b // 2) % m, +1
    return ((b - m) // 2) % m, -1


def _pair_single_layer(sides: list[dict], slots: int, y: int) -> list[dict]:
    coils = []
    for ph in sorted({s["phase"] for s in sides}):
        plus = [s["slot"] for s in sides if s["phase"] == ph and s["sign"] > 0]
        minus = [s["slot"] for s in sides if s["phase"] == ph and s["sign"] < 0]
        if len(plus) != len(minus):
            raise ValueError(f"single-layer pairing failed for phase {ph}; use layers=2")
        for a in plus:
            b = min(minus, key=lambda s: (abs(((s - a) % slots) - y), (s - a) % slots))
            minus.remove(b)
            coils.append({"phase": ph, "go_slot": a + 1, "return_slot": b + 1, "pitch": (b - a) % slots})
    return coils


def layout(slots: int, poles: int, phases: int = 3, layers: int = 2, coil_pitch: int | None = None) -> dict:
    """Coil sides [{slot (1-based), layer, phase, sign, direction}] and coils [{phase, go_slot, return_slot, pitch}]."""
    info = check_feasibility(slots, poles, phases, layers)
    pp = info["pole_pairs"]
    y = coil_pitch if coil_pitch is not None else max(1, round(slots / poles))
    if not isinstance(y, int) or y < 1 or y >= slots:
        raise ValueError(f"coil_pitch must be an integer number of slots in 1..{slots - 1}, got {coil_pitch!r}")
    names = phase_names(phases)
    sides: list[dict] = []
    coils: list[dict] = []
    for k in range(slots):
        ph, sg = _belt(k, slots, pp, phases)
        sides.append({"slot": k, "layer": "a" if layers == 2 else "", "phase": ph, "sign": sg})
    if layers == 2:
        for k in range(slots):
            ph, sg = sides[k]["phase"], sides[k]["sign"]
            sides.append({"slot": (k + y) % slots, "layer": "b", "phase": ph, "sign": -sg})
            go, ret = (k, (k + y) % slots) if sg > 0 else ((k + y) % slots, k)
            coils.append({"phase": ph, "go_slot": go + 1, "return_slot": ret + 1, "pitch": y})
    else:
        coils = _pair_single_layer(sides, slots, y)
    out_sides = [{"slot": s["slot"] + 1, "layer": s["layer"], "phase": names[s["phase"]],
                  "sign": "+" if s["sign"] > 0 else "-", "direction": "up" if s["sign"] > 0 else "down",
                  "_p": s["phase"], "_s": s["sign"]} for s in sides]
    out_sides.sort(key=lambda s: (s["slot"], s["layer"]))
    for c in coils:
        c["phase"] = names[c["phase"]]
    return {**info, "coil_pitch": y, "sides": out_sides, "coils": coils}


def _phase_sum(sides: list[dict], phase: int, order: float, slots: int) -> complex:
    """Sum of signed conductor phasors of one phase for a spatial order (mechanical order * slot angle)."""
    return sum(s["_s"] * cmath.exp(1j * order * 2 * math.pi * (s["slot"] - 1) / slots)
               for s in sides if s["_p"] == phase)


def winding_factors(lay: dict, harmonics: int = 13) -> dict:
    """kw for electrical harmonic orders 1..harmonics (relative to the pole-pair number). |sum of signed phasors| / sides."""
    pp, slots = lay["pole_pairs"], lay["slots"]
    n_side = sum(1 for s in lay["sides"] if s["_p"] == 0)
    out = {}
    for nu in range(1, harmonics + 1):
        out[nu] = abs(_phase_sum(lay["sides"], 0, nu * pp, slots)) / n_side
    return out


def balance(lay: dict) -> dict:
    """Numerical balance check: every phase has the same side count, |EMF| and is 2*pi/m from the next."""
    m, pp, slots = lay["phases"], lay["pole_pairs"], lay["slots"]
    sums = [_phase_sum(lay["sides"], j, pp, slots) for j in range(m)]
    counts = [sum(1 for s in lay["sides"] if s["_p"] == j) for j in range(m)]
    mags = [abs(z) for z in sums]
    ok = len(set(counts)) == 1 and max(mags) - min(mags) < 1e-9 * max(mags)
    for j in range(m):
        d = cmath.phase(sums[(j + 1) % m] / sums[j]) if abs(sums[j]) > 1e-12 else 0.0
        ok = ok and abs(abs(d) - 2 * math.pi / m) < 1e-6
    return {"balanced": ok, "sides_per_phase": counts}


def mmf_harmonics(lay: dict, max_order: int | None = None, threshold: float = 0.01) -> list[dict]:
    """Stator MMF space harmonics for balanced m-phase currents, mechanical orders 1..max_order.

    Amplitude of order n ~ |sum_j W_j(n) e^(+-j 2 pi j/m)| / n, where W_j(n) is the spatial Fourier sum of
    phase j's conductors. Normalised to the working harmonic n = pp. "forward" turns with the working wave.
    Sub-harmonics (n < pp) and slot harmonics of fractional-slot windings show up here; they cause rotor
    losses and noise.
    """
    m, pp, slots = lay["phases"], lay["pole_pairs"], lay["slots"]
    max_order = max_order or max(4 * pp, 2 * slots)
    rows = []
    for n in range(1, max_order + 1):
        w = [_phase_sum(lay["sides"], j, n, slots) for j in range(m)]
        fw = abs(sum(w[j] * cmath.exp(-2j * math.pi * j / m) for j in range(m))) / n
        bw = abs(sum(w[j] * cmath.exp(+2j * math.pi * j / m) for j in range(m))) / n
        rows.append((n, fw, bw))
    ref = next(max(f, b) for n, f, b in rows if n == pp)
    out = []
    for n, f, b in rows:
        a = max(f, b) / ref if ref else 0.0
        if a >= threshold:
            out.append({"order_mech": n, "order_elec": n / pp, "rel_amplitude": round(a, 4),
                        "direction": "forward" if f >= b else "backward", "working": n == pp})
    return out


def part_name(side: dict, layers: int) -> str:
    """Repo convention Slot<kk>_<phase><+|->; double layer adds the layer letter: Slot<kk><a|b>_<phase><+|->."""
    lay = side["layer"] if layers == 2 else ""
    return f"Slot{side['slot']:02d}{lay}_{side['phase']}{side['sign']}"


def plan(slots: int, poles: int, phases: int = 3, layers: int = 2, coil_pitch: int | None = None,
         turns: int | None = None, parallel_paths: int = 1, harmonics: int = 13) -> dict:
    """Full winding plan: feasibility, layout, kw table, MMF harmonics, symmetry, part names, labels-JSON coils."""
    lay = layout(slots, poles, phases, layers, coil_pitch)
    bal = balance(lay)
    if not bal["balanced"]:
        raise ValueError(f"{slots}s/{poles}p with layers={layers}, coil_pitch={lay['coil_pitch']} is not balanced "
                         f"({bal['sides_per_phase']} sides per phase); try layers=2 or another coil pitch")
    kw = winding_factors(lay, harmonics)
    names = phase_names(phases)
    coils_json: dict = {p: {"up": [], "down": []} for p in names}
    for s in lay["sides"]:
        s["part"] = part_name(s, layers)
        coils_json[s["phase"]]["up" if s["sign"] == "+" else "down"].append(s["part"])
    coils_per_phase = len(lay["coils"]) // phases
    warnings: list[str] = []
    if parallel_paths < 1 or coils_per_phase % parallel_paths:
        raise ValueError(f"parallel_paths={parallel_paths} must divide the {coils_per_phase} coils per phase")
    sym = symmetry(slots, poles)
    reps = 2 * sym["periodicity_t"] if sym["boundary"] == "anti-periodic" else sym["periodicity_t"]
    if reps % parallel_paths:
        warnings.append(f"{parallel_paths} parallel paths do not divide the {reps} identical winding sections: "
                        "paths will not see equal EMF (circulating currents)")
    if lay["coil_pitch"] * 2 * lay["pole_pairs"] > 1.5 * slots and layers == 2:
        warnings.append("coil pitch is much longer than a pole pitch; check coil_pitch")
    out = {k: lay[k] for k in ("slots", "poles", "pole_pairs", "phases", "layers", "q", "coil_pitch",
                                "slot_pitch_elec_deg")}
    out.update({
        "winding_type": "concentrated (tooth coils)" if lay["coil_pitch"] == 1 else "distributed",
        "kw1": round(kw[1], 5),
        "winding_factors": {str(k): round(v, 5) for k, v in kw.items()},
        "mmf_harmonics": mmf_harmonics(lay),
        "symmetry": sym,
        "coils_per_phase": coils_per_phase,
        "slot_map": [{k: s[k] for k in ("slot", "layer", "phase", "sign", "direction", "part")} for s in lay["sides"]],
        "coil_list": lay["coils"],
        "labels_coils": coils_json,
        "warnings": warnings,
    })
    if turns is not None:
        if turns <= 0:
            raise ValueError("turns (per coil) must be positive")
        n_series = turns * coils_per_phase / parallel_paths
        out["turns_per_coil"] = turns
        out["series_turns_per_phase"] = n_series
        out["effective_turns_per_phase"] = round(n_series * kw[1], 4)
        out["fem_coil_hint"] = (f"one circuit Coil per phase with Turn={n_series:g} (series turns per phase) and "
                                f"jmag_add_fem_coil sides from labels_coils[<phase>]: up -> direction 'up', "
                                f"down -> 'down'"
                                + ("" if parallel_paths == 1 else
                                   f"; {parallel_paths} parallel paths: phase resistance and current per path change "
                                   "(how JMAG's Coil component represents paths is unverified - check jmag_docs_search)"))
    return out
