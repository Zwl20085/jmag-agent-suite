"""Field export outside JMAG: mesh.bin parser, assembly, statistics, probes and the three MCP tools - on tiny
hand-written fixtures (no JMAG, no JplotReader.dll)."""
import asyncio
import json
import math
import struct
import zipfile
import zlib

import pytest

np = pytest.importorskip("numpy")

from jmag_bridge import fielddata as F  # noqa: E402
from jmag_bridge import server  # noqa: E402

# Unit square [0, 1] m split in a quad (left half, part 1) and two triangles (right half, part 2), plus a moving
# node set for the slide test. Node ids are 1-based and not in file order, like JMAG's.
NODES = {10: (0.0, 0.0), 11: (0.5, 0.0), 12: (0.5, 1.0), 13: (0.0, 1.0), 14: (1.0, 0.0), 15: (1.0, 1.0)}
ELEMS = [(7, 3, 1, (10, 11, 12, 13)), (8, 2, 2, (11, 14, 15)), (9, 2, 2, (11, 15, 12))]


def mesh_bin(nodes=NODES, elems=ELEMS, prefix=b"\x7f\x7f~}~\x7f", trailing=b""):
    head = struct.pack("<4s5i", b"MJ\x00\x00", 1, len(nodes), len(elems), 1, 7).ljust(64, b"\x00")
    body = b"".join(struct.pack("<i3d", i, x, y, 0.0) for i, (x, y) in nodes.items())
    for eid, t, part, conn in elems:
        body += struct.pack(f"<4i{len(conn)}i", eid, t, 1, part, *conn)
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    return prefix + co.compress(head + body + trailing) + co.flush()


def jcf(tmp_path, blob=None):
    p = tmp_path / "Designer_input.jcf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("info.txt", "Version: 1.0\n")
        z.writestr("mesh.bin", blob if blob is not None else mesh_bin())
    return p


def values(nodal_fn=lambda x, y: 2 * x + 3 * y + 1, elem_vals=None):
    """B (element, 3 components) and Az (node) in the JplotFile.component shape."""
    ids = np.array(sorted(NODES))
    az = np.zeros((len(ids), 3))
    az[:, 2] = [nodal_fn(*NODES[i]) for i in ids]
    ev = elem_vals or {7: (0.3, 0.4, 0.0), 8: (1.0, 0.0, 0.0), 9: (0.0, -2.0, 0.0)}
    eids = np.array(sorted(ev))
    b = np.array([ev[i] for i in eids], dtype=float)
    return {"B": (F.QUANTITIES["B"], eids, b), "Bx": (F.QUANTITIES["Bx"], eids, b), "By": (F.QUANTITIES["By"], eids, b),
            "Az": (F.QUANTITIES["Az"], ids, az)}


TITLES = {1: "Core@35JN210", 2: "Magnet@N40H", 3: ""}


def data(tmp_path, **kw):
    mesh = F.read_mesh_jcf(jcf(tmp_path))
    return F.assemble(mesh, TITLES, values(**kw), None, "mm", step=3, time=0.01)


# ------------------------------------------------------------------------------------------- mesh.bin

def test_mesh_bin_round_trip(tmp_path):
    m = F.read_mesh_jcf(jcf(tmp_path))
    assert m.node_ids.tolist() == list(NODES) and m.xyz[4].tolist() == [1.0, 0.0, 0.0]
    assert m.elem_ids.tolist() == [7, 8, 9] and m.elem_type.tolist() == [3, 2, 2] and m.elem_part.tolist() == [1, 2, 2]
    assert m.conn[0] == (10, 11, 12, 13) and m.xyz.shape == (6, 3)


def test_mesh_bin_without_prefix_and_raw():
    raw = zlib.decompressobj(-15).decompress(mesh_bin()[6:])
    assert F.parse_mesh_bin(raw).elem_ids.tolist() == [7, 8, 9]              # already inflated
    assert F.parse_mesh_bin(mesh_bin(prefix=b"")).elem_ids.tolist() == [7, 8, 9]


@pytest.mark.parametrize("blob, match", [
    (b"garbage" * 20, "layout"),
    (None, "trailing"),
    ("type", "type 5"),
])
def test_mesh_bin_errors(blob, match):
    if blob is None:
        blob = mesh_bin(trailing=b"\x01\x00\x00\x00")
    elif blob == "type":
        blob = mesh_bin(elems=[(1, 5, 1, (10, 11, 12))])
    with pytest.raises(F.FieldDataError, match=match):
        F.parse_mesh_bin(blob)


