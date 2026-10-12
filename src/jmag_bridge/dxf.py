"""Tiny dependency-free DXF (R12) writer for 2D geometry handed to JMAG.

JMAG's Geometry Editor turns every closed loop into a region, so draw each
part as a closed outline. Shared edges between neighbouring parts are fine
(and recommended): draw them once or twice, the editor merges duplicates.

Units are whatever your model uses (JMAG's default is mm).

Example
-------
>>> d = Dxf()
>>> d.polyline([(0, 0), (10, 0), (10, 5), (0, 5)], closed=True)
>>> d.arc((0, 0), 20, 0, 45)
>>> d.save("part.dxf")
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

Point = Sequence[float]


class Dxf:
    def __init__(self) -> None:
        self._ents: list[list[str]] = []

    def line(self, a: Point, b: Point, layer: str = "0") -> "Dxf":
        self._ents.append(["0", "LINE", "8", layer,
                           "10", _f(a[0]), "20", _f(a[1]), "30", "0.0",
                           "11", _f(b[0]), "21", _f(b[1]), "31", "0.0"])
        return self

    def polyline(self, pts: Iterable[Point], closed: bool = True, layer: str = "0") -> "Dxf":
        pts = [tuple(p) for p in pts]
        segs = list(zip(pts, pts[1:]))
        if closed and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        for a, b in segs:
            self.line(a, b, layer)
        return self

    def arc(self, center: Point, radius: float, start_deg: float, end_deg: float, layer: str = "0") -> "Dxf":
        """Counter-clockwise arc from start_deg to end_deg."""
        self._ents.append(["0", "ARC", "8", layer,
                           "10", _f(center[0]), "20", _f(center[1]), "30", "0.0",
                           "40", _f(radius), "50", _f(start_deg), "51", _f(end_deg)])
        return self

    def circle(self, center: Point, radius: float, layer: str = "0") -> "Dxf":
        self._ents.append(["0", "CIRCLE", "8", layer,
                           "10", _f(center[0]), "20", _f(center[1]), "30", "0.0", "40", _f(radius)])
        return self

    def annular_sector(self, r_in: float, r_out: float, a0_deg: float, a1_deg: float,
                       center: Point = (0.0, 0.0), layer: str = "0") -> "Dxf":
        """Closed ring sector (two arcs + two radial lines), e.g. a magnet or an air-gap band."""
        cx, cy = center
        p = lambda r, a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
        if r_in > 0:
            self.arc(center, r_in, a0_deg, a1_deg, layer)
            self.line(p(r_in, a0_deg), p(r_out, a0_deg), layer)
            self.line(p(r_in, a1_deg), p(r_out, a1_deg), layer)
        else:
            self.line(center, p(r_out, a0_deg), layer)
            self.line(center, p(r_out, a1_deg), layer)
        self.arc(center, r_out, a0_deg, a1_deg, layer)
        return self

    def to_string(self) -> str:
        out = ["0", "SECTION", "2", "ENTITIES"]
        for e in self._ents:
            out += e
        out += ["0", "ENDSEC", "0", "EOF"]
        return "\n".join(out) + "\n"

    def save(self, path: str) -> str:
        with open(path, "w", encoding="ascii") as f:
            f.write(self.to_string())
        return path


def _f(v: float) -> str:
    return repr(float(v))


def c_core_inductor(path: str) -> dict:
    """Gapped C-core used by the self-test (mm). Returns the analytic data."""
    d = Dxf()
    d.polyline([(45, 30.5), (45, 45), (15, 45), (15, 15), (45, 15), (45, 29.5),
                (60, 29.5), (60, 0), (0, 0), (0, 60), (60, 60), (60, 30.5)])
    d.polyline([(16, 17), (24, 17), (24, 43), (16, 43)])     # coil side inside the window
    d.polyline([(-9, 17), (-1, 17), (-1, 43), (-9, 43)])     # return side outside
    d.save(path)
    return {"core_point": [5.0, 30.0], "coil_in_point": [20.0, 30.0], "coil_out_point": [-5.0, 30.0],
            "leg_width_mm": 15.0, "gap_mm": 1.0, "core_area_mm2": 2685.0, "coil_area_mm2": 208.0}
