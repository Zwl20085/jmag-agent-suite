"""Turn the operation journal into a standalone, reviewable replay script."""
from __future__ import annotations

import json
from pathlib import Path

from .config import JmagInstall

HEADER = '''# -*- coding: utf-8 -*-
r"""Replay of a jmag-mcp-bridge session ({n} operations).

Run with JMAG's bundled interpreter:
    "{python}" "{this}" [--pid <running JMAG pid>]
Each step stops on the first failure and prints JMAG's messages.
"""
import json, sys
sys.path.insert(0, r"{worker_dir}")
sys.path.insert(1, r"{home}")
import ops

ctx = ops.Context(r"{home}")
pid = int(sys.argv[sys.argv.index("--pid") + 1]) if "--pid" in sys.argv else None


def step(op, args):
    resp = ctx.dispatch(op, args)
    print(op, "ok" if resp["ok"] else "FAILED", json.dumps(resp.get("error") or "", default=str)[:300])
    if not resp["ok"]:
        print("jmag messages:", (resp.get("jmag") or {{}}).get("messages"))
        sys.exit(1)
    return resp.get("result")


'''


def write_replay_script(rows: list[dict], path: str, install: JmagInstall | None) -> str:
    worker_dir = Path(__file__).parent / "worker"
    p = Path(path).resolve()
    home = str(install.home) if install else ""
    python = str(install.python) if install else "python"
    lines = [HEADER.format(n=len(rows), python=python, this=p, worker_dir=worker_dir, home=home)]
    for r in rows:
        if not r.get("ok") and r.get("op") != "run":
            lines.append(f"# skipped (failed in session): {r['op']} {json.dumps(r.get('args'), default=str)}\n")
            continue
        args = dict(r.get("args") or {})
        if r["op"] == "connect":
            lines.append('step("connect", {"pid": pid, "launch": pid is None})\n')
            continue
        lines.append(f"step({r['op']!r}, {json.dumps(args, default=str)})\n")
    p.write_text("".join(lines), encoding="utf-8")
    return str(p)
