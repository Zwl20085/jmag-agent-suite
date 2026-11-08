"""Release plumbing: one version everywhere, and both READMEs list the same (complete) tool set."""
import asyncio
import json
import re
from pathlib import Path

from jmag_bridge import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    pyproject = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    changelog = re.search(r"^## (\d+\.\d+\.\d+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert pyproject and changelog
    assert pyproject.group(1) == __version__ == plugin == changelog.group(1), (
        pyproject.group(1), __version__, plugin, changelog.group(1))


def _tools_table(path: Path) -> set[str]:
    """Tool names in the 'Tools by task' table: the rows inside the <details> block whose summary counts MCP tools."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"<summary>\d+[^<]*MCP[^<]*<code>jmag_</code>[^<]*</summary>(.*?)</details>", text, re.S)
    assert m, f"no tools table in {path.name}"
    return set(re.findall(r"`([a-z0-9_]+)`", m.group(1)))


def _summary_count(path: Path) -> int:
    return int(re.search(r"<summary>(\d+)[^<]*MCP", path.read_text(encoding="utf-8")).group(1))


def test_readmes_list_the_same_complete_tool_set():
    from jmag_bridge.server import mcp
    registered = {t.name[len("jmag_"):] for t in asyncio.run(mcp.list_tools())}
    en, zh = ROOT / "README.md", ROOT / "README.zh-CN.md"
    en_tools, zh_tools = _tools_table(en), _tools_table(zh)
    assert en_tools == zh_tools, en_tools ^ zh_tools
    assert en_tools == registered, {"missing from README": registered - en_tools, "not a tool": en_tools - registered}
    assert _summary_count(en) == _summary_count(zh) == len(registered)
