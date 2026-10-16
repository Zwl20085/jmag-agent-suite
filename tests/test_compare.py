from jmag_bridge import compare, health


def _facts(thickness=50.0, turns=40, material="35JN210", elements=5000):
    return {
        "type": "Transient2D", "dimension": 2, "model_thickness": thickness, "elements": elements,
        "parts": [{"id": 1, "name": "Core", "material": material}],
        "conditions": [{"name": "Phase", "type": "FEMCoil", "link": "Coil1",
                        "properties": [{"name": "Turn", "value": turns}],
                        "subconditions": [{"index": 0, "name": "a", "parts": [2], "properties": []}]}],
        "circuit": {"has_circuit": True, "components": [{"name": "Coil1", "type": "Coil",
                                                         "properties": [{"name": "Turn", "value": turns}]}]},
        "step": {"Step": 121, "TimeStep": 1e-4},
        "equations": {"speed": {"value": 3000, "expression": "3000"}},
    }


def test_flatten_and_diff_setup():
    a = compare.flatten_facts(_facts())
    b = compare.flatten_facts(_facts(thickness=60.0, material="N42", elements=20000))
    assert a["material[Core]"] == "35JN210" and a["condition[Phase].Turn"] == 40
    assert a["equation.speed"] == 3000 and a["step.Step"] == 121
    d = compare.diff_flat(a, b)
    keys = {r["key"]: r for r in d["changed"]}
    assert set(keys) == {"model_thickness", "material[Core]", "elements"}
    assert keys["model_thickness"]["delta_pct"] == 20.0
    assert d["only_in_a"] == {} and d["only_in_b"] == {}


def test_diff_properties_only_in_one_side():
    a = compare.flatten_properties([{"name": "MultiCPU", "value": 4}])
    b = compare.flatten_properties([{"name": "MultiCPU", "value": 4.0}, {"name": "UseMultiCPU", "value": 1}])
    d = compare.diff_flat(a, b)
    assert d["changed"] == [] and d["only_in_b"] == {"study.UseMultiCPU": 1}


def test_compare_results_deltas():
    sa = {"Torque": {"T": health.waveform_stats([1.0, 1.0, 1.0])}, "Only": {"x": health.waveform_stats([1.0])}}
    sb = {"Torque": {"T": health.waveform_stats([1.005, 1.005, 1.005])}}
    r = compare.compare_results(sa, sb, threshold_pct=1.0)
    mean = next(x for x in r["rows"] if x["stat"] == "mean")
    assert abs(mean["delta_pct"] - 0.5) < 1e-6
    assert r["significant"] == [] and r["tables_only_in_a"] == ["Only"]
    r2 = compare.compare_results(sa, sb, threshold_pct=0.1)
    assert any(x["stat"] == "mean" for x in r2["significant"])


def test_inventory_flags():
    flags = compare.inventory_flags([
        {"name": "M", "studies": [{"name": "A", "solved": False, "conditions": 0},
                                  {"name": "B", "solved": True, "conditions": 3}]},
        {"name": "Empty", "studies": []},
    ])
    assert "M/A: not solved" in flags and any("no conditions" in f for f in flags)
    assert "model 'Empty' has no studies" in flags and not any(f.startswith("M/B") for f in flags)
