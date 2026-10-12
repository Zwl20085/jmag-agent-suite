"""12-slot / 4-pole surface-PM motor, drawn outside JMAG (the recommended workflow).

Writes  spm_12s4p.dxf        - closed outlines; JMAG turns each loop into a part
        spm_12s4p_parts.json - one labelled point inside every part, plus winding layout,
                               so the agent can name parts and set coils deterministically.

Single-layer full-pitch winding, 60-degree phase belts: slot k (0..11) carries
U+, W-, V+, U-, W+, V- repeating. Dimensions in mm.

    python make_geometry.py [out_dir]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from jmag_bridge.dxf import Dxf  # noqa: E402

P = dict(
    poles=4, slots=12,
    r_shaft=0.0, r_rotor=27.0, magnet_thickness=3.0, magnet_arc_deg=80.0,
    airgap=1.0, r_slot_bottom=45.0, r_stator_out=60.0, slot_angle_deg=15.0,
    stack_mm=50.0,
)
PHASE_PATTERN = [("U", "up"), ("W", "down"), ("V", "up"), ("U", "down"), ("W", "up"), ("V", "down")]


def pol(r: float, deg: float) -> tuple[float, float]:
    return (r * math.cos(math.radians(deg)), r * math.sin(math.radians(deg)))


def build(out_dir: Path) -> dict:
    d = Dxf()
    r_mag = P["r_rotor"] + P["magnet_thickness"]
    r_bore = r_mag + P["airgap"]
    pitch = 360.0 / P["slots"]
    half = P["slot_angle_deg"] / 2

    # stator: outer circle + bore/slot boundary (one closed loop) -> stator core region
    d.circle((0, 0), P["r_stator_out"])
    for k in range(P["slots"]):
        c = k * pitch
        a0, a1 = c - half, c + half
        d.arc((0, 0), P["r_slot_bottom"], a0, a1)                 # slot bottom
        d.line(pol(r_bore, a0), pol(P["r_slot_bottom"], a0))      # slot sides
        d.line(pol(r_bore, a1), pol(P["r_slot_bottom"], a1))
        d.arc((0, 0), r_bore, a1, a0 + pitch)                     # bore between slots (tooth tip)
        d.arc((0, 0), r_bore, a0, a1)                             # closes the slot -> coil region

    # rotor core + surface magnets
    d.circle((0, 0), P["r_rotor"])
    pole_pitch = 360.0 / P["poles"]
    for k in range(P["poles"]):
        c = k * pole_pitch
        d.annular_sector(P["r_rotor"], r_mag, c - P["magnet_arc_deg"] / 2, c + P["magnet_arc_deg"] / 2)

    out_dir.mkdir(parents=True, exist_ok=True)
    dxf_path = d.save(str(out_dir / "spm_12s4p.dxf"))

    r_coil = (r_bore + P["r_slot_bottom"]) / 2
    parts = [{"name": "StatorCore", "point": list(pol((P["r_slot_bottom"] + P["r_stator_out"]) / 2, 0))},
             {"name": "RotorCore", "point": [0.0, 0.0]}]
    magnets = []
    for k in range(P["poles"]):
        c = k * pole_pitch
        name = f"Magnet{k + 1}"
        parts.append({"name": name, "point": list(pol(P["r_rotor"] + P["magnet_thickness"] / 2, c))})
        magnets.append({"name": name, "center_deg": c, "polarity": "outward" if k % 2 == 0 else "inward"})
    coils = {"U": {"up": [], "down": []}, "V": {"up": [], "down": []}, "W": {"up": [], "down": []}}
    for k in range(P["slots"]):
        ph, direction = PHASE_PATTERN[k % 6]
        name = f"Slot{k + 1:02d}_{ph}{'+' if direction == 'up' else '-'}"
        parts.append({"name": name, "point": list(pol(r_coil, k * pitch))})
        coils[ph][direction].append(name)
    meta = {"dxf": dxf_path, "parameters": P, "parts": parts, "magnets": magnets, "coils": coils,
            "rotor_parts": ["RotorCore"] + [m["name"] for m in magnets]}
    (out_dir / "spm_12s4p_parts.json").write_text(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "out"
    m = build(out)
    print(f"wrote {m['dxf']} with {len(m['parts'])} labelled parts")
