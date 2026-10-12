"""Search the JMAG documentation installed on *this* machine.

Nothing from JMAG is shipped with the bridge. On first use we decompile the
installed ``Help/en/script-designer.chm`` (property names for conditions, circuit
components, study settings, result types ...) with Windows' ``hh.exe`` into the
user's cache, and parse ``<JMAG>/jmag/designer.py`` (the Python API with
docstrings) for API lookups.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

from .config import JmagInstall, state_dir


def _html_to_text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
    t = re.sub(r"(?i)<br\s*/?>|</p>|</tr>|</h\d>|</li>|</div>", "\n", t)
    t = re.sub(r"(?i)</td>|</th>", " | ", t)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


class DocsIndex:
    def __init__(self, install: JmagInstall):
        self.install = install
        self.dir = state_dir() / "docs" / install.version
        self.docs: list[dict] | None = None
        self.api: list[dict] | None = None

    # ------------------------------------------------------------------ build
    def ensure(self) -> dict:
        idx = self.dir / "index.json"
        if not idx.is_file():
            self._build()
        if self.docs is None:
            self.docs = json.loads(idx.read_text(encoding="utf-8"))
        return {"documents": len(self.docs), "cache": str(self.dir)}

    def _build(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        chm = self.install.home / "Help" / "en" / "script-designer.chm"
        docs: list[dict] = []
        if chm.is_file():
            local = self.dir / "script-designer.chm"
            shutil.copyfile(chm, local)  # hh.exe fails on paths with spaces
            out = self.dir / "html"
            subprocess.run(["hh.exe", "-decompile", "html", local.name], cwd=str(self.dir), timeout=300,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for f in sorted(out.rglob("*.htm*")):
                text = _html_to_text(f.read_text(encoding="utf-8", errors="ignore"))
                if len(text) < 40:
                    continue
                title = text.split("\n", 1)[0][:120]
                docs.append({"file": f.name, "title": title, "text": text})
        (self.dir / "index.json").write_text(json.dumps(docs), encoding="utf-8")
        self.docs = docs

    # ------------------------------------------------------------------ search
    def search(self, query: str, limit: int = 6, context_lines: int = 3) -> list[dict]:
        self.ensure()
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        if not terms:
            return []
        scored = []
        for d in self.docs or []:
            low = d["text"].lower()
            hits = [low.count(t) for t in terms]
            if not all(hits):
                continue
            score = sum(min(h, 20) for h in hits) + 15 * sum(t in d["title"].lower() for t in terms)
            if d["file"].startswith("script"):
                score += 10  # property tables are the most useful pages
            scored.append((score, d))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, d in scored[:limit]:
            lines = d["text"].split("\n")
            snippets, used = [], set()
            for k, line in enumerate(lines):
                if any(t in line.lower() for t in terms) and k not in used:
                    lo, hi = max(0, k - 1), min(len(lines), k + context_lines + 1)
                    snippets.append("\n".join(lines[lo:hi]))
                    used.update(range(lo, hi))
                if len(snippets) >= 4:
                    break
            out.append({"page": d["file"], "title": d["title"], "score": score, "snippets": snippets})
        return out

    def page(self, name: str, max_chars: int = 20000) -> str:
        self.ensure()
        for d in self.docs or []:
            if d["file"] == name or d["file"].rsplit(".", 1)[0] == name:
                return d["text"][:max_chars]
        raise KeyError(name)

    # ------------------------------------------------------------------ python API
    def _parse_api(self) -> list[dict]:
        if self.api is not None:
            return self.api
        api = []
        for mod in ("designer.py", "modeller.py"):
            f = self.install.home / "jmag" / mod
            if not f.is_file():
                continue
            cls = None
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines):
                m = re.match(r"class (\w+)\(", line)
                if m:
                    cls = m.group(1)
                    continue
                m = re.match(r"    def (\w+)\((.*)\)", line)
                if not m or m.group(1).startswith("_"):
                    continue
                doc = []
                j = i + 1
                while j < len(lines) and not lines[j].startswith(("    def ", "class ")):
                    s = lines[j].strip()
                    if s and not s.startswith(("return super", "super()", '"""')) and set(s) != {"-"}:
                        doc.append(s)
                    j += 1
                args = m.group(2).replace("self, ", "").replace("self", "")
                api.append({"module": mod[:-3], "class": cls, "method": m.group(1), "signature": f"{cls}.{m.group(1)}({args})",
                            "doc": " ".join(doc)[:1500]})
        self.api = api
        return api

    def api_lookup(self, query: str, limit: int = 15) -> list[dict]:
        api = self._parse_api()
        q = query.strip()
        if "." in q:
            c, mth = q.split(".", 1)
            hits = [a for a in api if a["class"].lower() == c.lower() and a["method"].lower().startswith(mth.lower())]
            if hits:
                return hits[:limit]
        exact = [a for a in api if a["method"].lower() == q.lower()]
        part = [a for a in api if q.lower() in a["method"].lower() and a not in exact]
        words = [w for w in q.lower().split() if w]
        docs = [a for a in api if words and all(w in (a["doc"] + a["signature"]).lower() for w in words)
                and a not in exact and a not in part]
        return (exact + part + docs)[:limit]

    def class_methods(self, cls: str) -> list[str]:
        return [a["method"] for a in self._parse_api() if a["class"] == cls]
