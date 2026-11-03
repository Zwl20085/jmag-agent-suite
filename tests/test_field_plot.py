"""Field / mesh renderer on a synthetic annulus mesh (offline, Agg). Expected numbers are computed by hand
with plain Python loops, independently of the numpy code in plotting.py."""
import json
import math

import pytest

pytest.importorskip("matplotlib")

from jmag_bridge import plotting  # noqa: E402

NA = 30                      # nodes around
RADII = [10.0, 14.0, 18.0, 22.0]   # 4 node rings -> 3 element rings: ring 0 Rotor, rings 1-2 Stator


def _png(path):
    with open(path, "rb") as f:
        return f.read(8) == b"\x89PNG\r\n\x1a\n"


def _annulus():
    nodes = []
    for r in RADII:
        for j in range(NA):
            t = 2 * math.pi * j / NA
            nodes.append([r * math.cos(t), r * math.sin(t)])
    els, ep = [], []
    for i in range(len(RADII) - 1):
        for j in range(NA):
            a, b = i * NA + j, i * NA + (j + 1) % NA
            c, d = (i + 1) * NA + (j + 1) % NA, (i + 1) * NA + j
            p = 0 if i == 0 else 1
            els += [[a, b, c], [a, c, d]]
            ep += [p, p]
    b = [2.0 + x / 22.0 + 0.1 * y / 22.0 for x, y in nodes]           # max 3.0 at (22, 0), min 1.0 at (-22, 0)
    az = [0.01 * y / (x * x + y * y) ** 0.5 for x, y in nodes]        # sin(theta)-like flux function
    demag = [0.001 * k for k in range(len(els))]                       # elemental
    return {"dimension": 2, "length_unit": "mm", "nodes": nodes, "elements": els, "element_part": ep,
            "parts": ["Rotor", "Stator"], "values": {"B": b, "Az": az, "Demag": demag},
            "quantities": {"B": {"result_type": "MagneticFluxDensity", "component": "Absolute", "unit": "T"},
                           "Az": {"result_type": "MagneticVectorPotential", "component": "Z", "unit": "Wb/m"},
                           "Demag": {"unit": "%"}},
            "step": 60, "time": 0.0198, "case": None}


def _area(p, q, r):
    return 0.5 * abs((q[0] - p[0]) * (r[1] - p[1]) - (r[0] - p[0]) * (q[1] - p[1]))


def _hand_stats(d, elem_vals):
    """Per-part max/min/area-weighted mean of element values, plus the global area-weighted mean."""
    acc = {}
    tot_w = tot_a = 0.0
    for k, (el, p) in enumerate(zip(d["elements"], d["element_part"])):
        a = _area(*(d["nodes"][i] for i in el))
        v = elem_vals[k]
        s = acc.setdefault(d["parts"][p], {"max": -1e300, "min": 1e300, "w": 0.0, "a": 0.0})
        s["max"], s["min"] = max(s["max"], v), min(s["min"], v)
        s["w"] += v * a
        s["a"] += a
        tot_w += v * a
        tot_a += a
    return {n: {"max": s["max"], "min": s["min"], "mean": s["w"] / s["a"]} for n, s in acc.items()}, tot_w / tot_a


def test_nodal_field_annotations(tmp_path):
    d = _annulus()
    r = plotting.field(d, str(tmp_path / "f.png"))
    assert _png(r["path"])
    a = r["annotations"]
    b = d["values"]["B"]
    assert a["quantity"] == "B" and a["unit"] == "T" and a["value_location"] == "node"
    assert a["max"] == pytest.approx(max(b), abs=1e-9) and a["max"] == pytest.approx(3.0, abs=1e-9)
    assert a["min"] == pytest.approx(min(b), abs=1e-9)
    assert a["at_max"] == pytest.approx([22.0, 0.0], abs=1e-9)
    assert a["part_at_max"] == "Stator"
    elem = [sum(b[i] for i in el) / 3 for el in d["elements"]]
    per, mean = _hand_stats(d, elem)
    assert a["mean"] == pytest.approx(mean, abs=1e-9)
    assert set(a["per_part"]) == {"Rotor", "Stator"}
    for name, s in per.items():
        for k in ("max", "min", "mean"):
            assert a["per_part"][name][k] == pytest.approx(s[k], abs=1e-9), (name, k)
    assert a["nodes"] == NA * len(RADII) and a["elements"] == 2 * NA * (len(RADII) - 1)
    assert a["step"] == 60 and a["time"] == 0.0198
    xs = [p[0] for p in d["nodes"]]
    ys = [p[1] for p in d["nodes"]]
    assert a["bbox"] == pytest.approx([min(xs), min(ys), max(xs), max(ys)], abs=1e-9)
    assert a["flux_lines"] == 24
    assert a["outline_edges"] == 3 * NA        # inner circle + outer circle + Rotor/Stator interface
    json.dumps(a)                               # annotations are JSON-serialisable