def test_jcf_without_mesh_member(tmp_path):
    p = tmp_path / "x.jcf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mesh.dat", "0 3 m *Node\n")
    with pytest.raises(F.FieldDataError, match="no mesh.bin"):
        F.read_mesh_jcf(p)


# ------------------------------------------------------------------------------------------- assembly

def test_split_quads_keeps_orientation():
    tris, src = F.split_quads([(1, 2, 3, 4), (5, 6, 7)])
    assert tris == [(1, 2, 3), (1, 3, 4), (5, 6, 7)] and src == [0, 0, 1]
    with pytest.raises(F.FieldDataError):
        F.split_quads([(1, 2)])


def test_part_names_from_titles():
    ids, names, mats = F.part_names(TITLES)
    assert names == ["Core", "Magnet", "Air_3"] and mats == ["35JN210", "N40H", "Air"] and ids == [1, 2, 3]


def test_assemble_format(tmp_path):
    d = data(tmp_path)
    assert d["dimension"] == 2 and d["length_unit"] == "mm" and d["step"] == 3 and d["time"] == 0.01
    assert len(d["elements"]) == 4 and len(d["element_part"]) == 4      # quad -> 2 triangles
    assert d["element_ids"] == [7, 7, 8, 9]
    assert d["parts"] == ["Core", "Magnet", "Air_3"] and d["element_part"] == [0, 0, 1, 1]
    xs = [p[0] for p in d["nodes"]]
    assert max(xs) == 1000.0                                            # m -> mm
    assert d["values"]["B"][:2] == [0.5, 0.5] and d["values"]["B"][3] == 2.0     # |B| per triangle
    assert d["values"]["By"][3] == -2.0
    assert len(d["values"]["Az"]) == 6
    q = d["quantities"]
    assert q["B"]["location"] == "element" and q["Az"]["location"] == "node" and q["Az"]["unit"] == "Wb/m"
    json.dumps(d)                                                       # JSON-serialisable
    # every triangle counter-clockwise (positive area), total = 1 m^2 = 1e6 mm^2
    xy = np.array(d["nodes"])
    t = np.array(d["elements"])
    a = 0.5 * ((xy[t[:, 1], 0] - xy[t[:, 0], 0]) * (xy[t[:, 2], 1] - xy[t[:, 0], 1]) -
               (xy[t[:, 2], 0] - xy[t[:, 0], 0]) * (xy[t[:, 1], 1] - xy[t[:, 0], 1]))
    assert (a > 0).all() and abs(a.sum() - 1e6) < 1e-6


def test_missing_values_become_none(tmp_path):
    mesh = F.read_mesh_jcf(jcf(tmp_path))
    v = values(elem_vals={8: (1.0, 0.0, 0.0)})                       # only element 8 has a value (like J)
    d = F.assemble(mesh, TITLES, {"B": v["B"]}, None)
    assert d["values"]["B"] == [None, None, 1.0, None] and d["quantities"]["B"]["defined"] == 1


def test_assemble_refuses_3d(tmp_path):
    mesh = F.read_mesh_jcf(jcf(tmp_path))
    mesh.xyz[0, 2] = 0.1
    with pytest.raises(F.FieldDataError, match="2D"):
        F.assemble(mesh, TITLES, {}, None)


def test_displacement_moves_nodes(tmp_path):
    mesh = F.read_mesh_jcf(jcf(tmp_path))
    disp = (np.array([14, 15]), np.array([[0.1, 0.0, 0.0], [0.1, 0.0, 0.0]]))
    d = F.assemble(mesh, TITLES, {}, disp)
    k = d["node_ids"].index(14)
    assert d["nodes"][k] == [1100.0, 0.0]


