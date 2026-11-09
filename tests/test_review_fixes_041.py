"""Regression tests for the 0.4.1 review round (no JMAG): a cancelled / interrupted case of jmag_run_cases is not
reported as finished, an owned cancel never kills solvers it cannot tie to its own calc folder, interruption
messages are matched loosely and cross-checked against the jmonitor step count. (The dq sequence pin is in
test_review_fixes.py, the README badge check in test_versions.py.)"""
import asyncio
import json
import time

import pytest

from jmag_bridge import health, server, solver_monitor
from jmag_bridge.worker import ops

from test_ops_fake import FakeCtx, FakeStudy, Log
from test_review_fixes import INTERRUPTED, _ui, fake  # noqa: F401  (fixture)


# --------------------------------------------------------------------------- 1. run_cases: interrupted cases

class _Msg:
    def __init__(self, text):
        self.text = text

    def GetText(self):
        return self.text


class _Report:
    def __init__(self, warnings):
        self.warnings = list(warnings)

    def NumErrorMessages(self):
        return 0

    def GetErrorMessage(self, i):
        raise AssertionError

    def NumWarningMessages(self):
        return len(self.warnings)

    def GetWarningMessage(self, i):
        return _Msg(self.warnings[i])

    def GetCalculationFolder(self):
        return "C:/w/m.jfiles/M~1/S~1/Case1"


class _RunStudy(FakeStudy):
    """Run() solves the current case; `on_run(case)` returns the run's warnings (and may drop a cancel file)."""

    def __init__(self, log, on_run, **kw):
        super().__init__(log, **kw)
        self.on_run = on_run
        self.warnings = []

    def Run(self):
        self.log.append(("Run", self.current))
        self.warnings = self.on_run(self.current) or []
        self.results.add(self.current)

    def DeleteResultCurrentCase(self):
        self.results.discard(self.current)

    def GetReport(self):
        return _Report(self.warnings)


def _run_ctx(on_run, n_cases=2):
    log = Log()
    s = _RunStudy(log, on_run, n_cases=n_cases)
    return FakeCtx(s), s


@pytest.mark.parametrize("text", [INTERRUPTED, "The interruption key (Ctrl + C) was pressed.",
                                  "THE INTERRUPTION KEY (CTRL+C) HAS BEEN PRESSED"])
def test_run_cases_marks_a_case_with_the_interrupt_warning_interrupted(text):
    ctx, s = _run_ctx(lambda c: [text] if c == 0 else [])
    out = ops.run_cases(ctx, "S", cases=[0, 1])
    states = {r["case"]: r["state"] for r in out["cases"]}
    assert states == {0: "interrupted", 1: "finished"}
    assert out["finished"] == [1] and out["interrupted"] == [0]


def test_run_cases_marks_the_case_solving_at_cancel_interrupted(tmp_path):
    cancel = tmp_path / "j.cancel"

    def on_run(c):
        cancel.touch()          # jmag_cancel_run while case 0 solves; JMAG keeps the partial result, no message
        return []

    ctx, s = _run_ctx(on_run)
    out = ops.run_cases(ctx, "S", cases=[0, 1], cancel_file=str(cancel))
    states = {r["case"]: r["state"] for r in out["cases"]}
    assert states == {0: "interrupted", 1: "cancelled"}
    assert out["finished"] == [] and out["interrupted"] == [0]


def _cases_job(tmp_path, response, **kw):
    prog = tmp_path / "j.progress.json"
    prog.write_text(json.dumps({"state": "finished", "cases": [0, 1], "current_case": None, "folder": None,
                                "done": (response.get("result") or {}).get("cases") or []}))
    job = {"id": "j1", "kind": "cases", "study": "Load", "started": time.time() - 10, "finished": time.time(),
           "done": True, "cases": None, "progress_file": str(prog), "total_steps": None, "response": response}
    job.update(kw)
    return job


def test_run_cases_status_reports_an_interrupted_case_as_not_ok(tmp_path, monkeypatch):
    """An older/looser worker said 'finished' although the case carries the interrupt warning."""
    monkeypatch.setattr(server.solver_monitor, "progress", lambda f: {"steps_done": 0, "monitor_found": False})
    rows = [{"case": 0, "state": "finished", "seconds": 4.0, "errors": [], "warnings": [INTERRUPTED]},
            {"case": 1, "state": "finished", "seconds": 5.0, "errors": [], "warnings": []}]
    job = _cases_job(tmp_path, {"ok": True, "result": {"cases": rows, "finished": [0, 1], "failed": []}})
    st = asyncio.run(server._run_cases_status(job))
    assert st["ok"] is False and st["state"] == "failed"
    assert [d["state"] for d in st["done"]] == ["interrupted", "finished"]
    assert st["done"][0]["warnings"] == [INTERRUPTED]                       # warnings kept in the summary
    assert st["interrupted"] == [0] and "jmag_run_cases" in st["hint"] and "cases=[0]" in st["hint"]


def test_run_cases_status_cancelled_case_is_never_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(server.solver_monitor, "progress", lambda f: {"steps_done": 0, "monitor_found": False})
    rows = [{"case": 0, "state": "finished", "seconds": 4.0, "errors": [], "warnings": []},
            {"case": 1, "state": "cancelled"}]
    job = _cases_job(tmp_path, {"ok": True, "result": {"cases": rows, "finished": [0], "failed": []}},
                     cancelled_case=0)
    st = asyncio.run(server._run_cases_status(job))
    assert st["ok"] is False and st["done"][0]["state"] == "interrupted" and st["interrupted"] == [0]