def test_elemental_field_annotations(tmp_path):
    d = _annulus()
    r = plotting.field(d, str(tmp_path / "e.png"), quantity="Demag", flux_lines=False, show_mesh=True)
    assert _png(r["path"])
    a = r["annotations"]
    dm = d["values"]["Demag"]
    per, mean = _hand_stats(d, dm)
    assert a["value_location"] == "element" and a["unit"] == "%"
    assert a["max"] == pytest.approx(max(dm), abs=1e-9) and a["min"] == pytest.approx(0.0, abs=1e-9)
    assert a["mean"] == pytest.approx(mean, abs=1e-9)
    for name, s in per.items():
        for k in ("max", "min", "mean"):
            assert a["per_part"][name][k] == pytest.approx(s[k], abs=1e-9), (name, k)
    last = d["elements"][-1]
    cx = sum(d["nodes"][i][0] for i in last) / 3
    cy = sum(d["nodes"][i][1] for i in last) / 3
    assert a["at_max"] == pytest.approx([cx, cy], abs=1e-9) and a["part_at_max"] == "Stator"
    assert a["flux_lines"] == 0


def test_elemental_az_flux_lines_and_clipping(tmp_path):
    d = _annulus()
    az = d["values"]["Az"]
    d["values"]["Az"] = [sum(az[i] for i in el) / 3 for el in d["elements"]]   # elemental Az -> averaged to nodes
    r = plotting.field(d, str(tmp_path / "c.png"), flux_levels=10, vmin=1.5, vmax=2.5, cmap="viridis",
                       title="custom title")
    assert r["annotations"]["flux_lines"] == 10
    assert r["annotations"]["max"] == pytest.approx(3.0, abs=1e-9)   # stats ignore the colour clipping


def test_parts_filter_changes_bbox_and_stats(tmp_path):
    d = _annulus()
    r = plotting.field(d, str(tmp_path / "rotor.png"), parts=["Rotor"])
    a = r["annotations"]
    inner = [p for p in d["nodes"] if math.hypot(*p) < 14.5]
    xs, ys = [p[0] for p in inner], [p[1] for p in inner]
    assert a["bbox"] == pytest.approx([min(xs), min(ys), max(xs), max(ys)], abs=1e-9)
    assert a["bbox"][2] == pytest.approx(14.0, abs=1e-9)
    assert list(a["per_part"]) == ["Rotor"] and a["elements"] == 2 * NA and a["nodes"] == 2 * NA
    assert a["max"] == pytest.approx(max(2.0 + x / 22.0 + 0.1 * y / 22.0 for x, y in inner), abs=1e-9)
    assert a["part_at_max"] == "Rotor"
    assert a["outline_edges"] == 2 * NA        # only the Rotor ring's two circles
    with pytest.raises(ValueError, match="not in the field data"):
        plotting.field(d, str(tmp_path / "x.png"), parts=["Air"])


