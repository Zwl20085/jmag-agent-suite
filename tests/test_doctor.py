"""doctor.py check logic with fakes (never runs a real JMAG, claude CLI or PowerShell)."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jmag_bridge import config, doctor
from jmag_bridge.doctor import FAIL, PASS, WARN


def _cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """A fake JMAG folder with the files find_jmag/_valid and the doctor look for; state dir in tmp."""
    home = tmp_path / "JMAG-Fake23.0"
    (home / "jmag").mkdir(parents=True)
    (home / "python3.8").mkdir()
    for f in ("designer.exe", "jmag/designer.py", "jmag/private.py", "python3.8/python.exe"):
        (home / f).write_text("")
    monkeypatch.setenv("JMAG_HOME", str(home))
    monkeypatch.delenv("JMAG_PYTHON", raising=False)
    monkeypatch.setenv("JMAG_BRIDGE_HOME", str(tmp_path / "state"))
    return home


@pytest.fixture
def quiet_side_checks(monkeypatch, tmp_path):
    """Neutralise checks that would touch the real machine (claude CLI, skills folder, designer processes)."""
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor, "user_skills_dir", lambda: tmp_path / "noskills")
    monkeypatch.setattr(doctor, "check_designer_processes", lambda: doctor.Check("designer processes", PASS, "fake"))


def _by_name(checks):
    return {c.name: c for c in checks}


def test_all_pass_with_working_fake_install(fake_home, quiet_side_checks, monkeypatch):
    def run(cmd, timeout, cwd=None, env=None):
        assert "PYTHONPATH" not in env
        if "import sys; print(sys.version)" in cmd:
            return _cp(out="3.8.1 (tags/v3.8.1) [MSC v.1916 64 bit (AMD64)]\n")
        assert cmd[-1] == str(fake_home) and "jmag.designer" in cmd[3]
        return _cp(out="PyJmag chatter\n" + doctor.PROBE_MARK + " True\n")
    monkeypatch.setattr(doctor, "_run", run)
    checks = _by_name(doctor.run_checks())
    for name in ("host python", "mcp package", "jmag install", "designer.exe", "jmag python", "jmag python runs",
                 "pyjmag import", "state dir"):
        assert checks[name].status == PASS, (name, checks[name])
    assert str(fake_home) in checks["jmag install"].detail
    assert checks["claude cli"].status == WARN and "claude-code" in checks["claude cli"].fix
    assert doctor.report(list(checks.values()))["ok"] is True


def test_jmag_not_found_is_fail_with_fix_and_skips_dependents(monkeypatch, quiet_side_checks, tmp_path):
    monkeypatch.setattr(config, "find_jmag", lambda: None)
    monkeypatch.setenv("JMAG_BRIDGE_HOME", str(tmp_path / "state"))
    checks = doctor.run_checks()
    c = _by_name(checks)["jmag install"]
    assert c.status == FAIL and "JMAG_HOME" in c.fix and "-JmagHome" in c.fix
    assert "pyjmag import" not in _by_name(checks)
    assert doctor.report(checks)["ok"] is False


def test_jmag_python_override_missing(fake_home, monkeypatch):
    monkeypatch.setenv("JMAG_PYTHON", str(fake_home / "nope" / "python.exe"))
    inst = config.find_jmag()
    c = doctor.check_jmag_python(inst)
    assert c.status == FAIL and "JMAG_PYTHON" in c.fix and "override" in c.detail


def test_jmag_python_wrong_version_and_crash(fake_home, monkeypatch):
    inst = config.find_jmag()
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: _cp(out="3.11.4 (main)\n"))
    assert doctor.check_jmag_python_runs(inst).status == WARN
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: _cp(rc=3, err="Fatal Python error: init failed"))
    c = doctor.check_jmag_python_runs(inst)
    assert c.status == FAIL and "Fatal" in c.detail

    def hang(*a, **k):
        raise subprocess.TimeoutExpired("python", 1)
    monkeypatch.setattr(doctor, "_run", hang)
    assert doctor.check_jmag_python_runs(inst).status == FAIL


def test_pyjmag_import_warn_when_module_missing_fail_on_dll_error(fake_home, monkeypatch):
    inst = config.find_jmag()
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: _cp(rc=1, err="ModuleNotFoundError: No module named 'jmag'"))
    assert doctor.check_pyjmag_import(inst).status == WARN
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: _cp(rc=1, err="OSError: [WinError 126] PyJmag.dll not found"))
    c = doctor.check_pyjmag_import(inst)
    assert c.status == FAIL and "WinError 126" in c.detail
    (fake_home / "jmag" / "private.py").unlink()  # Designer without its Python module: never even run the probe
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: pytest.fail("probe must not run"))
    c = doctor.check_pyjmag_import(inst)
    assert c.status == WARN and "Python module" in c.detail


def test_crashing_check_becomes_fail_row(fake_home, quiet_side_checks, monkeypatch):
    def boom():
        raise RuntimeError("kaboom")
    monkeypatch.setattr(doctor, "check_matplotlib", boom)
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: _cp(out="3.8.1\n" + doctor.PROBE_MARK + " True\n"))
    checks = _by_name(doctor.run_checks())
    assert checks["matplotlib"].status == FAIL and "kaboom" in checks["matplotlib"].detail
    assert checks["state dir"].status == PASS  # later checks still ran


def test_host_python_and_matplotlib(monkeypatch):
    assert doctor.check_host_python().status in (PASS, WARN)  # the test interpreter is >= 3.10
    monkeypatch.setattr(doctor.importlib.util, "find_spec", lambda name: None)
    c = doctor.check_matplotlib()
    assert c.status == WARN and "[plots]" in c.fix


def test_mcp_missing(monkeypatch):
    def nope(name):
        raise doctor.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(doctor.importlib.metadata, "version", nope)
    assert doctor.check_mcp().status == FAIL
    monkeypatch.setattr(doctor.importlib.metadata, "version", lambda name: "1.2.0")
    assert doctor.check_mcp().status == WARN


def test_state_dir_not_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path / "does-not-exist")
    c = doctor.check_state_dir()
    assert c.status == FAIL and "JMAG_BRIDGE_HOME" in c.fix


@pytest.mark.parametrize("out,status", [
    ("other: npx foo - \u2713 Connected\njmag: C:\\v\\python.exe -m jmag_bridge - \u2713 Connected\n", PASS),
    ("jmag: C:\\v\\python.exe -m jmag_bridge - \u2717 Failed to connect\n", WARN),
    ("jmag: uvx jmag-mcp - ? Pending approval (run `claude` to approve)\n", WARN),
    ("No MCP servers configured.\n", WARN),
])
def test_mcp_registration_parsing(monkeypatch, out, status):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "C:/bin/claude.exe")
    monkeypatch.setattr(doctor, "_run", lambda cmd, timeout, **k: _cp(out=out))
    assert doctor.check_mcp_registration().status == status


def test_mcp_registration_skips_without_cli_or_inside_session(monkeypatch):
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: pytest.fail("must not call the CLI"))
    assert doctor.check_mcp_registration(run_cli=False).status == PASS
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    assert doctor.check_mcp_registration().status == WARN


def _skill(root: Path, name: str, text: str = "x") -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(text)


def test_skills_compare_against_repo(tmp_path):
    repo, dest = tmp_path / "repo", tmp_path / "dest"
    for s in ("jmag", "jmag-geometry"):
        _skill(repo, s, "v2")
    _skill(dest, "jmag", "v2")
    c = doctor.check_skills(repo, dest)
    assert c.status == WARN and "jmag-geometry" in c.detail and "1/2" in c.detail
    _skill(dest, "jmag-geometry", "v1")
    c = doctor.check_skills(repo, dest)
    assert c.status == WARN and "out of date" in c.detail
    (dest / "jmag-geometry" / "SKILL.md").write_text("v2")
    assert doctor.check_skills(repo, dest).status == PASS


def test_skills_without_checkout_lists_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "repo_skills_dir", lambda: None)
    dest = tmp_path / "dest"
    assert doctor.check_skills(None, dest).status == WARN
    _skill(dest, "jmag-postprocess")
    c = doctor.check_skills(None, dest)
    assert c.status == PASS and "jmag-postprocess" in c.detail


def test_repo_skills_dir_found_from_checkout():
    d = doctor.repo_skills_dir()
    assert d is not None and (d / "jmag" / "SKILL.md").is_file()


def test_table_json_and_exit_code(monkeypatch, capsys):
    rows = [doctor.Check("a", PASS, "fine"), doctor.Check("b", WARN, "meh", "do x"), doctor.Check("c", FAIL, "bad", "do y")]
    monkeypatch.setattr(doctor, "run_checks", lambda run_claude_cli=True: rows)
    assert doctor.cli() == 1
    out = capsys.readouterr().out
    assert "[FAIL] c" in out and "fix: do y" in out and "fix: do x" in out and "1 pass / 1 warn / 1 fail" in out
    assert doctor.cli(as_json=True) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == "1 pass / 1 warn / 1 fail" and data["checks"][2] == {
        "name": "c", "status": FAIL, "detail": "bad", "fix": "do y"}
    monkeypatch.setattr(doctor, "run_checks", lambda run_claude_cli=True: rows[:2])
    assert doctor.cli() == 0


def test_record_startup_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)
    try:
        raise ImportError("no module named mcp.server")
    except ImportError as e:
        log = doctor.record_startup_failure(e)
    assert log == tmp_path / "server-startup.log" and "ImportError" in log.read_text()
    err = capsys.readouterr().err
    assert err.startswith("jmag-agent-suite failed to start: ImportError") and "--doctor" in err


def test_server_main_doctor_and_version(monkeypatch, capsys):
    from jmag_bridge import server
    monkeypatch.setattr(doctor, "run_checks", lambda run_claude_cli=True: [doctor.Check("a", PASS, "fine")])
    monkeypatch.setattr(server.mcp, "run", lambda *a, **k: pytest.fail("server must not start"))
    with pytest.raises(SystemExit) as ex:
        server.main(["--doctor", "--json"])
    assert ex.value.code == 0 and json.loads(capsys.readouterr().out)["ok"] is True
    with pytest.raises(SystemExit) as ex:
        server.main(["--version"])
    assert ex.value.code == 0 and "jmag-agent-suite" in capsys.readouterr().out


def test_server_main_logs_startup_failure(monkeypatch, tmp_path, capsys):
    from jmag_bridge import server
    monkeypatch.setattr(config, "state_dir", lambda: tmp_path)

    def broken():
        raise RuntimeError("stdio broke")
    monkeypatch.setattr(server.mcp, "run", broken)
    with pytest.raises(RuntimeError):
        server.main([])
    assert "stdio broke" in (tmp_path / "server-startup.log").read_text()
    assert "failed to start" in capsys.readouterr().err


def test_jmag_doctor_tool(monkeypatch):
    from jmag_bridge import server
    seen = {}

    def fake(run_claude_cli=True):
        seen["cli"] = run_claude_cli
        return [doctor.Check("a", PASS, "fine"), doctor.Check("b", FAIL, "bad", "fix it")]
    monkeypatch.setattr(doctor, "run_checks", fake)
    out = asyncio.run(server.jmag_doctor())
    assert out["ok"] is True and out["result"]["summary"] == "1 pass / 0 warn / 1 fail"
    assert out["result"]["checks"][1]["fix"] == "fix it" and seen["cli"] is False
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "jmag_doctor" in names and "jmag_doctor" not in server.READ_ONLY_OPS


def test_main_module_doctor_survives_broken_server_import(monkeypatch, capsys):
    """`python -m jmag_bridge --doctor` must still report when the server module cannot be imported."""
    import importlib
    main_mod = importlib.import_module("jmag_bridge.__main__")
    monkeypatch.setitem(sys.modules, "jmag_bridge.server", None)  # makes `from .server import main` raise ImportError
    monkeypatch.setattr(doctor, "run_checks", lambda run_claude_cli=True: [doctor.Check("mcp package", FAIL, "x", "y")])
    monkeypatch.setattr(sys, "argv", ["jmag_bridge", "--doctor"])
    with pytest.raises(SystemExit) as ex:
        main_mod.main()
    assert ex.value.code == 1
    cap = capsys.readouterr()
    assert "failed to import" in cap.err and "[FAIL] mcp package" in cap.out


def test_main_module_logs_import_failure(tmp_path):
    """A server that cannot import leaves server-startup.log and the one-line pointer on stderr."""
    shim = tmp_path / "mcp"
    shim.mkdir()
    (shim / "__init__.py").write_text("raise ImportError('mcp is broken (test shim)')\n")
    env = {**os.environ, "PYTHONPATH": str(tmp_path), "JMAG_BRIDGE_HOME": str(tmp_path / "state")}
    r = subprocess.run([sys.executable, "-m", "jmag_bridge"], capture_output=True, text=True, env=env, timeout=60,
                       cwd=str(tmp_path), stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and "jmag-agent-suite failed to start: ImportError" in r.stderr and "--doctor" in r.stderr
    assert "test shim" in (tmp_path / "state" / "server-startup.log").read_text()
    r = subprocess.run([sys.executable, "-m", "jmag_bridge", "--version"], capture_output=True, text=True, env=env,
                       timeout=60, cwd=str(tmp_path))
    assert r.returncode == 0 and r.stdout.startswith("jmag-agent-suite ")
