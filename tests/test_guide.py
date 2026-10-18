"""The skills travel with the server: jmag_bridge.guide reads them, jmag_guide/jmag://guide serve them (no JMAG)."""
import asyncio
from pathlib import Path

import pytest

from jmag_bridge import guide, server

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {"jmag", "jmag-experiments", "jmag-geometry", "jmag-losses-efficiency", "jmag-machine-design",
          "jmag-parametric-optimisation", "jmag-postprocess", "jmag-scripting", "jmag-simulation",
          "jmag-troubleshooting"}


def test_checkout_uses_live_skills():
    assert Path(str(guide.skills_root())).resolve() == (ROOT / ".claude" / "skills").resolve()


def test_list_has_exactly_the_ten_skills_router_first():
    skills = guide.list_skills()
    assert {s["name"] for s in skills} == SKILLS and len(skills) == 10
    assert skills[0]["name"] == "jmag"
    assert all(len(s["description"]) > 40 for s in skills)


def test_read_skill_and_reference():
    assert guide.read_skill("jmag").startswith("---\nname: jmag\n")
    assert "properties.md" in guide.list_references("jmag-simulation")
    text = guide.read_skill("jmag-simulation", "properties.md")
    assert text == (ROOT / ".claude/skills/jmag-simulation/references/properties.md").read_text(encoding="utf-8")
    assert guide.read_skill("jmag-simulation", "properties") == text  # .md optional


@pytest.mark.parametrize("bad", ["../jmag", "jmag/references", "jmag\\x", "..", ".hidden", "C:jmag", ""])
def test_bad_names_rejected(bad):
    with pytest.raises(ValueError):
        guide.read_skill(bad)
    with pytest.raises(ValueError):
        guide.read_skill("jmag", bad or " ")


def test_unknown_names_are_key_errors():
    with pytest.raises(KeyError):
        guide.read_skill("jmag-nope")
    with pytest.raises(KeyError):
        guide.read_skill("jmag", "nope.md")


def test_front_matter_folded_and_quoted():
    fm = guide._front_matter('---\nname: "x"\ndescription: >\n  one\n  two\n---\nbody')
    assert fm == {"name": "x", "description": "one two"}
    assert guide._front_matter("no header") == {}


def test_wheel_config_packages_the_skills():
    tomllib = pytest.importorskip("tomllib")
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"]["build"]["targets"]
    assert cfg["wheel"]["force-include"][".claude/skills"] == "jmag_bridge/skills"
    assert cfg["sdist"]["force-include"][".claude/skills"] == ".claude/skills"


def test_guide_tool_default_is_index_plus_router():
    r = asyncio.run(server.jmag_guide())
    assert r["ok"] and r["result"]["topic"] == "jmag"
    assert {s["name"] for s in r["result"]["skills"]} == SKILLS
    assert r["result"]["text"] == guide.read_skill("jmag")
    assert "tool-map.md" in r["result"]["references"]


def test_guide_tool_topic_reference_and_errors():
    r = asyncio.run(server.jmag_guide(topic="jmag-troubleshooting"))
    assert r["ok"] and "finding-codes.md" in r["result"]["references"]
    r = asyncio.run(server.jmag_guide(topic="jmag-simulation", reference="feedback.md"))
    assert r["ok"] and r["result"]["text"] == guide.read_skill("jmag-simulation", "feedback.md")
    r = asyncio.run(server.jmag_guide(reference="tool-map.md"))  # unique -> topic found
    assert r["ok"] and r["result"]["topic"] == "jmag"
    r = asyncio.run(server.jmag_guide(topic="nope"))
    assert not r["ok"] and "jmag-simulation" in r["error"]["hint"]
    r = asyncio.run(server.jmag_guide(topic="jmag", reference="nope.md"))
    assert not r["ok"] and "tool-map.md" in r["error"]["hint"]
    r = asyncio.run(server.jmag_guide(topic="../secrets"))
    assert not r["ok"]


def test_guide_resources_when_supported():
    if not hasattr(server.mcp, "read_resource"):
        pytest.skip("SDK without resources")
    uris = {str(r.uri) for r in asyncio.run(server.mcp.list_resources())}
    assert "jmag://guide" in uris
    out = asyncio.run(server.mcp.read_resource("jmag://guide/jmag-simulation"))
    text = "".join(getattr(c, "content", getattr(c, "text", "")) for c in out)
    assert text == guide.read_skill("jmag-simulation")
