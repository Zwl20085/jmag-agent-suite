"""Self-contained Markdown report folder from what the bridge already knows (pure file I/O, no JMAG import).

Layout written to ``out_dir``::

    report.md       title, verdict overview, per-study setup / verdict / invariants / results / sweeps / images,
                    open doubts, reproduction appendix
    images/         copies of every referenced image (the folder can be zipped or moved as a whole)
    replay.py       replay script from the operation journal (when journal rows are given)

Rule: FAIL and CHECK findings are always written in full; a study without a health block is reported as
"NOT CHECKED" and becomes an open doubt. A report never looks cleaner than the evidence.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any


def _cell(v: Any) -> str:
    if isinstance(v, float):
        v = f"{v:.6g}" if math.isfinite(v) else str(v)
    elif isinstance(v, (dict, list)):
        v = json.dumps(v, default=str)
    s = "" if v is None else str(v)
    return s.replace("|", "\\|").replace("\n", " ")


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_") or "x"


def verdict_of(study: dict) -> str:
    h = study.get("health")
    if not h:
        return "NOT CHECKED"
    return h.get("verdict", "NOT CHECKED")


def verdict_block(health: dict | None) -> str:
    """Verdict + every error and warning finding (never summarised away); infos as a short list."""
    if not health:
        return "**Verdict: NOT CHECKED** - no health check was run for this study; do not trust its numbers yet.\n"
    v = health.get("verdict", "?")
    lines = [f"**Verdict: {v}** (errors {health.get('counts', {}).get('error', 0)}, "
             f"warnings {health.get('counts', {}).get('warning', 0)})\n"]
    findings = health.get("findings", [])
    serious = [f for f in findings if f.get("level") in ("error", "warning")]
    if serious:
        lines.append(table(["level", "code", "message", "fix"],
                           [[f["level"].upper(), f.get("code"), f.get("message"), f.get("fix", "")] for f in serious]))
    infos = [f for f in findings if f.get("level") == "info"]
    if infos:
        lines.append("Info: " + "; ".join(f"`{f.get('code')}` {f.get('message')}" for f in infos) + "\n")
    return "\n".join(lines)


def _copy_images(paths: list[str], dest: Path, prefix: str) -> list[tuple[str, str]]:
    out = []
    for p in paths or []:
        src = Path(p)
        if not src.is_file():
            out.append((p, ""))
            continue
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"{_slug(prefix)}__{src.name}"
        shutil.copy2(src, target)
        out.append((p, f"images/{target.name}"))
    return out


def _study_section(s: dict, img_dir: Path, include_images: bool, doubts: list[str]) -> str:
    name = s.get("name", "?")
    md = [f"## Study `{name}`\n"]
    if s.get("error"):
        md.append(f"> Could not collect data: {s['error']}\n")
        doubts.append(f"{name}: data collection failed ({s['error']})")
    if s.get("setup"):
        md.append("### Setup\n")
        md.append(table(["item", "value"], [[k, v] for k, v in s["setup"].items()]))
    md.append("### Health\n")
    for stage, h in (("pre-run", s.get("precheck")), ("post-run", s.get("health"))):
        if h is None and stage == "pre-run":
            continue
        md.append(f"**{stage}**\n\n" + verdict_block(h))
    pre = (s.get("precheck") or {}).get("verdict")
    if pre in ("FAIL", "CHECK"):
        doubts.append(f"{name}: pre-run verdict {pre}")
    v = verdict_of(s)
    if v in ("FAIL", "CHECK", "NOT CHECKED"):
        doubts.append(f"{name}: verdict {v} - resolve the findings above before using its numbers")
    inv = s.get("invariants") or []
    if inv:
        md.append("### Physics invariants\n")
        md.append(table(["check", "ok", "key numbers"],
                        [[i.get("check"), i.get("ok"), {k: v for k, v in i.items() if k not in ("check", "ok")}]
                         for i in inv]))
        doubts += [f"{name}: invariant {i.get('check')} failed" for i in inv if i.get("ok") is False]
    if s.get("convergence"):
        md.append("### Convergence\n")
        md.append("```json\n" + json.dumps(s["convergence"], indent=1, default=str)[:3000] + "\n```\n")
    rs = s.get("result_summary") or {}
    if rs:
        md.append("### Results (source: study / table / column)\n")
        rows = []
        for t, cols in rs.items():
            for c, st in cols.items():
                rows.append([t, c] + [st.get(k) for k in ("mean", "rms", "min", "max", "pk_pk", "ripple_pct_of_mean")])
        md.append(table(["table", "column", "mean", "rms", "min", "max", "pk-pk", "ripple % of mean"], rows))
    for sw in s.get("sweeps") or []:
        md.append(f"### Sweep: {sw.get('title', '')}\n")
        md.append(table(list(sw.get("columns", [])), sw.get("rows", [])))
    if include_images and s.get("images"):
        md.append("### Images\n")
        for src, rel in _copy_images(s["images"], img_dir, name):
            if rel:
                md.append(f"![{Path(src).name}]({rel})\n")
            else:
                md.append(f"- image missing: `{src}`\n")
                doubts.append(f"{name}: image {src} was not found")
    return "\n".join(md)


def _appendix(journal: list[dict] | None, script: str | None) -> str:
    md = ["## Appendix: reproduction\n"]
    if not journal:
        md.append("No journal entries were supplied; the model build cannot be replayed from this report.\n")
        return "\n".join(md)
    if script:
        md.append(f"Replay script: `{Path(script).name}` (run with JMAG's bundled Python; see its header).\n")
    rows = [[i + 1, r.get("op"), "ok" if r.get("ok") else ("?" if r.get("ok") is None else "FAILED"),
             json.dumps(r.get("args"), default=str)[:160]] for i, r in enumerate(journal)]
    md.append(table(["#", "op", "status", "args (truncated)"], rows))
    return "\n".join(md)


def build_report(out_dir: str, title: str, studies: list[dict], notes: str | list[str] | None = None,
                 include_images: bool = True, journal: list[dict] | None = None, install: Any = None,
                 doubts: list[str] | None = None, sweeps: list[dict] | None = None) -> dict:
    """Write report.md (+ images/, replay.py) into out_dir and return {"path", "verdicts", "open_doubts", ...}.

    studies: [{"name", "setup": {..}, "precheck": summarize(...), "health": summarize(...), "invariants": [...],
               "convergence": {..}, "result_summary": {table: {col: stats}}, "sweeps": [{"title", "columns", "rows"}],
               "images": [paths], "error": str}]  - every key except name is optional.
    sweeps: study-independent sweep tables [{"title", "columns", "rows"}], written in their own section.
    """
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    img_dir = out / "images"
    open_doubts = list(doubts or [])
    sections = [_study_section(s, img_dir, include_images, open_doubts) for s in studies]
    verdicts = {s.get("name", "?"): verdict_of(s) for s in studies}
    md = [f"# {title}\n", f"Generated {time.strftime('%Y-%m-%d %H:%M')} by JMAG Agent Suite.\n",
          "## Verdict overview\n", table(["study", "verdict"], [[k, v] for k, v in verdicts.items()])]
    if not studies:
        open_doubts.append("the report covers no studies")
    md += sections
    for sw in sweeps or []:
        md.append(f"## Sweep: {sw.get('title', '')}\n")
        md.append(table(list(sw.get("columns", [])), sw.get("rows", [])))
    note_list = [notes] if isinstance(notes, str) else list(notes or [])
    if note_list:
        md.append("## Notes\n")
        md += [f"- {n}" for n in note_list]
        md.append("")
    md.append("## Open doubts\n")
    md += [f"- {d}" for d in open_doubts] if open_doubts else ["- none recorded (state what was *not* checked)"]
    md.append("")
    script = None
    if journal:
        from .replay import write_replay_script
        script = write_replay_script(journal, str(out / "replay.py"), install)
    md.append(_appendix(journal, script))
    path = out / "report.md"
    path.write_text("\n".join(md), encoding="utf-8")
    images = sorted(str(p) for p in img_dir.glob("*")) if img_dir.is_dir() else []
    return {"path": str(path), "out_dir": str(out), "verdicts": verdicts, "open_doubts": open_doubts,
            "images": images, "replay_script": script}
