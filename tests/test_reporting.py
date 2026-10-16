from jmag_bridge import health, reporting


def _fail_health():
    return health.summarize([
        {"level": "error", "code": "NO_MATERIAL", "message": "parts without material: ['Core']", "fix": "assign"},
        {"level": "warning", "code": "COARSE_MESH", "message": "mesh has only 300 elements"},
        {"level": "info", "code": "NO_MESH_YET", "message": "no mesh yet"},
    ])


def test_report_never_omits_fail_and_check(tmp_path):
    img = tmp_path / "contour.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    studies = [
        {"name": "Load", "setup": {"type": "Transient2D", "stack": 50.0}, "health": _fail_health(),
         "invariants": [{"check": "kvl", "ok": False, "max_rel_residual": 0.2}],
         "result_summary": {"Torque": {"Torque": {"mean": 1.5, "rms": 1.6, "min": 1.0, "max": 2.0, "pk_pk": 1.0}}},
         "images": [str(img), str(tmp_path / "missing.png")],
         "sweeps": [{"title": "current angle", "columns": ["beta_deg", "T_Nm"], "rows": [[0, 1.0], [30, 1.2]]}]},
        {"name": "NoLoad"},
    ]
    journal = [{"op": "create_study", "args": {"study_type": "Transient2D", "name": "Load"}, "ok": True},
               {"op": "set_material", "args": {"study": "Load"}, "ok": False}]
    out = reporting.build_report(str(tmp_path / "rep"), "SPM check", studies, notes="first pass",
                                 journal=journal, sweeps=[{"title": "global", "columns": ["a"], "rows": [[1]]}])
    md = (tmp_path / "rep" / "report.md").read_text(encoding="utf-8")
    assert "**Verdict: FAIL**" in md and "NO_MATERIAL" in md and "COARSE_MESH" in md
    assert "NOT CHECKED" in md
    assert out["verdicts"] == {"Load": "FAIL", "NoLoad": "NOT CHECKED"}
    doubts = " ".join(out["open_doubts"])
    assert "Load: verdict FAIL" in doubts and "NoLoad: verdict NOT CHECKED" in doubts
    assert "invariant kvl failed" in doubts and "missing.png" in doubts
    assert (tmp_path / "rep" / "images" / "Load__contour.png").is_file()
    assert "](images/Load__contour.png)" in md
    assert "current angle" in md and "## Sweep: global" in md
    assert "first pass" in md and "## Open doubts" in md
    assert out["replay_script"] and (tmp_path / "rep" / "replay.py").is_file()
    assert "| 2 | set_material | FAILED |" in md


def test_pass_report_without_journal(tmp_path):
    out = reporting.build_report(str(tmp_path), "ok", [{"name": "S", "health": health.summarize([])}])
    md = open(out["path"], encoding="utf-8").read()
    assert "**Verdict: PASS**" in md and "cannot be replayed" in md
    assert out["open_doubts"] == []


def test_table_escapes_pipes():
    assert "a\\|b" in reporting.table(["x"], [["a|b"]])
