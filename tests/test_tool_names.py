"""Every jmag_* name the docs mention must be a registered MCP tool (agents copy names from the docs)."""
import asyncio
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# jmag_* tokens that are not tools: the Python package, the worker module, envelope/state keys.
NOT_TOOLS = {"jmag_bridge", "jmag_worker", "jmag_messages", "jmag_pid"}


def registered_tools() -> set[str]:
    from jmag_bridge.server import mcp
    tm = getattr(mcp, "_tool_manager", None)
    if tm is not None and hasattr(tm, "list_tools"):
        return {t.name for t in tm.list_tools()}
    return {t.name for t in asyncio.run(mcp.list_tools())}  # SDK without a tool manager


def doc_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "README.zh-CN.md", ROOT / "CLAUDE.md"]  # zh-CN is optional
    files += sorted((ROOT / ".claude" / "skills").rglob("*.md"))
    files += sorted((ROOT / "docs").glob("*.md"))
    return [f for f in files if f.is_file()]


def test_docs_are_scanned():
    names = {f.name for f in doc_files()}
    assert {"README.md", "quickstart.md", "install-troubleshooting.md", "other-clients.md"} <= names


def mentioned() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for f in doc_files():
        for name in re.findall(r"\bjmag_[a-z0-9_]+", f.read_text(encoding="utf-8")):
            name = name.rstrip("_")
            if name not in NOT_TOOLS:
                out.setdefault(name, set()).add(str(f.relative_to(ROOT)))
    return out


def test_registry_is_readable():
    tools = registered_tools()
    assert "jmag_status" in tools and "jmag_solver_report" in tools and "jmag_report" not in tools


def test_every_documented_tool_exists():
    tools = registered_tools()
    missing = {n: sorted(files) for n, files in mentioned().items() if n not in tools}
    assert not missing, "documented but not registered tools: " + "; ".join(
        f"{n} (in {', '.join(f)})" for n, f in sorted(missing.items()))
