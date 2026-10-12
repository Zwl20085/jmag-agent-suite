"""Tests that need neither JMAG nor Windows UI."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from jmag_bridge import solver_monitor
from jmag_bridge.config import JmagInstall
from jmag_bridge.dxf import Dxf, c_core_inductor
from jmag_bridge.replay import write_replay_script
from jmag_bridge.worker_client import Worker


def _lines(path):
    lines = Path(path).read_text().splitlines()
    return lines


def test_dxf_c_core(tmp_path):
    meta = c_core_inductor(str(tmp_path / "c.dxf"))
    text = (tmp_path / "c.dxf").read_text()
    assert text.startswith("0\nSECTION") and text.rstrip().endswith("EOF")
    assert text.count("\nLINE\n") == 12 + 4 + 4
    assert meta["core_area_mm2"] == 2685.0


def test_dxf_annular_sector_entities():
    d = Dxf().annular_sector(10, 12, 0, 90)
    s = d.to_string()
    assert s.count("\nARC\n") == 2 and s.count("\nLINE\n") == 2


def test_solver_monitor_parsing(tmp_path):
    (tmp_path / "jmonitor").write_text("*CONTROL_DATA\n*End_of_Step 999999\n 1\n*End_of_Step 999999\n 2\n")
    (tmp_path / "jcgmntr").write_text("1 1 0\n2 1 51\n2 2 47\n3 1 36\n3 15 3\n")
    (tmp_path / "Designer.log").write_text(
        "<message><status>Warning</status><text>Newton Raphson has not converged</text>"
        '<data key="STEP">003</data></message><message><status>Message</status><text>ok</text></message>')
    assert solver_monitor.progress(tmp_path)["steps_done"] == 2
    c = solver_monitor.convergence(tmp_path, max_iter=15)
    assert c["max_nonlinear_iterations"] == 15 and c["steps_at_iteration_limit"] == [3]
    msgs = solver_monitor.log_messages(tmp_path)
    assert msgs == [{"status": "Warning", "text": "Newton Raphson has not converged", "step": 3}]


def test_replay_script(tmp_path):
    rows = [{"op": "connect", "args": {"pid": 1}, "ok": True},
            {"op": "set_properties", "args": {"target": {"study": "S", "kind": "study"}, "values": {"ModelThickness": 50}}, "ok": True},
            {"op": "add_current", "args": {"study": "S"}, "ok": False}]
    out = write_replay_script(rows, str(tmp_path / "replay.py"), None)
    text = Path(out).read_text()
    compile(text, out, "exec")
    assert "step('set_properties'" in text and "# skipped" in text


@pytest.fixture()
def worker(tmp_path, monkeypatch):
    """The real worker process, run with this interpreter and no JMAG (protocol-level checks)."""
    monkeypatch.setenv("JMAG_BRIDGE_HOME", str(tmp_path))
    inst = JmagInstall(home=tmp_path, version="test", python=Path(sys.executable))
    w = Worker(inst, "test")
    w.start(timeout=60)
    yield w
    w.stop()


def test_worker_protocol(worker):
    r = worker.call("ping", 30)
    assert r["ok"] and r["result"]["connected"] is False
    assert worker.call("nope", 30)["error"]["type"] == "UnknownOp"
    bad = worker.call("ping", 30, unexpected=1)
    assert bad["error"]["type"] == "BadArguments"
    r = worker.call("list_parts", 30)
    assert not r["ok"] and "jmag_connect" in r["error"]["hint"]


def test_worker_survives_native_stdout_noise(worker):
    # Anything written to fd 1 inside the worker (like PyJmag/designer chatter) must not reach the protocol.
    r = worker.call("exec_python", 30, code="import os\nos.write(1, b'garbage that is not json\\n')\nresult = 42")
    assert r["ok"] and r["result"]["result"] == 42
    assert worker.call("ping", 30)["ok"]
    assert "garbage" in worker.log_path.read_text()


def test_server_registers_tools():
    from jmag_bridge import server
    tools = asyncio.run(server.mcp.list_tools())
    names = {t.name for t in tools}
    assert {"jmag_connect", "jmag_import_dxf", "jmag_add_fem_coil", "jmag_run", "jmag_check", "jmag_self_test"} <= names
    for t in tools:
        assert t.description and len(t.description) > 12, t.name
    json.dumps([t.name for t in tools])
