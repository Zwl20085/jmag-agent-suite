"""JMAG field results as plain data outside JMAG (server side, Python >= 3.10, pure apart from one ctypes class).

Two files of a solved study's case folder hold everything a field plot needs (measured on JMAG-Designer 23.0):

* ``Designer.jplot`` (``study.GetResultFileName()``) read with JMAG's own ``JplotReader.dll`` (C ABI, shipped in the
  install root; ``Tools/JPlotReader`` has the header): node ids + coordinates [m], element ids + part ids + element
  type + centre + area, the part titles (``"Name@Material"``; JMAG's own air regions have an empty title), per step
  the result components by post key (16001 element B, 11005 nodal A ...) and the nodal displacement of moving
  (rotor) nodes. Steps are 1-based. It has no connectivity.
* ``Designer_input.jcf`` / ``Designer.jcf`` of the case folder (whichever holds ``mesh.bin``; else
  ``study.WriteMeshJcf(path)``, which writes the same layout) is a
  ZIP whose member ``mesh.bin`` is a 6-byte prefix + a raw-deflate stream of an undocumented little-endian layout:
  64-byte header (``b"MJ\\0\\0"``, int32 1, node count, element count, ...), node records ``(int32 id, 3 x float64
  xyz)``, then element records ``(int32 id, int32 type, int32 1, int32 part id, node ids ...)`` with 3 node ids for
  type 2 (triangle) and 4 for type 3 (quadrilateral). Node and element ids and the part ids are those of the jplot
  (checked on every element: centroid and area from this connectivity equal the jplot's element centre and area).

``JplotFile`` is the only code bound to the DLL; everything else works on numpy arrays and is unit-tested with
hand-made fixtures. The assembled dict is the field-data format ``plotting.field`` / ``plotting.mesh`` draw
(see ``plotting.validate_field_data``): triangles only (quads split), lengths in mm, elemental values per triangle.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

NUMPY_HINT = 'pip install "jmag-agent-suite[plots]"  (installs numpy + matplotlib into the bridge\'s Python)'


class FieldDataError(RuntimeError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


def import_numpy(error: type = None):
    """numpy, or `error` (default FieldDataError) with an install hint. Shared with plotting."""
    try:
        import numpy as np
    except ImportError as e:
        exc = error or FieldDataError
        raise (exc(f"numpy is not installed ({e})", NUMPY_HINT) if exc is FieldDataError
               else exc(f"numpy is not installed ({e})")) from None
    return np


def _np():
    return import_numpy()


def tri_areas(xy, tris):
    """Unsigned areas of triangles (index array (E, 3) into xy (N, 2))."""
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    return 0.5 * abs((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1]))


def element_to_nodal(n_nodes: int, tris, ev, areas):
    """Area-weighted average of per-triangle values onto n_nodes nodes; NaN where no valued triangle touches.
    The single implementation used by nodal_average, the probes and plotting (flux lines, smooth shading)."""
    np = _np()
    ev = np.asarray(ev, dtype=float)
    w = np.where(np.isfinite(ev), areas, 0.0)
    v = np.nan_to_num(ev) * w
    num = np.zeros(n_nodes)
    den = np.zeros(n_nodes)
    for j in range(3):
        np.add.at(num, tris[:, j], v)
        np.add.at(den, tris[:, j], w)
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)


# ------------------------------------------------------------------------------------------------ quantities

@dataclass(frozen=True)
class Quantity:
    key: int            # jplot post key (KeyList.xml in the JMAG install root)
    location: str       # "element" | "node"
    component: Any      # 0/1/2 = x/y/z component, "abs" = vector magnitude
    result_type: str    # JMAG result type name (what jmag_contour_image takes)
    jmag_component: str
    unit: str
    verified: bool      # value compared with JMAG's own output on a real study


#: Quantities the field export knows. Verified = compared with JMAG on the 12s/4p SPM example (JMAG-Designer 23.0):
#: B max equals jmag_contour_image's ContourMaxValue at the same step to all printed digits; Az is the nodal
#: vector potential (flux lines); J matches N*I / slot area. The others are read the same way but their meaning
#: was only checked by name and magnitude (marked unverified).
QUANTITIES: dict[str, Quantity] = {
    "B": Quantity(16001, "element", "abs", "MagneticFluxDensity", "Absolute", "T", True),
    "Bx": Quantity(16001, "element", 0, "MagneticFluxDensity", "X", "T", True),
    "By": Quantity(16001, "element", 1, "MagneticFluxDensity", "Y", "T", True),
    "Az": Quantity(11005, "node", 2, "VectorPotential", "Z", "Wb/m", True),
    "J": Quantity(16503, "element", 2, "CurrentDensity", "Z", "A/m^2", True),
    "M": Quantity(16505, "element", "abs", "MagnetizationVector", "Absolute", "A/m", False),
    "mu_r_diff": Quantity(16521, "element", 0, "DifferentialPermeability", "", "1", False),
    "PermeanceFactor": Quantity(170002, "element", 0, "PermeanceFactor", "", "1", False),
}
#: Element types in mesh.bin / jplot: number of nodes. Only these were seen in 2D meshes (23.0).
ELEMENT_NODES = {2: 3, 3: 4}
LENGTH_SCALE = {"mm": 1e3, "m": 1.0}     # jplot / mesh.bin coordinates are in metres


def parse_quantity(name: str) -> tuple[str, Quantity]:
    """A name of QUANTITIES, or the escape hatch ``key:<post key>:<node|element>:<0|1|2|abs>`` (unverified)."""
    if name in QUANTITIES:
        return name, QUANTITIES[name]
    if name.startswith("key:"):
        try:
            _, k, loc, comp = name.split(":")
            q = Quantity(int(k), loc, comp if comp == "abs" else int(comp), f"jplot key {k}", str(comp), "", False)
        except ValueError:
            q = None
        if q is not None and loc in ("node", "element") and (q.component == "abs" or q.component in (0, 1, 2)):
            return name, q
        raise FieldDataError(f"bad raw quantity {name!r}", "format key:<post key>:<node|element>:<0|1|2|abs>, "
                                                           "e.g. key:16505:element:abs; keys: KeyList.xml in the JMAG install")
    raise FieldDataError(f"unknown field quantity {name!r}",
                         f"known: {sorted(QUANTITIES)} (Br/Bt for probes are derived from Bx/By)")


# ------------------------------------------------------------------------------------------------ mesh.bin

@dataclass
class Mesh:
    node_ids: Any        # (N,) int
    xyz: Any             # (N, 3) float, metres
    elem_ids: Any        # (E,) int
    elem_type: Any       # (E,) int
    elem_part: Any       # (E,) int  (jplot part id)
    conn: list = field(default_factory=list)   # E tuples of node ids


_MESH_MAGIC = b"MJ\x00\x00"
_HEADER = 64


def _inflate_mesh_bin(raw: bytes) -> bytes:
    for off in range(0, 33):   # 23.0 writes a 6-byte prefix before the raw-deflate stream; search a little
        try:
            out = zlib.decompressobj(-15).decompress(raw[off:])
        except zlib.error:
            continue
        if out[:4] == _MESH_MAGIC:
            return out
    if raw[:4] == _MESH_MAGIC:
        return raw
    raise FieldDataError("mesh.bin is not in the layout the bridge knows (no MJ header after inflating)",
                         "the mesh format may have changed with this JMAG version; report it with the JMAG version")


def parse_mesh_bin(blob: bytes) -> Mesh:
    """Decode an inflated or raw ``mesh.bin`` (see module docstring)."""
    np = _np()
    b = _inflate_mesh_bin(blob)
    if len(b) < _HEADER:
        raise FieldDataError("mesh.bin is truncated")
    _, _ver, n, e = struct.unpack_from("<4s3i", b, 0)
    if n <= 0 or e <= 0:
        raise FieldDataError(f"mesh.bin has {n} nodes and {e} elements", "is the study meshed and solved?")
    rec = np.dtype([("id", "<i4"), ("xyz", "<f8", 3)])
    end_nodes = _HEADER + n * rec.itemsize
    if end_nodes > len(b):
        raise FieldDataError("mesh.bin node block is truncated")
    nodes = np.frombuffer(b, dtype=rec, count=n, offset=_HEADER)
    ints = np.frombuffer(b, dtype="<i4", offset=end_nodes, count=(len(b) - end_nodes) // 4)
    eid = np.empty(e, dtype=np.int64)
    etype = np.empty(e, dtype=np.int64)
    epart = np.empty(e, dtype=np.int64)
    conn: list[tuple[int, ...]] = []
    p = 0
    for k in range(e):
        if p + 4 > ints.size:
            raise FieldDataError(f"mesh.bin element block ends after {k} of {e} elements")
        t = int(ints[p + 1])
        nn = ELEMENT_NODES.get(t)
        if nn is None:
            raise FieldDataError(f"element {int(ints[p])} has type {t}; only 2 (triangle) and 3 (quad) are supported",
                                 "2D first-order meshes only; second-order or 3D meshes are not supported yet")
        if p + 4 + nn > ints.size:
            raise FieldDataError(f"mesh.bin element block ends after {k} of {e} elements")
        eid[k], etype[k], epart[k] = ints[p], t, ints[p + 3]
        conn.append(tuple(int(v) for v in ints[p + 4:p + 4 + nn]))
        p += 4 + nn
    if p != ints.size:
        raise FieldDataError(f"mesh.bin has {4 * (ints.size - p)} unexpected trailing bytes",
                             "the mesh format may have changed with this JMAG version")
    return Mesh(nodes["id"].astype(np.int64), np.array(nodes["xyz"], dtype=float), eid, etype, epart, conn)


def read_mesh_jcf(path: str | os.PathLike) -> Mesh:
    """Mesh of a ``.jcf`` file (ZIP with ``mesh.bin``): Designer_input.jcf / Designer.jcf of a case folder, or
    study.WriteMeshJcf output."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "mesh.bin" not in names:
                raise FieldDataError(f"{path} has no mesh.bin (members: {names[:12]})",
                                     "use the Designer_input.jcf of a solved case, or study.WriteMeshJcf")
            blob = z.read("mesh.bin")
    except (OSError, zipfile.BadZipFile) as e:
        raise FieldDataError(f"cannot read mesh file {path}: {e}") from None
    return parse_mesh_bin(blob)