def test_slide_remap_reconnects_rotated_layer():
    # a ring layer: fixed outer nodes 1..4, moving inner nodes 11..14 on a square; rotating the inner nodes by one
    # position must reconnect each straddling element to the node that now sits where its node was.
    xyz0 = np.array([[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0],
                     [.5, 0, 0], [0, .5, 0], [-.5, 0, 0], [0, -.5, 0]], dtype=float)
    ids = [1, 2, 3, 4, 11, 12, 13, 14]
    index = {i: k for k, i in enumerate(ids)}
    conn = [(11, 1, 2, 12), (12, 2, 3, 13), (13, 3, 4, 14), (14, 4, 1, 11)]
    pos = xyz0.copy()
    pos[4:] = xyz0[[5, 6, 7, 4]]          # inner node 11 moved to where 12 was, etc. (90 deg)
    new, n, worst = F.slide_remap(conn, xyz0, pos, [11, 12, 13, 14], index)
    assert n == 4 and worst < 1e-12
    assert new[0] == (14, 1, 2, 11)       # node now at (0.5, 0) is 14
    for c in new:                          # reconnected elements are not sheared: same geometry as at step 1
        assert [tuple(pos[index[i], :2]) for i in c if i > 10] in (
            [tuple(xyz0[index[j], :2]) for j in o if j > 10] for o in conn)


# ------------------------------------------------------------------------------------------- statistics

def test_part_stats(tmp_path):
    d = data(tmp_path)
    s = F.part_stats(d, "B")
    core, mag = s["per_part"]["Core"], s["per_part"]["Magnet"]
    assert core["max"] == 0.5 and core["mean"] == 0.5 and core["elements"] == 2 and abs(core["area"] - 5e5) < 1e-6
    assert mag["max"] == 2.0 and mag["min"] == 1.0 and abs(mag["mean"] - 1.5) < 1e-12
    assert s["max"] == 2.0 and s["part_at_max"] == "Magnet" and s["unit"] == "T"
    assert "Air_3" not in s["per_part"]                                 # no elements
    # nodal quantity: element value = mean of its 3 nodes
    az = F.part_stats(d, "Az")
    assert az["per_part"]["Magnet"]["max"] > az["per_part"]["Core"]["min"]


def test_nodal_average(tmp_path):
    d = data(tmp_path, elem_vals={7: (1.0, 0, 0), 8: (3.0, 0, 0), 9: (3.0, 0, 0)})
    nv = F.nodal_average(d, "B")
    k10, k14, k11 = (d["node_ids"].index(i) for i in (10, 14, 11))
    assert nv[k10] == 1.0 and nv[k14] == 3.0 and 1.0 < nv[k11] < 3.0


# ------------------------------------------------------------------------------------------- probes

def test_probe_linear_field_is_exact(tmp_path):
    d = data(tmp_path)                                  # Az = 2x + 3y + 1 with x, y in m; nodes in mm
    pts = [[100.0, 200.0], [750.0, 250.0], [499.0, 999.0], [1000.0, 1000.0]]
    r = F.probe(d, "Az", pts)
    want = [2 * x / 1e3 + 3 * y / 1e3 + 1 for x, y in pts]
    assert r["method"] == "linear" and r["inside"] == 4
    assert all(abs(a - b) < 1e-12 for a, b in zip(r["values"], want))


def test_probe_element_and_outside(tmp_path):
    d = data(tmp_path)
    r = F.probe(d, "B", [[250.0, 500.0], [900.0, 100.0], [2000.0, 0.0]])
    assert r["values"] == [0.5, 1.0, None] and r["method"] == "element" and r["inside"] == 2
    rn = F.probe(d, "B", [[0.0, 0.0]], method="nodal")
    assert rn["method"] == "nodal" and rn["values"][0] == 0.5


def test_probe_radial_tangential(tmp_path):
    d = data(tmp_path, elem_vals={7: (1.0, 0.0, 0.0), 8: (1.0, 0.0, 0.0), 9: (1.0, 0.0, 0.0)})   # uniform Bx = 1
    r = F.probe(d, "Br", [[600.0, 600.0]], center=(500.0, 600.0))      # point due "east" of the centre
    t = F.probe(d, "Bt", [[500.0, 900.0]], center=(500.0, 600.0))      # due "north": tangential = -Bx
    assert abs(r["values"][0] - 1.0) < 1e-12 and abs(t["values"][0] + 1.0) < 1e-12


