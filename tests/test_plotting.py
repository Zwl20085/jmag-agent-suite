import math

import pytest

from jmag_bridge import plotting

pytest.importorskip("matplotlib")


def _png(path):
    with open(path, "rb") as f:
        return f.read(8) == b"\x89PNG\r\n\x1a\n"


def _sine(n=120, periods=2, f=50.0, dc=0.0, amp=1.0):
    t = [k / (f * n / periods) for k in range(n)]
    return t, [dc + amp * math.sin(2 * math.pi * f * x) for x in t]


def test_waveform_annotations(tmp_path):
    t, y = _sine(dc=2.0)
    r = plotting.plot("waveform", {"x": t, "series": {"Torque": y}, "x_name": "time", "x_unit": "s"},
                      str(tmp_path / "w.png"))
    assert _png(r["path"])
    assert r["annotations"]["Torque"]["mean"] == pytest.approx(2.0, abs=1e-9)


def test_fft_last_period(tmp_path):
    t, y = _sine(n=120, periods=2, amp=3.0)
    r = plotting.plot("fft", {"x": t, "columns": {"Flux": y}}, str(tmp_path / "f.png"), electrical_frequency_hz=50)
    a = r["annotations"]["Flux"]
    assert a["fundamental"] == pytest.approx(3.0, rel=1e-6) and a["samples"] == 60


def test_sweep_with_x_series(tmp_path):
    data = {"series": {"angle": [0, 10, 20, 30], "T": [1.0, 3.0, 2.0, 0.5]}}
    r = plotting.plot("sweep", data, str(tmp_path / "s.png"), x="angle")
    assert r["annotations"]["T"]["x_at_max"] == 10


def test_map_and_torque_speed(tmp_path):
    x, y = [1000, 2000, 3000], [10, 20]
    z = [[0.9, 0.92, None], [0.88, 0.95, 0.91]]
    r = plotting.plot("map", {"x": x, "y": y, "z": z}, str(tmp_path / "m.png"))
    assert r["annotations"]["z_max"] == 0.95 and r["annotations"]["at_max"] == [2000, 20]
    assert r["annotations"]["blank_cells"] == 1
    sp = [0, 1000, 2000, 3000, 4000]
    tq = [10, 10, 10, 7.5, 5]
    r = plotting.plot("torque_speed", {"x": sp, "series": {"torque": tq}}, str(tmp_path / "ts.png"))
    assert r["annotations"]["base_speed_rpm"] == 2000
    assert _png(r["path"])


def test_csv_roundtrip(tmp_path):
    p = tmp_path / "Load__Torque.csv"
    p.write_text('time[s],"Torque[N*m]"\n0.0,1.0\n0.001,2.0\n0.002,3.0\n', encoding="utf-8")
    d = plotting.load_csv(str(p))
    assert d["x"] == [0.0, 0.001, 0.002] and d["columns"]["Torque[N*m]"] == [1.0, 2.0, 3.0]
    r = plotting.plot("waveform", d, str(tmp_path / "c.png"))
    assert r["annotations"]["Torque[N*m]"]["max"] == 3.0


def test_bad_inputs(tmp_path):
    with pytest.raises(ValueError, match="unknown plot kind"):
        plotting.plot("pie", {}, str(tmp_path / "x.png"))
    with pytest.raises(ValueError, match="has 2 points"):
        plotting.plot("waveform", {"x": [0, 1, 2], "series": {"a": [1, 2]}}, str(tmp_path / "x.png"))
    with pytest.raises(ValueError, match="not found"):
        plotting.plot("waveform", {"x": [0, 1], "series": {"a": [1, 2]}}, str(tmp_path / "x.png"), y=["b"])


def test_style_does_not_leak_and_series_never_cycle(tmp_path):
    import matplotlib
    before = dict(matplotlib.rcParams)
    t, y = _sine()
    series = {f"s{k}": [v + k for v in y] for k in range(10)}       # > 8 series: the tail is grey, not cycled
    r = plotting.plot("waveform", {"x": t, "series": series}, str(tmp_path / "many.png"))
    assert _png(r["path"]) and set(r["annotations"]) == set(series)
    assert plotting._color(7) == plotting.SERIES_COLORS[7] and plotting._color(8) == plotting.OTHER_COLOR
    r = plotting.plot("fft", {"x": t, "series": {"a": y, "b": [2 * v for v in y]}}, str(tmp_path / "f2.png"),
                      electrical_frequency_hz=50)
    assert r["annotations"]["b"]["fundamental"] == pytest.approx(2.0, rel=1e-6)
    after = dict(matplotlib.rcParams)
    assert {k for k in before if before[k] != after.get(k)} <= {"backend"}


def test_torque_speed_with_power_series(tmp_path):
    sp = [0, 1000, 2000, 3000]
    r = plotting.plot("torque_speed", {"x": sp, "series": {"torque": [10, 10, 9, 6], "power": [0, 900, 1800, 1500]}},
                      str(tmp_path / "ts.png"))
    a = r["annotations"]
    assert a["max_power_W"] == 1800 and a["speed_at_max_power_rpm"] == 2000 and a["base_speed_rpm"] == 1000
    assert set(a) == {"max_torque_Nm", "base_speed_rpm", "max_power_W", "speed_at_max_power_rpm", "max_speed_rpm"}