# ------------------------------------------------------------------------------------------------ JplotReader.dll

def dll_path(home: str | os.PathLike | None) -> Path:
    if home is None:
        raise FieldDataError("JMAG-Designer installation not found", "set JMAG_HOME to the folder containing designer.exe")
    p = Path(home) / "JplotReader.dll"
    if not p.is_file():
        raise FieldDataError(f"JplotReader.dll not found in {home}",
                             "it ships with JMAG-Designer 23.x in the install root; older versions have no jplot reader")
    return p


class JplotFile:
    """ctypes wrapper of JMAG's JplotReader.dll (64-bit; load with the bridge's Python, not JMAG's).

    Usage: ``with JplotFile(home, path) as jp: jp.nodes(); jp.component(step, 16001)``.
    """
    _lib = None

    def __init__(self, home: str | os.PathLike | None, path: str | os.PathLike):
        self.path = str(path)
        if not os.path.isfile(self.path):
            raise FieldDataError(f"result file not found: {self.path}", "run the study first (jmag_run)")
        self.lib = self._load(dll_path(home))
        self.h = self.lib.jplotOpen(self.path.encode("mbcs" if os.name == "nt" else "utf-8"))
        if not self.h:
            raise FieldDataError(f"JplotReader could not open {self.path}", "is the file complete (solve finished)?")

    @classmethod
    def _load(cls, dll: Path):
        if cls._lib is not None:
            return cls._lib
        C = ctypes
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(dll.parent))
        try:
            L = C.CDLL(str(dll))
        except OSError as e:
            raise FieldDataError(f"cannot load {dll}: {e}", "the bridge needs a 64-bit Python on Windows") from None
        vp, ip, dp = C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_double)
        sigs = {
            "jplotOpen": ([C.c_char_p], vp), "jplotClose": ([vp], None),
            "jplotCountNodes": ([vp], C.c_int), "jplotStartPopNode": ([vp], None),
            "jplotPopNode": ([vp, ip, dp, dp, dp], None), "jplotEndPopNode": ([vp], None),
            "jplotCountElements": ([vp], C.c_int), "jplotStartPopElementCenter": ([vp], None),
            "jplotPopElementCenter": ([vp, ip, ip, ip, dp, dp, dp, dp], None), "jplotEndPopElementCenter": ([vp], None),
            "jplotCountParts": ([vp], C.c_int), "jplotGetPartIdTitle": ([vp, C.c_int, ip, C.c_char_p, C.c_int], None),
            "jplotCreateComponentReader": ([vp, C.c_int, C.c_int], vp), "jplotComponentDimension": ([vp], C.c_int),
            "jplotCountComponents": ([vp], C.c_int), "jplotStartPopComponent": ([vp], None),
            "jplotPopComponent": ([vp, ip, C.c_double * 3], None), "jplotEndPopComponent": ([vp], None),
            "jplotDeleteComponentReader": ([vp], None),
            "jplotCountNodalDisplacements": ([vp, C.c_int], C.c_int),
            "jplotStartPopNodalDisplacement": ([vp, C.c_int], None),
            "jplotPopNodalDisplacement": ([vp, ip, dp, dp, dp], None), "jplotEndPopNodalDisplacement": ([vp], None),
        }
        for name, (args, res) in sigs.items():
            f = getattr(L, name)
            f.argtypes, f.restype = args, res
        cls._lib = L
        return L

    def close(self) -> None:
        if self.h:
            self.lib.jplotClose(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def nodes(self):
        np, L, C = _np(), self.lib, ctypes
        n = L.jplotCountNodes(self.h)
        ids = np.empty(n, dtype=np.int64)
        xyz = np.empty((n, 3))
        i, x, y, z = C.c_int(), C.c_double(), C.c_double(), C.c_double()
        L.jplotStartPopNode(self.h)
        for k in range(n):
            L.jplotPopNode(self.h, i, x, y, z)
            ids[k], xyz[k] = i.value, (x.value, y.value, z.value)
        L.jplotEndPopNode(self.h)
        return ids, xyz

    def elements(self):
        """ids, part ids, element types, centres (E, 3) [m], areas [m^2] (volumes in 3D)."""
        np, L, C = _np(), self.lib, ctypes
        n = L.jplotCountElements(self.h)
        ids, part, et = (np.empty(n, dtype=np.int64) for _ in range(3))
        ctr, area = np.empty((n, 3)), np.empty(n)
        i, p, t = C.c_int(), C.c_int(), C.c_int()
        x, y, z, a = C.c_double(), C.c_double(), C.c_double(), C.c_double()
        L.jplotStartPopElementCenter(self.h)
        for k in range(n):
            L.jplotPopElementCenter(self.h, i, p, t, x, y, z, a)
            ids[k], part[k], et[k], ctr[k], area[k] = i.value, p.value, t.value, (x.value, y.value, z.value), a.value
        L.jplotEndPopElementCenter(self.h)
        return ids, part, et, ctr, area

    def parts(self) -> dict[int, str]:
        L, C = self.lib, ctypes
        out = {}
        buf = C.create_string_buffer(512)
        i = C.c_int()
        for k in range(L.jplotCountParts(self.h)):
            L.jplotGetPartIdTitle(self.h, k, i, buf, 512)
            out[i.value] = buf.value.decode("mbcs" if os.name == "nt" else "utf-8", "replace")
        return out

    def component(self, step: int, key: int):
        """(ids, values (n, 3)) of post key `key` at 1-based `step`, or None if the file has no such data."""
        np, L, C = _np(), self.lib, ctypes
        r = L.jplotCreateComponentReader(self.h, int(step), int(key))
        if not r:
            return None
        try:
            n = L.jplotCountComponents(r)
            ids = np.empty(n, dtype=np.int64)
            vals = np.empty((n, 3))
            i, v = C.c_int(), (C.c_double * 3)()
            L.jplotStartPopComponent(r)
            for k in range(n):
                L.jplotPopComponent(r, i, v)
                ids[k], vals[k] = i.value, (v[0], v[1], v[2])
            L.jplotEndPopComponent(r)
        finally:
            L.jplotDeleteComponentReader(r)
        return ids, vals

    def available_steps(self, key: int, upto: int) -> list[int]:
        """1-based steps for which the file holds post key `key` (probes readers until 1000 consecutive misses
        after the last hit, or `upto`)."""
        out, miss = [], 0
        for k in range(1, int(upto) + 1):
            r = self.lib.jplotCreateComponentReader(self.h, k, int(key))
            if r:
                self.lib.jplotDeleteComponentReader(r)
                out.append(k)
                miss = 0
            else:
                miss += 1
                if out and miss > 1000:
                    break
        return out

    def displacement(self, step: int):
        """(ids, d (n, 3)) nodal displacement of moving nodes at `step` [m] (rotor of a motion condition)."""
        np, L, C = _np(), self.lib, ctypes
        n = L.jplotCountNodalDisplacements(self.h, int(step))
        ids = np.empty(max(n, 0), dtype=np.int64)
        d = np.empty((max(n, 0), 3))
        if n <= 0:
            return ids, d
        i, x, y, z = C.c_int(), C.c_double(), C.c_double(), C.c_double()
        L.jplotStartPopNodalDisplacement(self.h, int(step))
        for k in range(n):
            L.jplotPopNodalDisplacement(self.h, i, x, y, z)
            ids[k], d[k] = i.value, (x.value, y.value, z.value)
        L.jplotEndPopNodalDisplacement(self.h)
        return ids, d


# ------------------------------------------------------------------------------------------------ assembly

def part_names(titles: dict[int, str]) -> tuple[list[int], list[str], list[str]]:
    """jplot part ids -> (ids, names, materials). Titles are "Name@Material"; JMAG's generated air regions have an
    empty title and become "Air_<id>" with material "Air"."""
    ids, names, mats = [], [], []
    for pid in sorted(titles):
        t = titles[pid] or ""
        name, _, mat = t.rpartition("@") if "@" in t else (t, "", "")
        if not name:
            name, mat = f"Air_{pid}", mat or "Air"
        ids.append(pid)
        names.append(name)
        mats.append(mat)
    return ids, names, mats


def split_quads(conn: Sequence[Sequence[int]]) -> tuple[list[tuple[int, int, int]], list[int]]:
    """Triangles + index of the source element for each. Quad (a, b, c, d) -> (a, b, c), (a, c, d) (keeps the
    orientation)."""
    tris, src = [], []
    for k, c in enumerate(conn):
        if len(c) == 3:
            tris.append((c[0], c[1], c[2]))
            src.append(k)
        elif len(c) == 4:
            tris += [(c[0], c[1], c[2]), (c[0], c[2], c[3])]
            src += [k, k]
        else:
            raise FieldDataError(f"element {k} has {len(c)} nodes; only triangles and quads are supported")
    return tris, src


def slide_remap(conn: list[tuple[int, ...]], xyz0, pos, moving, index: dict[int, int]):
    """Reconnect elements that straddle the sliding interface after the rotor moved.

    JMAG's slide mesh keeps one layer of air-gap elements whose nodes on one side rotate with the rotor; the
    stored connectivity is that of step 1. For a step k every element with both moving and fixed nodes gets, for
    each moving node, the moving node that now sits closest to where that node was at step 1 (exact when the
    rotation is a whole number of slide divisions, which JMAG enforces through its step control).
    Returns (new conn, remapped element count, worst position mismatch [m])."""
    np = _np()
    mov = np.zeros(len(xyz0), dtype=bool)
    mov[[index[i] for i in moving if i in index]] = True
    mixed = [k for k, c in enumerate(conn) if 0 < sum(mov[index[i]] for i in c) < len(c)]
    if not mixed:
        return conn, 0, 0.0
    cand = sorted({i for k in mixed for i in conn[k] if mov[index[i]]})
    cidx = np.array([index[i] for i in cand])
    cpos = pos[cidx, :2]
    out = list(conn)
    worst = 0.0
    lookup: dict[int, int] = {}
    for i in cand:
        d = np.hypot(cpos[:, 0] - xyz0[index[i], 0], cpos[:, 1] - xyz0[index[i], 1])
        j = int(np.argmin(d))
        lookup[i] = cand[j]
        worst = max(worst, float(d[j]))
    for k in mixed:
        out[k] = tuple(lookup.get(i, i) if mov[index[i]] else i for i in conn[k])
    return out, len(mixed), worst


def assemble(mesh: Mesh, titles: dict[int, str], values: dict[str, tuple[Quantity, Any, Any]],
             displacement=None, length_unit: str = "mm", step: int | None = None, time: float | None = None,
             case: int | None = None, meta: dict | None = None) -> dict:
    """Build the field-data dict.

    values: {name: (Quantity, ids, raw (n, 3) components)} as JplotFile.component returns them; elements or nodes
    without a value get None. displacement: (ids, d (n, 3)) of moving nodes at this step, or None.
    """
    np = _np()
    if length_unit not in LENGTH_SCALE:
        raise FieldDataError(f"length_unit {length_unit!r} not supported", f"use one of {sorted(LENGTH_SCALE)}")
    if np.any(np.abs(mesh.xyz[:, 2]) > 1e-12):
        raise FieldDataError("the mesh is not planar (z != 0): only 2D studies can be exported",
                             "for 3D use jmag_contour_image or a section plane")
    index = {int(i): k for k, i in enumerate(mesh.node_ids)}
    pos = mesh.xyz.copy()
    conn = list(mesh.conn)
    slide = {"remapped_elements": 0, "max_mismatch": 0.0, "moving_nodes": 0}
    if displacement is not None and len(displacement[0]):
        ids, d = displacement
        rows = [index[int(i)] for i in ids if int(i) in index]
        if len(rows) != len(ids):
            raise FieldDataError("displacement refers to nodes that are not in the mesh",
                                 "mesh file and result file do not belong to the same run")
        pos[rows] += d
        conn, nrem, worst = slide_remap(conn, mesh.xyz, pos, [int(i) for i in ids], index)
        slide = {"remapped_elements": nrem, "max_mismatch": worst * LENGTH_SCALE[length_unit],
                 "moving_nodes": len(rows)}
    pids, names, mats = part_names(titles)
    missing = sorted(set(int(p) for p in mesh.elem_part) - set(pids))
    for p in missing:          # part id in the mesh without a title (should not happen): keep it visible
        pids.append(p)
        names.append(f"Part_{p}")
        mats.append("")
    pidx = {p: k for k, p in enumerate(pids)}
    tris, src = split_quads(conn)
    src = np.asarray(src, dtype=np.int64)
    tri_idx = np.array([[index[a], index[b], index[c]] for a, b, c in tris], dtype=np.int64)
    eindex = {int(i): k for k, i in enumerate(mesh.elem_ids)}
    s = LENGTH_SCALE[length_unit]
    out_vals: dict[str, list] = {}
    quantities: dict[str, dict] = {}
    for name, (q, ids, raw) in values.items():
        comp = np.linalg.norm(raw, axis=1) if q.component == "abs" else raw[:, int(q.component)]
        if q.location == "node":
            arr = np.full(len(mesh.node_ids), np.nan)
            rows = np.array([index.get(int(i), -1) for i in ids], dtype=np.int64)
            ok = rows >= 0
            arr[rows[ok]] = comp[ok]
        else:
            per_elem = np.full(len(mesh.elem_ids), np.nan)
            rows = np.array([eindex.get(int(i), -1) for i in ids], dtype=np.int64)
            ok = rows >= 0
            per_elem[rows[ok]] = comp[ok]
            arr = per_elem[src]
        out_vals[name] = [None if not math.isfinite(v) else float(v) for v in arr.tolist()]
        quantities[name] = {"result_type": q.result_type, "component": q.jmag_component, "unit": q.unit,
                            "location": q.location, "jplot_key": q.key, "verified": q.verified,
                            "defined": int(np.isfinite(arr).sum())}
    data = {
        "dimension": 2, "length_unit": length_unit,
        "nodes": [[round(float(x) * s, 9), round(float(y) * s, 9)] for x, y in pos[:, :2].tolist()],
        "elements": tri_idx.tolist(),
        "element_part": [pidx[int(mesh.elem_part[k])] for k in src.tolist()],
        "parts": names,
        "part_materials": mats,
        "part_ids": pids,
        "values": out_vals,
        "quantities": quantities,
        "step": step, "time": time, "case": case,
        "node_ids": [int(i) for i in mesh.node_ids.tolist()],
        "element_ids": [int(mesh.elem_ids[k]) for k in src.tolist()],
        "slide": slide,
    }
    if meta:
        data["source"] = meta
    return data


B_KEY, AZ_KEY = 16001, 11005
DERIVED_B_NOTE = ("curl of Az per element: the result file has no element B at this step (seen on 23.0 for every run "
                  "after the first in a project; cause not isolated); equals JMAG's element B to 1e-8 T")


def derive_b(mesh: Mesh, az_ids, az_vals, displacement=None):
    """Element B = curl(Az z) from nodal Az [Wb/m] at the step's node positions [m]: (Bx, By) = (dAz/dy, -dAz/dx).
    Triangles: the constant gradient of the linear element; quads: the bilinear gradient at the element centre.
    Measured on JMAG-Designer 23.0 (12s/4p SPM): equals JMAG's element B (key 16001) to 1e-8 T on triangles and
    2e-14 T on the slide-layer quads. Returns (element ids, (E, 3) components) like JplotFile.component."""
    np = _np()
    index = {int(i): k for k, i in enumerate(mesh.node_ids.tolist())}
    pos = mesh.xyz[:, :2].copy()
    if displacement is not None and len(displacement[0]):
        pos[[index[int(i)] for i in displacement[0]]] += np.asarray(displacement[1])[:, :2]
    a = np.full(len(pos), np.nan)
    a[[index[int(i)] for i in az_ids]] = np.asarray(az_vals)[:, 2]
    out = np.zeros((len(mesh.conn), 3))
    xi, et = np.array([-1.0, 1.0, 1.0, -1.0]) / 4, np.array([-1.0, -1.0, 1.0, 1.0]) / 4
    for e, c in enumerate(mesh.conn):
        k = [index[n] for n in c]
        P, A = pos[k], a[k]
        if len(c) == 3:
            M = np.array([P[1] - P[0], P[2] - P[0]])
            r = np.array([A[1] - A[0], A[2] - A[0]])
        else:
            M = np.array([[xi @ P[:, 0], xi @ P[:, 1]], [et @ P[:, 0], et @ P[:, 1]]])
            r = np.array([xi @ A, et @ A])
        gx, gy = np.linalg.solve(M, r)
        out[e] = (gy, -gx, 0.0)
    return np.asarray(mesh.elem_ids), out


def reconcile_parts(mesh: Mesh, jp_ids, jp_parts, jp_types, jp_centres, tol: float = 1e-9) -> list[str]:
    """Check that `mesh` is the mesh of the result file and take the per-element part ids from the jplot
    (authoritative: its part titles name them). Refuses (FieldDataError) when element ids, types or centroids
    differ - then the connectivity belongs to another mesh. Returns warnings (e.g. part ids remapped, as for a
    study.WriteMeshJcf mesh, which numbers JMAG's air regions differently). Mutates mesh.elem_part."""
    np = _np()
    jp_ids = np.asarray(jp_ids, dtype=np.int64)
    if len(jp_ids) != len(mesh.elem_ids) or set(jp_ids.tolist()) != set(mesh.elem_ids.tolist()):
        raise FieldDataError(f"mesh file ({len(mesh.elem_ids)} elements) and result file ({len(jp_ids)}) differ",
                             "the mesh was changed after the run; re-run the study or export its own mesh")
    order = {int(i): k for k, i in enumerate(jp_ids.tolist())}
    rows = np.array([order[int(i)] for i in mesh.elem_ids.tolist()], dtype=np.int64)
    if np.any(np.asarray(jp_types)[rows] != mesh.elem_type):
        raise FieldDataError("element types of the mesh file and the result file differ",
                             "the mesh file belongs to another mesh; re-run the study to get a matching mesh")
    index = {int(i): k for k, i in enumerate(mesh.node_ids.tolist())}
    cen = np.array([mesh.xyz[[index[n] for n in c], :2].mean(axis=0) for c in mesh.conn])
    span = float(np.max(np.ptp(mesh.xyz[:, :2], axis=0))) or 1.0
    dev = float(np.max(np.hypot(*(cen - np.asarray(jp_centres)[rows, :2]).T)))
    if dev > tol * span:
        raise FieldDataError(f"element centres of the mesh file and the result file differ by up to {dev:.3g} m",
                             "the mesh file belongs to another mesh; re-run the study to get a matching mesh")
    jpart = np.asarray(jp_parts, dtype=np.int64)[rows]
    diff = int(np.sum(jpart != mesh.elem_part))
    warnings = []
    if diff:
        pairs = sorted({(int(a), int(b)) for a, b in zip(mesh.elem_part[jpart != mesh.elem_part], jpart[jpart != mesh.elem_part])})
        warnings.append(f"{diff} elements had different part ids in the mesh file than in the result file "
                        f"(mesh -> result: {pairs[:8]}); part ids taken from the result file")
        mesh.elem_part = jpart
    return warnings


def steps_summary(steps: Sequence[int]) -> dict:
    """{"count", "first", "last", "steps" (only when not contiguous and at most 500)}."""
    st = sorted(int(k) for k in steps)
    out: dict[str, Any] = {"count": len(st), "first": st[0] if st else None, "last": st[-1] if st else None}
    if st and st != list(range(st[0], st[-1] + 1)) and len(st) <= 500:
        out["steps"] = st
    return out


def export(home, jplot: str, mesh_path: str, step: int | None = None, quantities: Sequence[str] = ("B", "Az"),
           length_unit: str = "mm", time: float | None = None, case: int | None = None,
           meta: dict | None = None, max_step: int | None = None, times: Sequence[float] | None = None) -> dict:
    """Read a solved 2D study's jplot + mesh at 1-based `step` (None = last step stored in the file) and assemble
    the field-data dict. The available steps are those for which the file has the first requested quantity
    (probed 1..max_step, default 100000; creating a reader costs microseconds); times[step - 1] gives "time"."""
    mesh = read_mesh_jcf(mesh_path)
    parsed = [parse_quantity(n) for n in quantities]
    with JplotFile(home, jplot) as jp:
        titles = jp.parts()
        e_ids, e_part, e_type, e_ctr, _ = jp.elements()
        warnings = reconcile_parts(mesh, e_ids, e_part, e_type, e_ctr)
        upto = max_step or 100000
        by_key: dict[int, set] = {}

        def steps_of(k: int) -> set:
            if k not in by_key:
                by_key[k] = set(jp.available_steps(k, upto))
            return by_key[k]

        sets = []
        for name, q in (parsed or [("B", QUANTITIES["B"])]):
            st = steps_of(q.key)
            if q.key == B_KEY:          # element B may be missing: derived from Az then (see derive_b)
                st = st | steps_of(AZ_KEY)
            if not st:
                raise FieldDataError(f"result file has no steps with {name} (jplot key {q.key})",
                                     "is this quantity an output of the study (study properties -> output settings)?")
            sets.append(st)
        avail = sorted(set.intersection(*sets))
        summary = steps_summary(avail)
        if not avail:
            raise FieldDataError("the requested quantities share no stored step", "export them one at a time")
        if step is None:
            step = avail[-1]
        if int(step) not in set(avail):
            raise FieldDataError(f"step {step} is not stored in the result file",
                                 f"stored steps: {summary['count']} from {summary['first']} to {summary['last']}"
                                 + (f" ({summary['steps'][:20]}...)" if "steps" in summary else "")
                                 + "; output-step control may store only some steps")
        step = int(step)
        vals = {}
        derived = []
        disp = jp.displacement(step)
        for name, q in parsed:
            got = jp.component(step, q.key)
            if got is None and q.key == B_KEY:
                az = jp.component(step, AZ_KEY)
                if az is not None:
                    got = derive_b(mesh, az[0], az[1], disp)
                    derived.append(name)
            if got is None:
                raise FieldDataError(f"result file has no {name} (jplot key {q.key}) at step {step}",
                                     "check that the quantity is an output of this study (study properties -> "
                                     "output settings)")
            vals[name] = (q, got[0], got[1])
    if times is not None and 1 <= step <= len(times):
        time = times[step - 1]
    data = assemble(mesh, titles, vals, disp, length_unit, step, time, case, meta)
    data["available_steps"] = summary
    for name in derived:
        data["quantities"][name]["source"] = DERIVED_B_NOTE
    if warnings:
        data["warnings"] = warnings
    return data


# ------------------------------------------------------------------------------------------------ analysis

def _fd_arrays(data: dict, quantity: str):
    np = _np()
    if quantity not in (data.get("values") or {}):
        raise FieldDataError(f"quantity {quantity!r} not in the field data; values: {sorted(data.get('values') or {})}",
                             "export it with jmag_field_data(quantities=[...])")
    xy = np.asarray(data["nodes"], dtype=float)
    tris = np.asarray(data["elements"], dtype=np.int64)
    v = np.array([np.nan if a is None else a for a in data["values"][quantity]], dtype=float)
    if v.size == len(xy):
        loc = "node"
    elif v.size == len(tris):
        loc = "element"
    else:
        raise FieldDataError(f"values[{quantity!r}] has {v.size} entries, expected {len(xy)} or {len(tris)}")
    return xy, tris, v, loc


def element_values(data: dict, quantity: str):
    """Per-triangle values (nodal quantities: mean of the 3 nodes, the convention plotting.field uses)."""
    np = _np()
    xy, tris, v, loc = _fd_arrays(data, quantity)
    return v[tris].mean(axis=1) if loc == "node" else v


def nodal_average(data: dict, quantity: str) -> list:
    """Area-weighted average of an elemental quantity onto the nodes (None where no element has a value)."""
    np = _np()
    xy, tris, v, loc = _fd_arrays(data, quantity)
    if loc == "node":
        return [None if not math.isfinite(a) else float(a) for a in v.tolist()]
    out = element_to_nodal(len(xy), tris, v, tri_areas(xy, tris))
    return [None if not math.isfinite(a) else float(a) for a in out.tolist()]


def part_stats(data: dict, quantity: str) -> dict:
    """{"per_part": {name: {max, min, mean (area-weighted), area, at_max [x, y], elements}}, "max", "min",
    "at_max", "part_at_max", "unit"} from per-triangle values (nodal: mean of 3 nodes). at_max = triangle
    centroid. Parts without a value (e.g. J outside the coils) report elements and area only."""
    np = _np()
    xy, tris, _, _ = _fd_arrays(data, quantity)
    ev = element_values(data, quantity)
    w = tri_areas(xy, tris)
    ep = np.asarray(data["element_part"], dtype=np.int64)
    cen = xy[tris].mean(axis=1)
    per: dict[str, dict] = {}
    gmax, gmin, garg = -math.inf, math.inf, None
    for k, name in enumerate(data["parts"]):
        sel = ep == k
        if not sel.any():
            continue
        row: dict[str, Any] = {"elements": int(sel.sum()), "area": float(w[sel].sum())}
        vv, ww = ev[sel], w[sel]
        ok = np.isfinite(vv)
        if ok.any():
            j = int(np.flatnonzero(sel)[np.flatnonzero(ok)[int(np.argmax(vv[ok]))]])
            row.update({"max": float(vv[ok].max()), "min": float(vv[ok].min()),
                        "mean": float(np.sum(vv[ok] * ww[ok]) / np.sum(ww[ok])) if np.sum(ww[ok]) > 0 else None,
                        "at_max": [round(float(c), 6) for c in cen[j]]})
            if row["max"] > gmax:
                gmax, garg = row["max"], (name, row["at_max"])
            gmin = min(gmin, row["min"])
        per[name] = row
    unit = ((data.get("quantities") or {}).get(quantity) or {}).get("unit", "")
    out: dict[str, Any] = {"quantity": quantity, "unit": unit, "per_part": per}
    if garg is not None:
        out.update({"max": gmax, "min": gmin, "at_max": garg[1], "part_at_max": garg[0]})
    return out


# ------------------------------------------------------------------------------------------------ probes

def circle_points(r: float, n: int = 360, cx: float = 0.0, cy: float = 0.0, start_deg: float = 0.0) -> list:
    """n points on a circle (endpoint excluded, so the samples are exactly one revolution for a DFT)."""
    if not (r > 0) or int(n) < 4:
        raise FieldDataError("circle needs r > 0 and n >= 4")
    return [[cx + r * math.cos(math.radians(start_deg) + 2 * math.pi * k / n),
             cy + r * math.sin(math.radians(start_deg) + 2 * math.pi * k / n)] for k in range(int(n))]


def segment_points(p0: Sequence[float], p1: Sequence[float], n: int = 100) -> list:
    """n points from p0 to p1, both ends included."""
    if int(n) < 2:
        raise FieldDataError("segment needs n >= 2")
    return [[p0[0] + (p1[0] - p0[0]) * k / (n - 1), p0[1] + (p1[1] - p0[1]) * k / (n - 1)] for k in range(int(n))]


def locate(data: dict, points: Sequence[Sequence[float]], chunk: int = 64):
    """Containing triangle (or -1) and barycentric coordinates for each point (numpy only, any mesh)."""
    np = _np()
    xy = np.asarray(data["nodes"], dtype=float)
    tris = np.asarray(data["elements"], dtype=np.int64)
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    a, b, c = xy[tris[:, 0]], xy[tris[:, 1]], xy[tris[:, 2]]
    det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    good = np.abs(det) > 0
    det = np.where(good, det, 1.0)
    lo, hi = np.minimum(np.minimum(a, b), c), np.maximum(np.maximum(a, b), c)
    span = float(np.max(hi - lo)) if len(tris) else 1.0
    tol = 1e-9
    tri = np.full(len(pts), -1, dtype=np.int64)
    bary = np.zeros((len(pts), 3))
    eps = 1e-12 * max(span, 1.0)
    for s0 in range(0, len(pts), chunk):
        p = pts[s0:s0 + chunk]
        inbox = ((p[:, None, 0] >= lo[None, :, 0] - eps) & (p[:, None, 0] <= hi[None, :, 0] + eps) &
                 (p[:, None, 1] >= lo[None, :, 1] - eps) & (p[:, None, 1] <= hi[None, :, 1] + eps) & good[None, :])
        for r, cand in enumerate(inbox):
            ks = np.flatnonzero(cand)
            if not ks.size:
                continue
            q = p[r]
            l1 = ((b[ks, 0] - q[0]) * (c[ks, 1] - q[1]) - (c[ks, 0] - q[0]) * (b[ks, 1] - q[1])) / det[ks]
            l2 = ((c[ks, 0] - q[0]) * (a[ks, 1] - q[1]) - (a[ks, 0] - q[0]) * (c[ks, 1] - q[1])) / det[ks]
            l3 = 1.0 - l1 - l2
            inside = (l1 >= -tol) & (l2 >= -tol) & (l3 >= -tol)
            if inside.any():
                m = int(np.flatnonzero(inside)[0])
                tri[s0 + r] = ks[m]
                bary[s0 + r] = (l1[m], l2[m], l3[m])
    return tri, bary


DERIVED = {"Br": ("Bx", "By"), "Bt": ("Bx", "By")}


def probe_many(data: dict, quantities: Sequence[str], points: Sequence[Sequence[float]], method: str = "auto",
               center: Sequence[float] = (0.0, 0.0)) -> dict:
    """probe() for several quantities from one point location: {name: {"values", "inside", "method",
    "location"}}. Br / Bt reuse the Bx / By evaluation; nodal averages are computed once per quantity."""
    np = _np()
    if method not in ("auto", "element", "nodal"):
        raise FieldDataError(f"method {method!r} unknown", "use auto, element or nodal")
    pts = np.asarray(points, dtype=float).reshape(-1, 2)
    tri, bary = locate(data, pts)
    inside = int((tri >= 0).sum())
    tris = np.asarray(data["elements"], dtype=np.int64)
    cache: dict[str, dict] = {}

    def base(q: str) -> dict:
        if q in cache:
            return cache[q]
        _, _, v, loc = _fd_arrays(data, q)
        if loc == "element" and method in ("auto", "element"):
            vals = [None if t < 0 or not math.isfinite(v[t]) else float(v[t]) for t in tri.tolist()]
            used = "element"
        else:
            nv = v if loc == "node" else np.array([np.nan if a is None else a for a in nodal_average(data, q)])
            vals = []
            for t, l in zip(tri.tolist(), bary):
                y = float(np.dot(l, nv[tris[t]])) if t >= 0 else math.nan
                vals.append(y if math.isfinite(y) else None)
            used = "linear" if loc == "node" else "nodal"
        cache[q] = {"values": vals, "inside": inside, "method": used, "location": loc}
        return cache[q]

    th = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    out = {}
    for q in quantities:
        if q in DERIVED:
            bx, by = base("Bx"), base("By")
            vals = []
            for x, y, t in zip(bx["values"], by["values"], th):
                if x is None or y is None:
                    vals.append(None)
                elif q == "Br":
                    vals.append(float(x * math.cos(t) + y * math.sin(t)))
                else:
                    vals.append(float(-x * math.sin(t) + y * math.cos(t)))
            out[q] = {"values": vals, "inside": inside, "method": bx["method"], "location": bx["location"]}
        else:
            out[q] = base(q)
    return out


def probe(data: dict, quantity: str, points: Sequence[Sequence[float]], method: str = "auto",
          center: Sequence[float] = (0.0, 0.0)) -> dict:
    """Values of `quantity` at points [length_unit]. Nodal quantities: linear interpolation inside the containing
    triangle (exact for a linear field). Elemental ones: method "element" (default; the constant FE value of the
    containing element - for first-order elements B is constant per element, so this is the FE solution itself) or
    "nodal" (linear interpolation of the area-weighted nodal average: smoother, slightly lower peaks). Derived
    quantities "Br" / "Bt" are the radial / tangential components of (Bx, By) about `center`.
    Points outside the mesh give None. Returns {"values", "inside", "method", "location"}."""
    return probe_many(data, [quantity], points, method, center)[quantity]


# ------------------------------------------------------------------------------------------------ files

def save(data: dict, path: str | os.PathLike) -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    return str(p.resolve())


def load(path: str | os.PathLike) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise FieldDataError(f"cannot read field data {path}: {e}", "pass the path jmag_field_data returned") from None
    if not isinstance(data, dict) or "nodes" not in data or "elements" not in data:
        raise FieldDataError(f"{path} is not a field-data file (no nodes/elements)")
    return data