def test_path_points():
    c = F.circle_points(2.0, 4, 1.0, 0.0)
    assert len(c) == 4 and c[0] == [3.0, 0.0] and abs(c[1][1] - 2.0) < 1e-12   # endpoint excluded
    s = F.segment_points([0, 0], [1, 2], 3)
    assert s == [[0.0, 0.0], [0.5, 1.0], [1.0, 2.0]]
    with pytest.raises(F.FieldDataError):
        F.circle_points(0, 10)


def test_parse_quantity():
    assert F.parse_quantity("B")[1].key == 16001
    name, q = F.parse_quantity("key:16505:element:abs")
    assert q.key == 16505 and q.location == "element" and not q.verified
    for bad in ("Bogus", "key:1:x:0", "key:abc"):
        with pytest.raises(F.FieldDataError):
            F.parse_quantity(bad)


def test_save_load(tmp_path):
    d = data(tmp_path)
    p = F.save(d, tmp_path / "f.json")
    assert F.load(p)["parts"] == d["parts"]
    (tmp_path / "bad.json").write_text("{}")
    with pytest.raises(F.FieldDataError):
        F.load(tmp_path / "bad.json")


def test_missing_dll_has_hint(tmp_path):
    with pytest.raises(F.FieldDataError, match="JplotReader.dll") as e:
        F.dll_path(tmp_path)
    assert e.value.hint


# ------------------------------------------------------------------------------------------- MCP tools

class FakeBridge:
    def __init__(self, export_result):
        self.calls, self.install, self.job = [], None, None
        self.export_result = export_result
        self.journal = server.B.journal
        self.session = "test"

    async def call(self, op, timeout=600, **a):
        self.calls.append((op, a))
        if op == "field_export":
            return {"ok": True, "result": self.export_result}
        return {"ok": False, "error": {"message": f"op {op} not faked"}}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    d = data(tmp_path)
    jplot = tmp_path / "Designer.jplot"
    jplot.write_bytes(b"x")
    fb = FakeBridge({"study": "Load", "model": "M", "case": None, "jplot": str(jplot), "mesh": "m.jcf",
                     "mesh_source": "run", "solved_steps": 5, "step": 3, "time": 0.01, "parts": ["Core", "Magnet"]})
    monkeypatch.setattr(server, "B", fb)
    seen = []

    def export(home, jp, mesh, step, quantities, *a, **k):
        seen.append(tuple(quantities))
        return {**d, "values": {q: d["values"][q] for q in quantities}, "source": {"solved_steps": 5}}
    monkeypatch.setattr(F, "export", export)
    server._FIELD_CACHE.clear()
    fb.seen = seen
    return fb


def test_field_data_tool(fake, tmp_path):
    r = asyncio.run(server.jmag_field_data("Load", quantities=["B", "Az"], out=str(tmp_path / "o.json")))
    assert r["ok"], r
    res = r["result"]
    assert res["elements"] == 4 and res["nodes"] == 6 and res["max"]["B"] == 2.0 and res["part_at_max"]["B"] == "Magnet"
    assert "values" not in res and "nodes" in json.load(open(res["path"]))
    assert res["per_part"]["B"]["Core"]["max"] == 0.5
    # second call: served from the cache (same jplot mtime, step, quantities)
    asyncio.run(server.jmag_field_data("Load", quantities=["Az", "B"], out=str(tmp_path / "o2.json")))
    assert len(fake.seen) == 1


def test_field_data_rejects_unknown_quantity_before_jmag(fake):
    r = asyncio.run(server.jmag_field_data("Load", quantities=["Bogus"]))
    assert not r["ok"] and fake.calls == []


def test_field_plot_tool_and_replot_from_file(fake, tmp_path):
    pytest.importorskip("matplotlib")
    if not hasattr(server.plotting, "field"):
        pytest.skip("renderer not available")
    out = asyncio.run(server.jmag_field_plot("Load", out=str(tmp_path / "f.png")))
    env = json.loads(out[0])
    assert env["ok"], env
    assert len(out) == 2 and env["result"]["annotations"]["max"] == 2.0
    saved = env["result"]["data_path"]
    out2 = asyncio.run(server.jmag_field_plot(data_path=saved, hide_air=True, kind="mesh", out=str(tmp_path / "m.png")))
    assert json.loads(out2[0])["ok"]
    assert fake.calls.count(("field_export", fake.calls[0][1])) == 1      # re-plot did not touch JMAG