def test_validate_field_data(tmp_path):
    d = _annulus()
    s = plotting.validate_field_data(d)
    assert s["nodes"] == 120 and s["elements"] == 180 and s["parts"] == ["Rotor", "Stator"]
    assert s["values"] == {"B": "node", "Az": "node", "Demag": "element"}

    bad = _annulus()
    bad["elements"][5][1] = len(bad["nodes"])
    with pytest.raises(ValueError, match=r"elements\[5\].*references a node"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    bad["values"]["B"] = bad["values"]["B"][:-1]
    with pytest.raises(ValueError, match=r"values\['B'\] has 119 entries; expected 120 .* or 180"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    bad["element_part"][3] = 2
    with pytest.raises(ValueError, match="not an index into parts"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    bad["element_part"] = bad["element_part"][:-1]
    with pytest.raises(ValueError, match="element_part has 179 entries"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    bad["elements"] = [el + [0] for el in bad["elements"]]
    with pytest.raises(ValueError, match="triangles"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    bad["dimension"] = 3
    with pytest.raises(ValueError, match="only 2D"):
        plotting.validate_field_data(bad)
    bad = _annulus()
    del bad["nodes"]
    with pytest.raises(ValueError, match="no 'nodes'"):
        plotting.validate_field_data(bad)
    # the renderer validates too, and names the available quantities
    bad = _annulus()
    bad["elements"][0][0] = -1
    with pytest.raises(ValueError, match="references a node"):
        plotting.field(bad, str(tmp_path / "x.png"))
    with pytest.raises(ValueError, match="quantity 'H' not in values"):
        plotting.field(_annulus(), str(tmp_path / "x.png"), quantity="H")


def test_none_values_are_blank_not_fatal(tmp_path):
    d = _annulus()
    d["values"]["B"][0] = None
    a = plotting.field(d, str(tmp_path / "n.png"))["annotations"]
    assert a["max"] == pytest.approx(3.0, abs=1e-9)


def test_mesh_plot(tmp_path):
    d = _annulus()
    r = plotting.mesh(d, str(tmp_path / "m.png"))
    assert _png(r["path"])
    a = r["annotations"]
    ring = lambda r0, r1: 0.5 * NA * math.sin(2 * math.pi / NA) * (r1 * r1 - r0 * r0)  # noqa: E731
    assert a["per_part"]["Rotor"]["elements"] == 60 and a["per_part"]["Stator"]["elements"] == 120
    assert a["per_part"]["Rotor"]["area"] == pytest.approx(ring(10, 14), abs=1e-9)
    assert a["per_part"]["Stator"]["area"] == pytest.approx(ring(14, 22), abs=1e-9)
    assert a["colours"] == {"Rotor": plotting.SERIES_COLORS[0], "Stator": plotting.SERIES_COLORS[1]}
    assert 0 < a["min_angle_deg"] < 60
    # colour follows the part, not its rank among the drawn parts
    assert plotting.mesh(d, str(tmp_path / "s.png"), parts="Stator")["annotations"]["colours"] == \
        {"Stator": plotting.SERIES_COLORS[1]}


def test_mesh_many_parts_fold_to_grey(tmp_path):
    d = _annulus()
    d["parts"] = [f"P{k}" for k in range(12)]
    d["element_part"] = [k % 12 for k in range(len(d["elements"]))]
    c = plotting.mesh(d, str(tmp_path / "many.png"))["annotations"]["colours"]
    assert [c[f"P{k}"] for k in range(8)] == list(plotting.SERIES_COLORS)
    assert {c[f"P{k}"] for k in range(8, 12)} == {plotting.OTHER_COLOR}


def test_plot_dispatch(tmp_path):
    d = _annulus()
    r = plotting.plot("field", d, str(tmp_path / "f.png"), y=["Demag"])     # server passes y as a list
    assert r["annotations"]["quantity"] == "Demag"
    r = plotting.plot("field", d, str(tmp_path / "f2.png"), title="B", parts=["Stator"], flux_levels=8)
    assert r["annotations"]["quantity"] == "B" and list(r["annotations"]["per_part"]) == ["Stator"]
    r = plotting.plot("mesh", d, str(tmp_path / "m.png"))
    assert r["annotations"]["elements"] == 180 and _png(r["path"])
    assert "field" in plotting.KINDS and "mesh" in plotting.KINDS
    with pytest.raises(ValueError, match="unexpected options"):
        plotting.plot("waveform", {"x": [0, 1], "series": {"a": [1, 2]}}, str(tmp_path / "w.png"), parts=["x"])
