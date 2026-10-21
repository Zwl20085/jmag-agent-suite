"""The server process must end promptly when its MCP client closes stdio, even with a tool call in flight.

Regression for the teardown hang: a thread blocked in Worker.call (a JMAG op, or jmag_run's 30-day wait) kept
`python -m jmag_bridge` alive after stdin closed, because asyncio joins its default executor on the way out. These
tests run the real server over real pipes with a stand-in JMAG install (this interpreter plays JMAG's Python, so the
worker runs but no JMAG is started) and a hard timeout on every wait.
"""
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from jmag_bridge.worker.ops import pid_alive

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the bridge targets Windows (JMAG-Designer)")

EXIT_BOUND_S = 10.0  # the fix exits in < 1 s; SHUTDOWN_GRACE_S (5 s) is the hard backstop


def _env(tmp_path: Path) -> dict:
    home = tmp_path / "JMAG"
    (home / "jmag").mkdir(parents=True)
    (home / "designer.exe").write_bytes(b"")
    (home / "jmag" / "designer.py").write_text("")
    return {"JMAG_HOME": str(home), "JMAG_PYTHON": sys.executable, "JMAG_BRIDGE_HOME": str(tmp_path / "state")}


def _blocking_code(pid_file: Path, seconds: int) -> str:
    """exec_python code that records the worker pid (= the call is in flight) and then blocks."""
    return f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep({seconds})\nresult = 1"


def _wait_for(path: Path, timeout: float) -> int:
    t_end = time.time() + timeout
    while time.time() < t_end:
        if path.is_file() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.1)
    raise AssertionError(f"the tool call never reached the worker ({path} not written)")


def _kill(pid: int | None) -> None:
    if pid and pid_alive(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)


def test_server_exits_when_stdin_closes_during_a_tool_call(tmp_path):
    pid_file = tmp_path / "worker.pid"
    proc = subprocess.Popen([sys.executable, "-m", "jmag_bridge"], env={**os.environ, **_env(tmp_path)},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    worker_pid = None

    def send(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
        assert b'"id":1' in proc.stdout.readline()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "jmag_exec_python", "arguments": {"code": _blocking_code(pid_file, 120)}}})
        worker_pid = _wait_for(pid_file, 60)
        t0 = time.time()
        proc.stdin.close()  # the client goes away while jmag_exec_python is still running
        try:
            proc.wait(EXIT_BOUND_S)
        except subprocess.TimeoutExpired:
            pytest.fail(f"server still alive {EXIT_BOUND_S:.0f} s after its stdin closed with a tool call in flight")
        assert time.time() - t0 < EXIT_BOUND_S
        # The worker is released, not killed: it exits by itself after its op (a runner mid-solve is left alone).
        assert pid_alive(worker_pid)
    finally:
        if proc.poll() is None:
            proc.kill()
        _kill(worker_pid)


CLIENT = textwrap.dedent('''
    import json, sys, time
    import anyio
    from mcp import ClientSession
    from mcp.client import stdio as stdio_mod
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env, code = json.loads(sys.argv[1]), sys.argv[2]

    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "jmag_bridge"], env=env)
        t0 = None
        try:
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    ok = await s.call_tool("jmag_units", {"convert": "speed", "value": 1000, "from_unit": "rpm",
                                                          "to_unit": "rad/s"})
                    assert json.loads(ok.content[0].text)["ok"]
                    async with anyio.create_task_group() as tg:
                        tg.start_soon(s.call_tool, "jmag_exec_python", {"code": code})
                        await anyio.sleep(3)  # the call is in flight in the worker
                        t0 = time.time()
                        raise RuntimeError("script failed inside the session")
        except BaseException as e:  # noqa: BLE001 - report whatever the teardown surfaced
            print(json.dumps({"teardown_s": time.time() - t0 if t0 else None, "exc": repr(e)[:300],
                              "client_grace_s": getattr(stdio_mod, "PROCESS_TERMINATION_TIMEOUT", 2.0)}), flush=True)

    anyio.run(main)
''')


def test_client_exception_in_session_with_call_in_flight_tears_down(tmp_path):
    """MCP Python client: an exception escaping `async with ClientSession` while a call is pending must not hang.

    The client closes the server's stdin, waits its grace period (2 s in mcp 1.x/2.x) and then kills the process
    tree. Before the fix the teardown always took that full grace and ended in the kill (the JMAG worker with it);
    now the server leaves by itself well inside it.
    """
    pid_file = tmp_path / "worker.pid"
    script = tmp_path / "client.py"
    script.write_text(CLIENT)
    try:
        out = subprocess.run([sys.executable, str(script), json.dumps(_env(tmp_path)), _blocking_code(pid_file, 120)],
                             capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        pytest.fail("client teardown hung with an exception in flight")
    finally:
        if pid_file.is_file():
            _kill(int(pid_file.read_text() or 0))
    rep = json.loads(out.stdout.strip().splitlines()[-1])
    assert "script failed inside the session" in rep["exc"], out
    assert rep["teardown_s"] is not None and rep["teardown_s"] < 0.75 * rep["client_grace_s"], rep