def test_field_probe_tool_segment_and_circle(fake, tmp_path):
    pytest.importorskip("matplotlib")
    out = asyncio.run(server.jmag_field_probe("Load", quantity="Az", segment={"from": [0, 0], "to": [1000, 1000], "n": 5},
                                              out=str(tmp_path / "p.png")))
    env = json.loads(out[0])
    assert env["ok"], env
    v = env["result"]["values"]["Az"]
    assert abs(v[0] - 1.0) < 1e-9 and abs(v[-1] - 6.0) < 1e-9 and env["result"]["x"][-1] == pytest.approx(1414.21, 1e-5)
    out = asyncio.run(server.jmag_field_probe("Load", circle={"r": 400, "n": 36, "cx": 500, "cy": 500}, pole_pairs=1))
    env = json.loads(out[0])
    assert env["ok"] and set(env["result"]["values"]) == {"B", "Br", "Bt"}
    assert "B" not in env["result"]["harmonics"] and "Br" in env["result"]["harmonics"]
    bad = json.loads(asyncio.run(server.jmag_field_probe("Load"))[0])
    assert not bad["ok"]


def test_probe_harmonics_electrical_orders():
    n = 360
    y = [math.cos(2 * 2 * math.pi * k / n) + 0.1 * math.cos(3 * 2 * 2 * math.pi * k / n) for k in range(n)]
    h = server._probe_harmonics(y, 2)
    assert h["fundamental_order"] == 2 and abs(h["fundamental"] - 1.0) < 1e-9
    assert abs(h["electrical_harmonics"]["3"] - 0.1) < 1e-9 and abs(h["thd_pct"] - 10.0) < 1e-6
    assert server._probe_harmonics([1.0, None, 2.0, 3.0], 2) is None


def test_field_data_per_part_defaults_to_b_and_j(fake, tmp_path):
    r = asyncio.run(server.jmag_field_data("Load", quantities=["B", "Az"], out=str(tmp_path / "o.json")))
    assert set(r["result"]["per_part"]) == {"B"}                        # Az only on request
    r = asyncio.run(server.jmag_field_data("Load", quantities=["B", "Az"], out=str(tmp_path / "o.json"),
                                           per_part=["Az"]))
    assert set(r["result"]["per_part"]) == {"Az"}


def test_field_plot_smooth_keeps_element_statistics(fake, tmp_path):
    pytest.importorskip("matplotlib")
    smooth = json.loads(asyncio.run(server.jmag_field_plot("Load", out=str(tmp_path / "s.png")))[0])["result"]
    flat = json.loads(asyncio.run(server.jmag_field_plot("Load", smooth=False, out=str(tmp_path / "f.png")))[0])["result"]
    a, b = smooth["annotations"], flat["annotations"]
    assert a["values"] == "element" and a["shading"] == "nodal-averaged per part" and b["shading"] == "flat per element"
    assert a["max"] == b["max"] == 2.0 and a["at_max"] == b["at_max"] and a["per_part"] == b["per_part"]


def test_field_plot_hides_air_by_default(fake, tmp_path):
    pytest.importorskip("matplotlib")
    d = data(tmp_path)
    d["part_materials"] = ["35JN210", "Air", "Air"]                     # pretend the right half is air
    p = F.save(d, tmp_path / "air.json")
    for kind in ("field", "mesh"):
        hid = json.loads(asyncio.run(server.jmag_field_plot(data_path=p, kind=kind, flux_lines=False,
                                                            out=str(tmp_path / f"{kind}1.png")))[0])["result"]
        full = json.loads(asyncio.run(server.jmag_field_plot(data_path=p, kind=kind, flux_lines=False, hide_air=False,
                                                             out=str(tmp_path / f"{kind}2.png")))[0])["result"]
        assert hid["annotations"]["bbox"][2] == 500.0 and full["annotations"]["bbox"][2] == 1000.0   # zoomed


def test_probe_figure_has_harmonic_panel(fake, tmp_path):
    pytest.importorskip("matplotlib")
    out = asyncio.run(server.jmag_field_probe("Load", quantity="Br", circle={"r": 400, "n": 36, "cx": 500, "cy": 500},
                                              pole_pairs=1, out=str(tmp_path / "p.png")))
    env = json.loads(out[0])
    assert env["ok"] and set(env["result"]["values"]) == {"Br"} and "bars" in env["result"]["annotations"]
