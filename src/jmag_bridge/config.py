"""Locate the JMAG-Designer installation and the bridge's state directory."""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_GLOBS = [
    r"C:\Program Files\JMAG-Designer*",
    r"D:\Program Files\JMAG-Designer*",
    r"E:\Program Files\JMAG-Designer*",
    r"C:\JMAG-Designer*",
    r"D:\JMAG-Designer*",
]


@dataclass
class JmagInstall:
    home: Path
    version: str
    python: Path

    @property
    def designer_py(self) -> Path:
        return self.home / "jmag" / "designer.py"

    def as_dict(self) -> dict:
        return {"home": str(self.home), "version": self.version, "python": str(self.python)}


def _version_of(home: Path) -> str:
    vf = home / "version"
    if vf.is_file():
        text = vf.read_text(errors="ignore").strip()
        m = re.search(r"\d+\.\d+\.\w+", text)
        return m.group(0) if m else text
    m = re.search(r"(\d+\.\d+)", home.name)
    return m.group(1) if m else "unknown"


def _valid(home: Path) -> bool:
    return (home / "designer.exe").is_file() and (home / "jmag" / "designer.py").is_file()


def find_jmag(explicit: str | None = None) -> JmagInstall | None:
    """JMAG_HOME env var > explicit arg > newest match of DEFAULT_GLOBS."""
    candidates: list[Path] = []
    for c in (os.environ.get("JMAG_HOME"), explicit):
        if c:
            candidates.append(Path(c))
    found = []
    for pattern in DEFAULT_GLOBS:
        found += [Path(p) for p in glob.glob(pattern)]
    found.sort(key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)] or [0], reverse=True)
    candidates += found
    for home in candidates:
        if _valid(home):
            py = Path(os.environ.get("JMAG_PYTHON") or home / "python3.8" / "python.exe")
            return JmagInstall(home=home, version=_version_of(home), python=py)
    return None


def state_dir() -> Path:
    """State folder: JMAG_BRIDGE_HOME, else %LOCALAPPDATA%/jmag-agent-suite (an existing
    jmag-mcp-bridge folder from older installs keeps being used, so journals and docs indexes survive)."""
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    base = os.environ.get("JMAG_BRIDGE_HOME")
    if not base:
        legacy = os.path.join(root, "jmag-mcp-bridge")
        base = legacy if os.path.isdir(legacy) else os.path.join(root, "jmag-agent-suite")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_state() -> dict:
    f = state_dir() / "state.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return {}


def save_state(**kw) -> None:
    st = load_state()
    st.update(kw)
    (state_dir() / "state.json").write_text(json.dumps(st, indent=2))