def test_cases_cancel_prepare_remembers_the_case_being_solved(tmp_path):
    prog = tmp_path / "j.progress.json"
    prog.write_text(json.dumps({"state": "running", "current_case": 3, "folder": "C:/w/Case4", "done": []}))
    job = {"progress_file": str(prog), "cancel_file": str(tmp_path / "j.cancel")}
    server._cases_cancel_prepare(job)
    assert job["cancelled_case"] == 3 and job["folder"] == "C:/w/Case4" and (tmp_path / "j.cancel").exists()


# --------------------------------------------------------------------------- 2. owned cancel without a folder

class _Runner:
    def kill(self):
        pass

    def stop(self):
        pass


def test_owned_cancel_without_a_folder_kills_nothing(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(100, "Run Analysis")], ["C:/elsewhere/x.jfiles/M~1/S~1/Case1"])
    orig = fake.call

    async def call(op, timeout=600, **a):
        if op == "calc_folder":
            return {"ok": False, "error": {"message": "no folder"}}
        return await orig(op, timeout, **a)

    fake.call = call
    fake.job = {"id": "r1", "study": "Load", "model": None, "done": False, "started": time.time(),
                "folder": None, "pid": 100, "runner": _Runner()}
    r = asyncio.run(server.jmag_cancel_run())
    assert killed == []                                                  # another solve on the machine survives
    assert any("not killed" in s for s in r["steps"]) and r["solver_stopped_verified"] is False
    assert "jmag_status" in r["hint"]


def test_kill_solver_refuses_without_a_folder(monkeypatch):
    calls = []
    monkeypatch.setattr(solver_monitor, "solver_processes", lambda folder=None, **k: [{"pid": 1}])
    monkeypatch.setattr(solver_monitor.subprocess, "run", lambda *a, **k: calls.append(a))
    for folder in (None, ""):
        with pytest.raises(ValueError):
            solver_monitor.kill_solver(folder)
    assert calls == []


# --------------------------------------------------------------------------- 6. nothing matched the folder

def test_owned_cancel_with_no_matching_process_is_not_reported_as_stopped(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(100, "Run Analysis")], [], alive=False)
    fake.job = {"id": "r1", "study": "Load", "model": None, "done": False, "started": time.time(),
                "folder": "C:/work/motor.jfiles/M~1/Load~1/Case1", "pid": 100, "runner": _Runner()}
    r = asyncio.run(server.jmag_cancel_run())
    assert killed == [] and r["solver_stopped_verified"] is False
    assert any("no solver process matched" in s and "not verified stopped" in s for s in r["steps"])


def test_owned_cancel_verifies_a_matched_solver_that_exits(fake, monkeypatch):
    clicks, killed = _ui(monkeypatch, [(100, "Run Analysis")], [])
    seen = iter([[{"pid": 7}], []])                                      # matched before Abort, gone after
    monkeypatch.setattr(server.solver_monitor, "solver_processes", lambda folder=None, **k: next(seen, []))
    fake.job = {"id": "r1", "study": "Load", "model": None, "done": False, "started": time.time(),
                "folder": "C:/work/motor.jfiles/M~1/Load~1/Case1", "pid": 100, "runner": _Runner()}
    r = asyncio.run(server.jmag_cancel_run())
    assert killed == [] and r["solver_stopped_verified"] is True


# --------------------------------------------------------------------------- 3. interruption matching

@pytest.mark.parametrize("text", ["The interruption key (Ctrl + C) is pressed.", "interruption key (ctrl+c) was pressed",
                                  # Designer.log wording found in existing interrupted calc folders (offline read)
                                  "The interruption key (Ctrl+C) is pressed at SigintLikeDefault().",
                                  "The Interruption  Key (Ctrl+C)\nhas been pressed. The program will be terminated."])
def test_interrupt_message_variants(text):
    assert health.classify_message(text, "warning")[1] == "RUN_INTERRUPTED"
    assert health.solver_abort_messages({"ok": True, "result": {"warnings": [text]}})


def test_run_incomplete_from_the_step_count():
    f = health.postflight({"has_result": True}, configured_steps=4001, steps_done=800)
    assert {x["code"]: x["level"] for x in f}.get("RUN_INCOMPLETE") == "error"
    assert "RUN_INCOMPLETE" not in {x["code"] for x in health.postflight({"has_result": True}, configured_steps=4001,
                                                                         steps_done=4001)}
    # JMAG retimed the steps for the motion: the configured count is no longer the total, so no verdict
    auto = {"has_result": True, "warnings": ["The step control has been automatically updated by the function ..."]}
    assert "RUN_INCOMPLETE" not in {x["code"] for x in health.postflight(auto, configured_steps=120, steps_done=100)}
    assert "RUN_INCOMPLETE" not in {x["code"] for x in health.postflight({"has_result": True}, configured_steps=120)}


def test_run_status_flags_a_short_run_without_any_message(fake, monkeypatch):
    monkeypatch.setattr(server.solver_monitor, "log_messages", lambda folder: [])
    monkeypatch.setattr(server.solver_monitor, "convergence", lambda *a, **k: None)
    monkeypatch.setattr(server.solver_monitor, "progress", lambda f: {"steps_done": 40, "monitor_found": True})
    fake.job = {"id": "r1", "study": "Load", "model": None, "started": time.time() - 5, "finished": time.time(),
                "done": True, "folder": "calc", "freq": None,
                "response": {"ok": True, "result": {"has_result": True, "errors": [], "warnings": [],
                                                    "calc_folder": "calc"}}}
    st = asyncio.run(server.jmag_run_status())
    assert "RUN_INCOMPLETE" in {f["code"] for f in st["post"]["health"]["findings"]}
    assert st["ok"] is False and st["run_incomplete"] is True and "RUN_INCOMPLETE" in st["error"]["message"]
