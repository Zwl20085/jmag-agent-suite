"""JMAG MCP server: tools for driving JMAG-Designer with feedback at every step.

Every tool returns an envelope:

    {"ok": bool, "result": ..., "error": {"message", "hint"}, "jmag_messages": [...], "seconds": float}

``ok`` is False when the operation raised *or* JMAG logged an error while it ran
(JMAG rarely raises; it logs). Read ``hint`` - it usually names the fix. ``ok`` says the tool ran; whether
the physics passed is ``result.passed`` / ``result.verdict`` (jmag_check, jmag_physics_check).
Exceptions: jmag_dialogs returns a list; image tools return [json envelope, Image].
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore
from mcp.types import ToolAnnotations

from . import compare, estimate, fielddata, health, materials, plotting, reporting, solver_monitor, star, units, winding, winui
from . import server_support as sup
from .server_support import (_field_quantities, _field_summary, _probe_harmonics, _round, _setup_table, _sig,
                             _visible_parts, wrap)
from .config import find_jmag, load_state, save_state, state_dir
from .docs_index import DocsIndex
from .worker_client import Worker, WorkerError, abandon_all

INSTRUCTIONS = """\
Bridge to JMAG-Designer (electromagnetic FEA). Typical loop:
jmag_status -> jmag_connect -> jmag_new_project/jmag_open_project -> jmag_import_dxf (2D) or jmag_import_cad (3D STEP)
-> jmag_rename_parts -> jmag_create_study -> jmag_set_material -> stack length via jmag_set_properties(kind='study',
{'ModelThickness': mm}) -> excitation (static: jmag_add_current; transient: jmag_circuit_quick + jmag_add_fem_coil)
-> jmag_set_step -> jmag_check(stage='pre') -> jmag_run -> jmag_check(stage='post') -> jmag_get_result / jmag_contour_image.
Rules: never trust a step that returned ok=false; read error.hint and jmag_messages. Geometry is drawn outside JMAG
(DXF for 2D, STEP for 3D). If a call times out or JMAG seems stuck, call jmag_dialogs (modal dialog?) and jmag_screenshot.
Unknown property names: jmag_get_properties lists valid ones; jmag_docs_search searches the local JMAG manual.
Before FEA: jmag_estimate / jmag_winding_plan / jmag_units give the expected numbers (no JMAG needed). After:
jmag_plot, jmag_compare, jmag_make_report; jmag_inventory shows the whole project tree.
If your client did not load the jmag skills (not Claude Code), call jmag_guide first and follow its safety rules.
Run contract: jmag_run returns state running|finished|failed with ok and solver_aborted (ok=false or
solver_aborted=true: no usable results). While state='running', poll jmag_run_status; it is not an error.
Cancel a solve only with jmag_cancel_run - never jmag_dialogs(click='Cancel') on a 'Run Analysis' window.
Set wait_seconds below your client's tool-call timeout; long tools state their maximum blocking time.
ok=true means the tool ran: physics passed only when result.passed is true; result.unverified lists missing checks.
"""

mcp = _Server("jmag", instructions=INSTRUCTIONS)
READ_ONLY = ToolAnnotations(readOnlyHint=True)

READ_ONLY_OPS = {"ping", "app_info", "list_parts", "get_properties", "get_equations", "find_materials", "list_conditions",
                 "circuit_summary", "calc_folder", "report", "list_results", "get_result", "study_facts"}


WORKER_HINT = "worker failed; call jmag_status, then jmag_connect again or jmag_doctor"


class Bridge:
    def __init__(self) -> None:
        self.install = find_jmag()
        self.main: Worker | None = Worker(self.install, "main") if self.install else None
        self.job: dict | None = None
        self.docs = DocsIndex(self.install) if self.install else None
        self.journal = state_dir() / "journal.jsonl"
        self.session = time.strftime("%Y%m%d-%H%M%S")

    def need_install(self) -> None:
        if not self.install:
            raise WorkerError("JMAG-Designer installation not found. Set JMAG_HOME to the folder containing designer.exe.")

    async def call(self, op: str, timeout: float = 600, **args: Any) -> dict:
        self.need_install()
        if self.job and not self.job.get("done") and op not in READ_ONLY_OPS:
            return {"ok": False, "error": {"message": f"a solve is running ({self.job['study']}); only read-only tools are allowed",
                                           "hint": "poll jmag_run_status or stop it with jmag_cancel_run"}}
        assert self.main is not None
        try:
            resp = await asyncio.to_thread(self.main.call, op, timeout, **args)
        except TimeoutError as e:
            dlg = await asyncio.to_thread(winui.dialogs)
            return {"ok": False, "error": {"message": str(e), "hint": "JMAG may be blocked by a modal dialog; worker was restarted, "
                                           "call jmag_connect again", "dialogs": dlg}}
        except WorkerError as e:
            return {"ok": False, "error": {"message": str(e), "hint": WORKER_HINT}}
        if op not in READ_ONLY_OPS:
            self._journal(op, args, resp.get("ok"))
        return resp

    def _journal(self, op: str, args: dict, ok: bool | None) -> None:
        try:
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"session": self.session, "t": time.time(), "op": op, "args": args, "ok": ok}, default=str) + "\n")
        except OSError:
            pass


B = Bridge()
THD_ORDERS = 15     # jmag_get_result: THD over harmonic orders 2..15
VERIFY = sup.VerifyRecord()          # which studies had post-check / physics-check on their current results


def envelope(resp: dict) -> dict:
    out: dict = {"ok": bool(resp.get("ok"))}
    if "result" in resp and resp["result"] is not None:
        out["result"] = resp["result"]
    if resp.get("error"):
        err = dict(resp["error"])
        err.pop("trace", None) if out["ok"] else None
        out["error"] = err
    msgs = (resp.get("jmag") or {}).get("messages")
    if msgs:
        out["jmag_messages"] = msgs
    out["seconds"] = resp["elapsed"] if resp.get("elapsed") is not None else 0.0
    return out


#: Ops after which the verification record no longer describes the project (new project, new JMAG).
_PROJECT_OPS = {"project_new", "project_open", "connect", "quit_app"}


async def run_op(op: str, timeout: float = 600, **args: Any) -> dict:
    if op == "run":   # a solve in the main worker (static inductance, loss study); runner solves invalidate in jmag_run*
        VERIFY.invalidate(args.get("study"), args.get("model"))      # new results: earlier checks no longer apply
    env = envelope(await B.call(op, timeout, **{k: v for k, v in args.items() if v is not None}))
    if op in _PROJECT_OPS and env.get("ok"):
        VERIFY.clear()
    return env


def out_path(kind: str, path: str | None, suffix: str = ".png") -> str:
    if path:
        return str(Path(path).resolve())
    d = state_dir() / "outputs"
    d.mkdir(exist_ok=True)
    return str(d / f"{kind}_{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:4]}{suffix}")


def with_image(env: dict, key: str = "path") -> list:
    items: list = [json.dumps(env, indent=1, default=str)]
    p = (env.get("result") or {}).get(key)
    if env.get("ok") and p and os.path.isfile(p):
        items.append(Image(path=p))
    return items


# =========================================================================== session

@mcp.tool()
async def jmag_status() -> dict:
    """Installation, worker, JMAG connection and modal-dialog state. Call this first in every conversation.
    result: install, state_dir, running_instances (JMAG windows on this machine), remembered_jmag_pid, worker (ping),
    jmag (app_info, only when connected), dialogs (progress windows carry kind='progress'), job (current or last solve
    of this bridge), and for solves the bridge may not own (server restart, GUI run): solver_processes (ExecSolver/jam
    pids), newest_monitor_age_s (seconds since the newest solver monitor file was written) and stalled (true when that
    age exceeds 600 s while a solver process exists: look at jmag_dialogs / jmag_screenshot, stop it only with
    jmag_cancel_run). Blocks at most ~80 s.
    No JMAG install found: ok=false, result.install null, hint = set JMAG_HOME (jmag_doctor diagnoses the install).
    """
    t0 = time.time()
    st: dict = {"install": B.install.as_dict() if B.install else None, "state_dir": str(state_dir())}
    if not B.install:
        return wrap(st, ok=False, t0=t0, error={
            "message": "JMAG-Designer installation not found",
            "hint": "set JMAG_HOME to the JMAG-Designer folder (contains designer.exe and python3.8/); jmag_doctor "
                    "diagnoses the install"})
    from .launcher import running_instances
    st["remembered_jmag_pid"] = load_state().get("jmag_pid")
    st["running_instances"] = await asyncio.to_thread(running_instances)
    if B.main and B.main.alive():
        st["worker"] = envelope(await B.call("ping", 20))
        if (st["worker"].get("result") or {}).get("connected"):
            st["jmag"] = envelope(await B.call("app_info", 60))
    else:
        st["worker"] = "not started (jmag_connect starts it)"
    st["dialogs"] = await asyncio.to_thread(winui.dialogs)
    if B.job:
        st["job"] = {k: B.job[k] for k in ("id", "study", "started", "done") if k in B.job}
    st.update(await _solver_status())
    return wrap(st, t0=t0)


async def _solver_status() -> dict:
    """Solver processes / newest monitor age / stalled, also for solves this bridge did not start."""
    folder = B.job.get("folder") if B.job and not B.job.get("done") else None
    try:
        return await asyncio.to_thread(sup.solver_status, folder)
    except (OSError, ValueError) as e:  # the status must never fail because process listing failed
        return {"solver_processes": [], "newest_monitor_age_s": None, "stalled": False,
                "solver_status_error": str(e)}


@mcp.tool()
async def jmag_connect(pid: int | None = None, launch_new: bool = False, visible: bool = True) -> dict:
    """Connect to JMAG-Designer.

    - pid: attach to that running instance (also works for a JMAG the engineer opened by hand).
    - no pid: re-attach to the instance this bridge used last; if there is none and other JMAG windows are open,
      they are listed so you can choose (they may hold the engineer's unsaved work) - pass pid=... or launch_new=true.
    - launch_new=true: start a new JMAG detached from this agent session, so it survives when the session ends.
    """
    from .launcher import launch_detached, running_instances
    from .worker.ops import pid_alive  # pure-python helper, safe to import here
    B.need_install()
    if pid and not pid_alive(pid):
        return {"ok": False, "error": {"message": f"JMAG process {pid} is not running",
                                       "hint": "jmag_status lists running instances; or launch_new=true"}}
    target = pid
    if not target and not launch_new:
        remembered = load_state().get("jmag_pid")
        if remembered and pid_alive(remembered):
            target = remembered
        else:
            others = await asyncio.to_thread(running_instances)
            if others:
                return {"ok": False, "running_instances": others,
                        "error": {"message": "JMAG is already running but not connected to this bridge",
                                  "hint": "attach with pid=<pid> (check the title is the right project) or launch_new=true"}}
            launch_new = True
    launched = None
    if launch_new:
        try:
            launched = await asyncio.to_thread(launch_detached, B.install)
        except (RuntimeError, TimeoutError) as e:
            return {"ok": False, "error": {"message": str(e)}, "dialogs": await asyncio.to_thread(winui.dialogs)}
        target = launched["pid"]
    env = await run_op("connect", 300, pid=target, launch=False, visible=visible)
    if env["ok"]:
        save_state(jmag_pid=env["result"]["jmag_pid"])
        env["result"]["launched"] = bool(launched)
    return env


@mcp.tool()
async def jmag_quit(save: bool = False) -> dict:
    """Close the connected JMAG-Designer. save=False (default) discards unsaved changes: call jmag_save_project first if the
    project matters. Refused while a solve runs (use jmag_cancel_run). Forgets the remembered pid, so the next session
    starts with jmag_connect. Blocks up to 120 s.
    """
    env = await run_op("quit_app", 120, save=save)
    if env["ok"]:
        save_state(jmag_pid=None)
    return env


@mcp.tool()
async def jmag_dialogs(click: str | None = None, title_filter: str | None = None) -> list | dict:
    """List the dialogs JMAG shows (title, text, buttons, kind) or press one button. JMAG blocks on dialogs
    ('save project before creating geometry', licence prompts): when a call hangs or times out, look here first.
    click='<button label>' presses it; pass title_filter (a regex on the title) so exactly the dialog you inspected is
    targeted. Progress windows (kind='progress', the 'Run Analysis' window of a solve) are listed but must be left
    alone: NEVER click Cancel or Abort there; stop a solve only with jmag_cancel_run (it answers JMAG's prompt in the
    verified order). For an unknown dialog prefer Cancel/No, or ask the user.
    Returns a list (not the usual envelope).
    """
    if click:
        return await asyncio.to_thread(winui.click, click, title_filter or "")
    return await asyncio.to_thread(winui.dialogs, title_filter or "")


@mcp.tool()
async def jmag_screenshot(out_dir: str | None = None) -> list:
    """Capture every JMAG window (main window, Geometry Editor, dialogs) as PNG, even when hidden behind others.
    out_dir: folder for the files (default: a folder under the bridge outputs). Returns the list of window rows (with
    the PNG path) plus up to 4 images. Use it when a call hangs or a result looks odd. Changes nothing in JMAG and
    needs no connection.
    """
    d = out_dir or str(state_dir() / "outputs" / f"screens_{time.strftime('%H%M%S')}")
    rows = await asyncio.to_thread(winui.screenshots, d)
    items: list = [json.dumps(rows, indent=1)]
    for r in rows[:4]:
        items.append(Image(path=r["path"]))
    return items


# =========================================================================== project

@mcp.tool()
async def jmag_new_project(path: str, overwrite: bool = False) -> dict:
    """Create a new project and save it at `path` (*.jproj, absolute path recommended). A saved project is required before
    any DXF import: the Geometry Editor blocks forever on a modal dialog otherwise. overwrite=False refuses to replace an
    existing file. Example: jmag_new_project("D:/work/spm12s10p.jproj"). Blocks up to 120 s.
    """
    return await run_op("project_new", 120, path=path, overwrite=overwrite)


@mcp.tool()
async def jmag_open_project(path: str) -> dict:
    """Open an existing *.jproj; it becomes the current project. Work on a COPY of a valuable project: later calls
    modify it and jmag_save_project overwrites it. Follow with jmag_inventory to see models, studies, conditions and
    results before changing anything. Blocks up to 600 s for large projects.
    """
    return await run_op("project_open", 600, path=path)


@mcp.tool()
async def jmag_save_project(path: str | None = None) -> dict:
    """Save the current project; with `path` it is Save As and the open project becomes that file. Save before risky
    steps: the first mesh/run of a new setup, and jmag_export_table field kinds (a field-export sequence crashed
    Designer 23.0 once). Blocks up to 600 s.
    """
    return await run_op("project_save", 600, path=path)


@mcp.tool()
async def jmag_summary() -> dict:
    """Cheap project overview from JMAG: jmag_pid, version, project path/name, running_process, models (name, dimension
    2|3, part count) and studies (name, type, has_result). Use jmag_inventory for conditions, calc folders and result
    tables. Blocks up to 60 s.
    """
    return await run_op("app_info", 60)


# =========================================================================== geometry

@mcp.tool()
async def jmag_import_dxf(path: str, model_name: str | None = None) -> dict:
    """Import 2D geometry (DXF/SAT drawn by an external script): every closed loop becomes a region/part.

    Returns parts with area and centroid; rename them next (jmag_rename_parts). The project must be saved first.
    Lengths are interpreted in the model unit (mm by default).
    """
    return await run_op("import_dxf", 600, path=path, model_name=model_name)


@mcp.tool(annotations=READ_ONLY)
async def jmag_dxf_inspect(path: str) -> dict:
    """Read a 2D DXF (user-supplied or generated) BEFORE jmag_import_dxf: the closed regions JMAG's import will
    create, with area [mm^2], centroid, a point strictly inside each (use it for jmag_rename_parts), radii
    r_min/r_max about the origin, layer and holes. Pure Python, no JMAG; read-only.

    Role guesses per region with confidence 0..1: machine cross-sections about the origin -> stator-like,
    rotor-like, slot-like, magnet-like, airgap-like, shaft-like; other devices -> core-like, coil-like; else
    unknown. Layer names (magnet, slot, coil, stator, rotor, shaft, air, gap) raise or override a guess.
    result.labels_draft is a draft labels JSON (jmag-geometry labels-schema: parts with points, magnets with
    alternating polarity, rotor_parts, suppress, expected areas) with confidence per entry - confirm every name
    and role (confidence < 0.7 is a guess), add coils (jmag_winding_plan labels_coils) and parameters.stack_mm.
    Entities read: LINE, ARC, CIRCLE, LWPOLYLINE, POLYLINE (bulges = arcs). Edges ending on another edge are split
    there; edges crossing without an end point, SPLINE/ELLIPSE/blocks are not handled (result.warnings says so,
    open ends are counted). OCS extrusion (0,0,-1) (mirrored entities) is honoured, other non-Z extrusions are
    skipped. result.units from $INSUNITS (mm | in | unknown, with a warning: JMAG reads the numbers in the model
    unit). Areas in drawing units^2 from 1-degree arc sampling (about 0.005 % low on arcs).
    """
    from . import dxf as dxf_mod
    from .dxf_inspect import DxfReadError
    t0 = time.time()
    try:
        res = await asyncio.to_thread(dxf_mod.inspect, str(Path(path).resolve()))
    except DxfReadError as e:
        return wrap(ok=False, t0=t0, error={"message": str(e), "hint": e.hint or "check the file"})
    except (ValueError, IndexError, ZeroDivisionError) as e:
        return wrap(ok=False, t0=t0, error={"message": f"could not interpret the DXF: {e}",
                                             "hint": "re-export as ASCII DXF with lines/arcs/polylines only"})
    return wrap(res, t0=t0)


@mcp.tool()
async def jmag_import_cad(path: str, model_name: str | None = None, healing: bool = True, remove_sheet: bool = True,
                          unit: str = "mm") -> dict:
    """Import 3D CAD (STEP/IGES/Parasolid/SAT) as a 3D model; needs a saved project. unit: length unit of the file
    (default "mm"; other values unverified); healing and remove_sheet default true.
    Name the solids in your CAD script: a named Open CASCADE STEP assembly gives parts '<assembly>/<product>'
    (verified 23.0); preview the names offline with `python -m jmag_bridge.cad3d model.step`. Unnamed products get
    generic names - fix them with jmag_rename_parts using an inside point. Returns model, dimension, parts.

    BLOCKING_TOOLS: blocks up to 1800 s (large assemblies with healing). There is no wait_seconds / background
    mode (unlike jmag_run): if your client's tool timeout is shorter, the call is cut off client-side while JMAG
    keeps importing - then poll jmag_status / jmag_list_parts instead of re-importing.
    """
    return await run_op("import_cad", 1800, path=path, model_name=model_name, healing=healing,
                        remove_sheet=remove_sheet, unit=unit)


@mcp.tool()
async def jmag_list_parts(model: str | None = None) -> dict:
    """Parts of a model: id, name, centroid [x, y, z]; 2D also area and inside_point [x, y] (a point inside the part,
    unlike the centroid of C/L shapes), 3D volume. Coordinates in the model length unit (mm unless imported otherwise).
    Area is mm^2 for a mm model (verified on 23.0: SPM slot 139.27727 mm^2 = analytic); volume is in the model unit
    cubed (3D CAD volumes matched the CAD to < 2e-5). inside_point works as the "part" reference in later calls. Blocks up to 60 s.
    """
    return await run_op("list_parts", 60, model=model)


@mcp.tool()
async def jmag_rename_parts(renames: list[dict], model: str | None = None) -> dict:
    """Rename parts. renames=[{"part": <name | id | [x, y] point inside the part>, "name": "StatorCore"}, ...].

    Points are robust after import (names like '0/Region.3' are meaningless); use a point you know lies inside the part.
    """
    return await run_op("rename_parts", 120, renames=renames, model=model)


@mcp.tool()
async def jmag_suppress_parts(parts: list, suppress: bool = True, study: str | None = None, model: str | None = None) -> dict:
    """Suppress parts so JMAG treats them as absent (its automatic air region fills the space), e.g. an air-gap ring
    that the DXF closed into a region, construction helpers, or parts not needed in one study. study=None: model-wide."""
    return await run_op("suppress_parts", 120, parts=parts, suppress=suppress, study=study, model=model)


@mcp.tool()
async def jmag_model_image(path: str | None = None, show_mesh: bool = False) -> list:
    """Render the current model to a PNG and return it as an image: geometry, or the mesh with show_mesh=true (run
    jmag_mesh first). path: output file (default: under the bridge outputs). Returns result.path.
    Use after import, renaming and material assignment to check parts visually, and again after local mesh sizes.
    Blocks up to 120 s.
    """
    return with_image(await run_op("model_image", 120, path=out_path("model", path), show_mesh=show_mesh))


# =========================================================================== study & properties

@mcp.tool()
async def jmag_create_study(study_type: str, name: str, model: str | None = None) -> dict:
    """Create a study. 2D: Static2D, Transient2D, Frequency2D (also AX variants); 3D: Static, Transient, Frequency.

    Static2D has no circuit in JMAG 23 (use jmag_add_current); circuits/FEM coils need Transient or Frequency studies.
    Returns the condition types available for that study.
    """
    return await run_op("create_study", 120, study_type=study_type, name=name, model=model)


@mcp.tool()
async def jmag_duplicate_study(study: str, new_name: str, model: str | None = None) -> dict:
    """Copy a study with every setting (conditions, circuit, mesh, step control) but no results. Use it for variants
    (mesh refinement, another operating point) so the base study stays clean. new_name must be new: an existing name is
    refused before JMAG is touched (hint: another name or jmag_delete_study). Returns result.study. Blocks up to 300 s.
    """
    return await run_op("duplicate_study", 300, study=study, new_name=new_name, model=model)


@mcp.tool()
async def jmag_delete_study(study: str, model: str | None = None) -> dict:
    """Delete a study and its results; there is no undo. Refused while a solve runs. Duplicate first
    (jmag_duplicate_study) if you may want it back. Returns result.deleted. Blocks up to 120 s.
    """
    return await run_op("delete_study", 120, study=study, model=model)


@mcp.tool()
async def jmag_get_properties(study: str, kind: str = "study", name: str | None = None, index: int | None = None,
                              names: list[str] | None = None, with_help: bool = False, model: str | None = None) -> dict:
    """List property names, types and values of a settable object.

    kind: study (analysis control: ModelThickness=stack length, NonlinearMaxIteration, solver, outputs, MultiCPU ...),
    step, mesh, condition (name=), subcondition (name=, index=), component (circuit, name=), material (name=<part>).
    """
    target = {"study": study, "kind": kind, "name": name, "index": index, "model": model}
    return await run_op("get_properties", 120, target={k: v for k, v in target.items() if v is not None},
                        names=names, with_help=with_help)


@mcp.tool()
async def jmag_set_properties(study: str, values: dict, kind: str = "study", name: str | None = None,
                              index: int | None = None, strict: bool = True, model: str | None = None) -> dict:
    """Set properties on a study, step, mesh, condition, sub-condition, circuit component or material, with read-back.
    Values are in JMAG's units for the property (mm, s, deg, rpm, A, V) unless given as {"value": 3, "unit": "mm"};
    strings are equation names or expressions; lists are tables. kind picks the target as in jmag_get_properties
    (name= for conditions, components and materials (part), index= for sub-conditions). strict=True (default) rejects
    unknown names and lists the closest ones before anything is set.
    Returns result.applied: {name: {set, read, type[, unit][, mismatch: true]}}; mismatch means JMAG kept a different
    value than requested: treat it as a failure. Examples: stack length -> kind='study', {'ModelThickness': 50};
    multi-core -> {'UseMultiCPU': 1, 'MultiCPU': 8}; nonlinear -> {'NonlinearMaxIteration': 50}.
    """
    target = {"study": study, "kind": kind, "name": name, "index": index, "model": model}
    return await run_op("set_properties", 120, target={k: v for k, v in target.items() if v is not None},
                        values=values, strict=strict)


@mcp.tool()
async def jmag_set_step(study: str, steps: int | None = None, time_step: float | None = None, end_time: float | None = None,
                        step_type: int | None = None, model: str | None = None) -> dict:
    """Transient step control. steps + time_step [s] -> StepType 0; steps + end_time [s] without time_step -> StepType 1
    (uniform); step_type overrides. `steps` is the number of time points including t = 0 (steps - 1 intervals, so
    the span is (steps - 1) * time_step), which is how the bridge's checks read it.
    Rule of thumb: >= 30-60 steps per electrical period, enough periods for steady state (jmag_estimate
    kind='step_plan' gives the arguments). Returns applied and the read-back StepType, Step, TimeStep, EndPoint,
    StepDivision. JMAG may rewrite step control for motion: use the returned time axis of the results.
    """
    return await run_op("set_step", 120, study=study, steps=steps, time_step=time_step, end_time=end_time,
                        step_type=step_type, model=model)


@mcp.tool()
async def jmag_set_equations(study: str, equations: dict, descriptions: dict | None = None, model: str | None = None) -> dict:
    """Define study equations (named parameters): {"speed": 3000, "freq": "speed/60*poles/2"}. Values are numbers or
    expression strings over other equation names. Use a name afterwards as a property value, e.g.
    jmag_set_properties(kind='condition', name='Rot', values={"AngularVelocity": "speed"}); the property that uses it
    fixes the unit (rpm, mm, A ...). descriptions={name: text} is optional. Equations named poles (or p) also let the
    checks derive the electrical frequency. Verify with jmag_get_equations. Blocks up to 120 s.
    """
    return await run_op("set_equations", 120, study=study, equations=equations, descriptions=descriptions, model=model)


@mcp.tool()
async def jmag_get_equations(study: str, model: str | None = None) -> dict:
    """List a study's equations: name, type, expression, evaluated value and description, plus the design-table
    parameter names. Blocks up to 60 s. Use it to confirm that names set with jmag_set_equations evaluate as intended.
    """
    return await run_op("get_equations", 60, study=study, model=model)


# =========================================================================== materials

@mcp.tool()
async def jmag_find_materials(query: str = "", limit: int = 40) -> dict:
    """Search JMAG's material library by name fragment, e.g. '35JN', 'N42', 'Copper', 'Air', 'SUS' (case-insensitive;
    fuzzy matches when nothing contains the text). Returns count, materials (up to `limit`, default 40) and
    library_size; an empty query lists the library from the start. Copy the exact name into jmag_set_material; for a
    material that is not in the library use jmag_create_material. Blocks up to 180 s.
    """
    return await run_op("find_materials", 180, query=query, limit=limit)


@mcp.tool()
async def jmag_create_material(name: str, bh: list | None = None, density: float | None = None,
                               conductivity: float | None = None, loss: dict | None = None, overwrite: bool = False,
                               kind: str = "steel", br: float | None = None, mu_r: float | None = None,
                               temperature: float | None = None, br_temp_coeff: float | None = None) -> dict:
    """Create a custom material in JMAG's 'Custom Materials' library, then assign it with jmag_set_material like
    any library grade (magnets also need orientation= there).

    kind="steel" (default; soft-magnetic lamination, e.g. measured or temperature-scaled):
      bh: [[H (A/m), B (T)], ...] strictly increasing in both; (0, 0) is prepended if missing. Extend it to
        >= 1e5 A/m - JMAG continues the last point with slope mu0, so a short table under-saturates.
      loss: iron-loss formula {"Kh", "Ke", "alpha", "beta"} (W/kg = Kh f B^alpha + Ke f^2 B^beta; beta defaults
        to 2). JMAG's unit convention for these constants is not verified by the bridge yet - cross-check a
        loss result against a hand calculation before relying on it.
    kind="magnet" (permanent magnet), either
      linear: br (T, at 20 degC) + mu_r (recoil, 1..1.3) -> JMAG "Linear(Br)" magnet (verified, JMAG 23.0).
        temperature (degC) + br_temp_coeff (1/K) apply Br(T) = br * (1 + coeff * (T - 20)); the applied Br is
        returned. Typical datasheet coefficients: NdFeB about -0.0012, SmCo about -0.0003, AlNiCo about -0.0002
        1/K - use the grade's own datasheet. mu_r and Hcj are not temperature-scaled, and irreversible
        demagnetisation is not modelled by the linear law (check DemagnetizationRatio or use a nonlinear table).
      nonlinear: bh = demagnetisation curve [[H (A/m), B (T)], ...], H from about -Hcb (negative) to >= 0,
        both strictly increasing -> JMAG "NonLinear(Reversible)" with the BhTable (table read back row-for-row;
        solver behaviour of this model not verified by the bridge yet).
    density: kg/m3. conductivity: S/m (0 for a laminated core with EddyCurrentCalculation off; magnets:
      sintered NdFeB about 6.7e5 S/m when magnet eddy loss matters).
    overwrite: replace an existing custom material of that name.
    Every value is read back; the response repeats the stored values and any validation warnings.
    """
    if kind not in ("steel", "magnet"):
        return _bad_input(ValueError(f"kind {kind!r} unknown"), "use kind='steel' or kind='magnet'")
    magnet = None
    try:
        if kind == "steel":
            if bh is None:
                raise materials.MaterialInputError("a steel needs bh", "bh = [[H A/m, B T], ...] from 0 to >= 1e5 A/m")
            if any(v is not None for v in (br, mu_r, temperature, br_temp_coeff)):
                raise materials.MaterialInputError("br / mu_r / temperature are magnet options",
                                                   "pass kind='magnet' for a permanent magnet")
            chk = materials.check_bh_table(bh)
            table = chk["table"]
            loss_v = materials.check_loss(loss)
        else:
            if loss:
                raise materials.MaterialInputError("iron-loss constants are for steels", "drop loss for a magnet")
            chk = materials.check_magnet(br, mu_r, temperature, br_temp_coeff, bh)
            table, loss_v = None, None
            magnet = ({"model": "linear", "br": chk["br"], "mu_r": chk["mu_r"]} if chk["kind"] == "linear"
                      else {"model": "nonlinear", "bh": chk["table"]})
    except materials.MaterialInputError as e:
        return {"ok": False, "error": {"message": str(e), "hint": e.hint}}
    out = await run_op("create_material", 120, name=name, bh=table, density=density, conductivity=conductivity,
                       loss=loss_v, overwrite=overwrite, kind=kind, magnet=magnet)
    if out.get("ok") and kind == "magnet":
        r = out.setdefault("result", {})
        if chk["kind"] == "linear":
            r.update({"br_20_T": chk["br_20"], "temperature_C": chk["temperature"], "br_temp_coeff": chk["br_temp_coeff"]})
        else:
            r.update({"br_from_table_T": chk["br"], "hcb_from_table_A_m": chk["hcb"]})
    if chk["warnings"]:
        out.setdefault("warnings", []).extend(chk["warnings"])
    return out


@mcp.tool()
async def jmag_set_material(study: str, parts: list, material: str, properties: dict | None = None,
                            orientation: dict | None = None, model: str | None = None) -> dict:
    """Assign a library material to parts (names, ids or [x, y] points), verified by read-back.
    properties: material settings, e.g. {"Laminated": 1, "LaminationFactor": 98 (percent), "EddyCurrentCalculation": 0}.
    orientation (magnets): {"pattern": "Radial", "outward": true} for radially magnetised arcs (alternate outward /
    inward per pole), or {"pattern": "Parallel", "direction": [x, y, z]}; optional "origin", "axis". The response lists
    the valid patterns. Verify the magnetisation afterwards with a contour image (jmag_field_plot or
    jmag_contour_image).
    """
    return await run_op("set_material", 300, study=study, parts=parts, material=material, properties=properties,
                        orientation=orientation, model=model)


# =========================================================================== conditions

@mcp.tool()
async def jmag_add_current(study: str, name: str, parts: list, turns: float, current: float, direction: str = "up",
                           replace: bool = False, model: str | None = None) -> dict:
    """Coil excitation for STATIC studies ('Current' condition): `turns` [-] and `current` [A, DC] give ampere-turns =
    turns * current through `parts`. direction 'up' = +z (out of the 2D plane), 'down' = -z; a coil's go and return
    sides need opposite directions. No circuit and no AC: for time-varying current use a Transient study with
    jmag_circuit_quick + jmag_add_fem_coil. 3D Current conditions need face selection (jmag-simulation
    references/static-3d-basics.md). replace=True overwrites a condition of that name.
    """
    return await run_op("add_current", 120, study=study, name=name, parts=parts, turns=turns, current=current,
                        direction=direction, replace=replace, model=model)


@mcp.tool()
async def jmag_add_fem_coil(study: str, name: str, component: str, sides: list[dict], turns: float | None = None,
                            resistance: float | None = None, replace: bool = False, model: str | None = None) -> dict:
    """FEM Coil condition linked to a circuit Coil component (Transient/Frequency studies).

    sides=[{"parts": ["U_plus"], "direction": "up"}, {"parts": ["U_minus"], "direction": "down"}]; parts of one side
    carry the same current direction. turns/resistance are written to the circuit component (phase totals).
    """
    return await run_op("add_fem_coil", 120, study=study, name=name, component=component, sides=sides, turns=turns,
                        resistance=resistance, replace=replace, model=model)


@mcp.tool()
async def jmag_add_fem_conductor(study: str, name: str, component: str, conductors: list[dict], replace: bool = False,
                                 model: str | None = None) -> dict:
    """FEM Conductor (solid bars with eddy currents, e.g. cage bars, hairpins) linked to a circuit FEMConductor component.

    conductors=[{"parts": ["Bar1"], "direction": "up"}, ...]. Their material must allow eddy currents.
    """
    return await run_op("add_fem_conductor", 120, study=study, name=name, component=component, conductors=conductors,
                        replace=replace, model=model)


@mcp.tool()
async def jmag_add_condition(study: str, condition_type: str, name: str, parts: list | None = None,
                             properties: dict | None = None, replace: bool = False, model: str | None = None) -> dict:
    """Any other condition: RotationMotion, Torque, Force, RotationPeriodicBoundary, SymmetryBoundary, EddyCurrent,
    Slide ... on `parts` (names, ids or [x, y] points).
    Units: RotationMotion AngularVelocity [rpm]; lengths mm; angles deg. Unknown property names are rejected with
    suggestions; list valid ones with jmag_get_properties(kind='condition', name=...).
    Returns result.condition (name, type, parts, link, properties), result.applied (as jmag_set_properties) and
    result.parts. replace=True overwrites a same-named condition; otherwise a name clash is an error.
    Examples: RotationMotion {"AngularVelocity": 3000} on the rotor parts; Torque on the rotor parts.
    """
    return await run_op("add_condition", 120, study=study, condition_type=condition_type, name=name, parts=parts,
                        properties=properties, replace=replace, model=model)


@mcp.tool()
async def jmag_list_conditions(study: str, model: str | None = None) -> dict:
    """Conditions of a study: name, type, assigned part ids, circuit link, sub-conditions and property values, plus the
    condition types the study accepts. Blocks up to 60 s. Use it to verify what a build step really created and to find
    the exact name for jmag_delete_condition or jmag_set_properties(kind='condition', name=...).
    """
    return await run_op("list_conditions", 60, study=study, model=model)


@mcp.tool()
async def jmag_delete_condition(study: str, name: str, model: str | None = None) -> dict:
    """Delete one condition by name (an unknown name is an error listing the existing ones). Refused while a solve
    runs. Deleting a FEM coil condition does not remove its circuit Coil component (unverified; check with
    jmag_circuit_summary). Blocks up to 60 s.
    """
    return await run_op("delete_condition", 60, study=study, name=name, model=model)


# =========================================================================== circuit

@mcp.tool()
async def jmag_circuit_quick(study: str, topology: str, coils: list[dict], source: dict, probe: bool = True,
                             model: str | None = None) -> dict:
    """Build a standard, correctly wired circuit in a Transient/Frequency study whose circuit is EMPTY.
    topology: current_source | voltage_source (one coil) | three_phase_current | three_phase_voltage (coils U, V, W,
    star, grounded neutral).
    coils=[{"name": "CoilU", "turns": 24, "resistance": 0.05}, ...]: turns are series turns PER PHASE (not per slot
    coil); resistance in ohm per phase.
    source={"type": "sin"|"constant", "amplitude": 10, "frequency": 50, "phase": 0}: amplitude is the PEAK value of
    the sine (A or V; convert rms first, jmag_units), frequency Hz, phase deg (PhaseU for 3-phase sources).
    probe=True (one-coil topologies only) adds a VoltageProbe VP_<coil> across the coil, which jmag_physics_check(kvl)
    picks up by itself; the 3-phase topologies get no probes.
    Next: jmag_add_fem_coil(component=<coil name>), then jmag_circuit_image.
    """
    return await run_op("circuit_quick", 120, study=study, topology=topology, coils=coils, source=source, probe=probe,
                        model=model)


@mcp.tool()
async def jmag_circuit_add(study: str, components: list[dict] | None = None, wires: list[list[int]] | None = None,
                           model: str | None = None) -> dict:
    """Free-form circuit editing on JMAG's integer grid.

    components=[{"type": "Coil"|"Resistor"|"CurrentSource"|"VoltageSource"|"Ground"|"VoltageProbe"|..., "name": "...",
    "x": 0, "y": 0, "properties": {...}, "function": {"type": "sin", "amplitude": 5, "frequency": 50}}]
    wires=[[x1, y1, x2, y2], ...] connecting terminal points. Terminal offsets at rotation 0: two-terminal parts (x-2,y),(x+2,y);
    1-terminal sources (x+2,y); 3-phase sources U(x+2,y+2) V(x+2,y) W(x+2,y-2); Ground (x,y+2); VoltageProbe (x,y-2).
    The response lists computed terminals. Always verify with jmag_circuit_image.
    """
    return await run_op("circuit_add", 120, study=study, components=components, wires=wires, model=model)


@mcp.tool()
async def jmag_circuit_summary(study: str, model: str | None = None) -> dict:
    """Circuit of a Transient/Frequency study: components (name, type, properties), wire count and the
    terminal-offset table used by jmag_circuit_add; {has_circuit: false} when the study has none (Static studies never
    do in JMAG 23). Blocks up to 60 s.
    """
    return await run_op("circuit_summary", 60, study=study, model=model)


@mcp.tool()
async def jmag_circuit_image(study: str, path: str | None = None, model: str | None = None) -> list:
    """Render the study circuit to a PNG for a visual wiring check; do it after every jmag_circuit_quick or
    jmag_circuit_add. path: output file (default: under the bridge outputs). Returns result.path and the image.
    Blocks up to 120 s.
    """
    return with_image(await run_op("circuit_image", 120, study=study, path=out_path("circuit", path), model=model))


# =========================================================================== mesh

@mcp.tool()
async def jmag_mesh(study: str, model: str | None = None) -> dict:
    """Generate the mesh now and report element and vertex counts; fails loudly on bad geometry. Does not run the solver.
    Refused while a solve runs. Look at it with jmag_model_image(show_mesh=true); refine locally with jmag_mesh_size.
    BLOCKING_TOOLS: blocks up to 3600 s (fine 3D meshes). There is no wait_seconds / background mode (unlike
    jmag_run); if your client's tool timeout is shorter, the call is cut off client-side while JMAG keeps meshing -
    wait, then check with jmag_summary before calling it again. A bridge-side timeout restarts the worker: call
    jmag_connect again.
    """
    return await run_op("mesh", 3600, study=study, model=model)


@mcp.tool()
async def jmag_mesh_size(study: str, parts: list, size: float, name: str | None = None, model: str | None = None) -> dict:
    """Local mesh size on parts: `size` is a length in the MODEL unit (mm), e.g. 0.3 for an air-gap band. Creates a mesh
    size condition named `name` (default 'size_<parts>'). Use it on the air gap, magnets and thin bridges, then judge
    the effect with a mesh-convergence comparison (jmag_compare of a refined copy), not one run. Blocks up to 120 s.
    """
    return await run_op("mesh_size", 120, study=study, parts=parts, size=size, name=name, model=model)


# =========================================================================== checks

async def _facts(study: str, model: str | None) -> dict:
    return await run_op("study_facts", 120, study=study, model=model)


async def _post(study: str, model: str | None, electrical_frequency_hz: float | None, run_env: dict | None = None,
                record: bool = True) -> dict:
    """Post-run health check. record=True marks the study's post-check as done in VERIFY (jmag_check post and a
    finished jmag_run_status show the verdict to the caller; jmag_make_report does not count)."""
    rep = await run_op("report", 120, study=study, model=model)
    info = dict(rep.get("result") or {})
    if run_env and run_env.get("result"):
        info.update({k: v for k, v in run_env["result"].items() if k in ("errors", "warnings", "has_result")})
    facts = (await _facts(study, model)).get("result") or {}
    derived = None
    if electrical_frequency_hz is None and str(facts.get("type", "")).startswith("Transient"):
        derived = health.derive_electrical_frequency(facts)
        electrical_frequency_hz = derived["hz"] if derived else None
    torque_cond = (any(c.get("type") in ("Torque", "Torque_Surface", "LorentzTorque") for c in facts.get("conditions", []))
                   if facts.get("conditions") is not None else None)
    conv = None
    if info.get("calc_folder"):
        conv = solver_monitor.convergence(info["calc_folder"], facts.get("nonlinear_max_iteration"))
    tables: dict = {}
    if info.get("has_result"):
        lr = await run_op("list_results", 120, study=study, model=model)
        for t in (lr.get("result") or {}).get("tables", []):
            r = await run_op("get_result", 300, study=study, table=t["index"], model=model)
            if r["ok"]:
                tables[t["title"]] = r["result"]
    findings = health.postflight(info, conv, tables, electrical_frequency_hz, (facts.get("step") or {}).get("Step"),
                                 has_torque_condition=torque_cond)
    if derived:
        findings.append({"level": "info", "code": "FE_DERIVED",
                         "message": f"electrical frequency {derived['hz']:.6g} Hz taken from {derived['source']}",
                         "fix": "pass electrical_frequency_hz explicitly if this is not the electrical frequency"})
    summary = {title: {col: _round(health.waveform_stats(v)) for col, v in tb["columns"].items()}
               for title, tb in tables.items()}
    verdict = health.summarize(findings)
    if record and info.get("has_result"):
        VERIFY.mark_post(study, model, verdict["verdict"])
    return {"health": verdict, "convergence": conv, "result_summary": summary,
            "calc_folder": info.get("calc_folder")}




@mcp.tool()
async def jmag_check(study: str, stage: str = "pre", electrical_frequency_hz: float | None = None,
                     model: str | None = None) -> dict:
    """Engineering health check.

    stage='pre': lint the setup (materials, stack length, excitation, circuit links/sources/ground, coil directions,
    turns, step resolution vs electrical_frequency_hz, mesh). stage='post': solver errors/warnings, nonlinear
    convergence per step, NaN/zero results, zero/flat torque, steady-state periodicity, waveform summaries.
    electrical_frequency_hz (= poles/2 * rpm/60): when omitted it is derived from the RotationMotion speed plus a
    'poles'/'p' study equation or a 3-phase source Frequency (finding FE_DERIVED); otherwise the step checks are
    skipped (finding STEP_CHECKS_SKIPPED) - pass it.
    ok=true means the check ran. The physics verdict is result.verdict: PASS / CHECK (warnings) / FAIL (errors),
    result.passed = no error finding, result.hint = the fixes to apply. Fix errors before trusting any number.
    (stage, verdict, counts, findings for 'pre' and health/convergence/result_summary for 'post' are also kept at
    the top level as before.)
    """
    t0 = time.time()
    if stage == "pre":
        f = await _facts(study, model)
        if not f["ok"]:
            return f
        summ = health.summarize(health.preflight(f["result"], electrical_frequency_hz))
        return {"ok": True, "stage": "pre", **summ, "result": {"stage": "pre", **sup.check_verdict(summ)},
                "seconds": round(time.time() - t0, 3)}
    if stage != "post":
        return wrap(ok=False, t0=t0, error={"message": f"unknown stage {stage!r}", "hint": "stage='pre' or 'post'"})
    post = await _post(study, model, electrical_frequency_hz)
    return {"ok": True, "stage": "post", **post, "result": {"stage": "post", **sup.check_verdict(post["health"])},
            "seconds": round(time.time() - t0, 3)}


# =========================================================================== run

def _finish_job(job: dict, resp: dict) -> None:
    job["done"] = True
    job["finished"] = time.time()
    job["response"] = envelope(resp)
    try:
        job["runner"].stop()
    except Exception:
        pass


@mcp.tool()
async def jmag_run(study: str, wait_seconds: float = 120, overwrite: bool = True, check_first: bool = True,
                   force: bool = False, electrical_frequency_hz: float | None = None, model: str | None = None) -> dict:
    """Solve a study. Runs in a separate runner process so the bridge stays responsive.

    - check_first: runs jmag_check(stage='pre') and refuses on errors unless force=true.
    - Waits up to wait_seconds; if still running returns state='running' -> poll jmag_run_status (progress, ETA,
      convergence) or stop with jmag_cancel_run.
    - On completion returns solver report + post-run health check.
    """
    B.need_install()
    if B.job and not B.job.get("done"):
        return {"ok": False, "error": {"message": f"already running {B.job['study']}", "hint": "jmag_run_status / jmag_cancel_run"}}
    pre = None
    if check_first:
        f = await _facts(study, model)
        if not f["ok"]:
            return f
        pre = health.summarize(health.preflight(f["result"], electrical_frequency_hz))
        if pre["counts"]["error"] and not force:
            return {"ok": False, "error": {"message": "pre-run check failed", "hint": "fix the errors or pass force=true"},
                    "precheck": pre}
        total_steps = (f["result"].get("step") or {}).get("Step")
    else:
        total_steps = None
    info = await run_op("app_info", 60)
    if not info["ok"]:
        return info
    pid = info["result"]["jmag_pid"]
    folder = (await run_op("calc_folder", 60, study=study, model=model)).get("result", {}).get("folder")
    runner = Worker(B.install, "runner")

    def work() -> dict:
        runner.start()
        c = runner.call("connect", 120, pid=pid, launch=False, visible=True)
        if not c.get("ok"):
            return c
        args = {"study": study, "overwrite": overwrite}
        if model:
            args["model"] = model
        return runner.call("run", 30 * 24 * 3600, **args)

    job = {"id": uuid.uuid4().hex[:8], "study": study, "model": model, "started": time.time(), "done": False,
           "runner": runner, "folder": folder, "total_steps": total_steps, "freq": electrical_frequency_hz, "precheck": pre,
           "pid": pid}
    VERIFY.invalidate(study, model)   # the study gets new results: earlier post/physics checks no longer apply
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, work)
    job["future"] = fut
    fut.add_done_callback(lambda f: _finish_job(job, f.result() if not f.exception() else
                                                {"ok": False, "error": {"message": repr(f.exception())}}))
    B.job = job
    B._journal("run", {"study": study, "model": model, "overwrite": overwrite}, None)
    await asyncio.wait({fut}, timeout=wait_seconds)
    await asyncio.sleep(0.05)
    return await jmag_run_status()


@mcp.tool()
async def jmag_run_status() -> dict:
    """Progress of the current/last solve: steps done / total, elapsed, ETA, nonlinear convergence, dialogs.
    When finished: the solver report plus the post-run health check.

    ok is true while the solve runs and when it finished with results; false when it failed, and also when JMAG's
    messages say the solver aborted (solver_aborted=true, e.g. "The solver module jam was aborted", "jam.exe failed to
    execute", "Size of point array is zero") even if the study still reports results: treat them as unusable.

    No run started by this bridge (after a server restart, or a solve started in the GUI): returns state='running'
    with owned=false when a solver process or a 'Run Analysis' window exists (ok=true), else ok=false with
    state='none'; both carry solver_processes, newest_monitor_age_s and stalled (see jmag_status).
    """
    job = B.job
    if not job:
        return await _unowned_run_status()
    if job.get("kind") == "cases":  # jmag_run_cases job (advanced section below)
        return await _run_cases_status(job)
    elapsed = (job.get("finished") or time.time()) - job["started"]
    st: dict = {"job": job["id"], "study": job["study"], "elapsed_s": round(elapsed, 1)}
    if not job.get("folder"):
        r = await run_op("calc_folder", 60, study=job["study"], model=job["model"])
        job["folder"] = (r.get("result") or {}).get("folder")
    if not job["done"]:
        st["ok"] = True
        st["state"] = "running"
        if job.get("folder"):
            pr = solver_monitor.progress(job["folder"])
            st.update(pr)
            tot = job.get("total_steps")
            if tot and pr.get("steps_done"):
                frac = min(1.0, pr["steps_done"] / float(tot))
                st["percent"] = round(100 * frac, 1)
                st["eta_s"] = round(elapsed * (1 - frac) / frac, 0) if frac > 0 else None
            st["convergence"] = solver_monitor.convergence(job["folder"])
        dlg = await asyncio.to_thread(winui.dialogs)
        for d in dlg:
            if d.get("kind") == "progress":
                st["jmag_progress_window"] = winui.parse_progress(d.get("text", ""))
        blocking = [d for d in dlg if d.get("kind") != "progress"]
        if blocking:
            st["dialogs"] = blocking
            st["hint"] = "JMAG shows a dialog - it may be waiting for input (see jmag_dialogs / jmag_screenshot)"
        return st
    resp = job["response"]
    st["state"] = "finished" if resp.get("ok") else "failed"
    st["run"] = resp
    if "post" not in job:
        job["post"] = await _post(job["study"], job["model"], job.get("freq"), resp)
    st["post"] = job["post"]
    log = solver_monitor.log_messages(job["folder"]) if job.get("folder") else []
    if log:
        st["solver_log_warnings"] = log[:20]
    aborted = health.solver_abort_messages(resp, log)
    st["solver_aborted"] = bool(aborted)
    st["ok"] = bool(resp.get("ok")) and not aborted
    if not st["ok"]:
        run_err = resp.get("error") or {}
        hint = ("no usable results: read run.error, post.health and solver_log_warnings, fix the model (often a "
                "material curve, the mesh or a condition), then jmag_run again")
        st["error"] = ({"message": f"the solver aborted: {aborted[0]}", "hint": hint} if aborted else
                       {"message": run_err.get("message") or "the run failed", "hint": run_err.get("hint") or hint})
    return st


async def _unowned_run_status() -> dict:
    """jmag_run_status when this bridge holds no job: report solves it does not own."""
    st: dict = {"owned": False, **await _solver_status()}
    windows = await asyncio.to_thread(winui.dialogs, "Run Analysis")
    st["run_analysis_window"] = bool(windows)
    if windows:
        st["jmag_progress_window"] = winui.parse_progress(windows[0].get("text", ""))
    if st["solver_processes"] or windows:
        st.update(ok=True, state="running",
                  hint="a solve is running that this bridge did not start (server restart or GUI run): wait and "
                       "poll jmag_run_status / jmag_status; stop it only with jmag_cancel_run"
                       + ("; stalled=true: check jmag_dialogs and jmag_screenshot" if st.get("stalled") else ""))
        return st
    st.update(ok=False, state="none", error={"message": "no run started in this session and no solver is running",
                                             "hint": "start one with jmag_run"})
    return st


async def _press_abort(steps: list[str], pid: int | None) -> None:
    """Abort in 'Run Analysis', then answer JMAG's 'Stop calculation' prompt (windows of `pid` only when given)."""
    r = await asyncio.to_thread(winui.click, "Abort", "Run Analysis", 3.0, pid=pid)
    if r.get("clicked"):
        steps.append("pressed Abort")
        for _ in range(10):
            a = await asyncio.to_thread(winui.click, "Yes, and open results", "Stop calculation", 20.0, pid=pid)
            if a.get("clicked"):
                steps.append("answered 'Yes, and open results'")
                break
            await asyncio.sleep(0.5)


async def _abort_protocol(folder: str | None, steps: list[str], pid: int | None = None) -> None:
    """The verified stop order: Abort in 'Run Analysis' -> answer 'Stop calculation' -> wait up to 10 s for the
    solver -> only then terminate the ExecSolver/jam tree working in `folder`. Appends what it did to steps."""
    await _press_abort(steps, pid)
    for _ in range(20):
        if not await asyncio.to_thread(solver_monitor.solver_processes, folder):
            break
        await asyncio.sleep(0.5)
    else:
        killed = await asyncio.to_thread(solver_monitor.kill_solver, folder)
        steps.append(f"terminated solver processes {killed}")


@mcp.tool()
async def jmag_cancel_run() -> dict:
    """Stop the running solve safely.

    Protocol (verified on JMAG 23): press Abort in the 'Run Analysis' window -> answer the 'Stop calculation' prompt
    -> if the solver process is still alive after 10 s, terminate the ExecSolver/jam tree -> release the runner.
    Killing the solver *before* answering JMAG's prompt can take Designer down, hence this order.
    Answering 'Yes, and open results' keeps the steps solved so far (JMAG-Designer 23.0, 2026-11-09: has_result
    true, run warning "The interruption key (Ctrl+C) is pressed"); jmag_run_status then reports
    solver_aborted=true / ok=false and the post-check RUN_INTERRUPTED: treat them as incomplete and re-run.
    No bridge-owned run (server restart, GUI-started solve) but a 'Run Analysis' window of the connected JMAG is
    open: the same protocol runs on that instance only. Verified on 23.0 (2026-11-09, 2D SPM, 4001-step run
    stopped at step ~800): Abort -> 'Yes, and open results' -> the solver exited by itself within the 10 s wait,
    Designer stayed alive, about 13 s in all. The kill fallback (exactly one calc folder of the connected project)
    was not needed there and is not verified live.
    """
    job = B.job
    if not job:
        return await _cancel_unowned()
    if job.get("done"):
        return {"ok": False, "error": {"message": "nothing is running",
                                       "hint": "the last run of this bridge has finished; jmag_run_status shows it"}}
    if job.get("kind") == "cases":  # stop starting new cases; kill target = current case folder
        _cases_cancel_prepare(job)
    steps: list[str] = []
    await _abort_protocol(job.get("folder"), steps, job.get("pid"))
    for _ in range(60):
        if job.get("done"):
            break
        await asyncio.sleep(0.5)
    if not job.get("done"):
        job["runner"].kill()
        steps.append("released the runner worker (Designer did not return from Run)")
        job["done"] = True
        job["finished"] = time.time()
        job["response"] = {"ok": False, "error": {"message": "cancelled by user"}}
    leftover = await asyncio.to_thread(winui.dialogs)
    return {"ok": True, "steps": steps, "dialogs_left": leftover,
            "hint": "if dialogs_left is not empty, inspect/close them with jmag_dialogs"}


async def _cancel_unowned() -> dict:
    """jmag_cancel_run without any bridge job (server restart, GUI-started solve; verified on 23.0 2026-11-09
    up to the kill fallback, which was not needed).

    Acts on the connected JMAG instance only: its pid (app_info, else the remembered pid) filters the windows, and
    the solver tree is killed only when exactly one solver calc folder lies in that instance's project
    (<project>.jfiles). Other JMAG instances' windows and solvers are never touched."""
    info = await run_op("app_info", 60)
    res = (info.get("result") or {}) if info.get("ok") else {}
    pid = res.get("jmag_pid") or load_state().get("jmag_pid")
    project = res.get("project_path")
    if not pid:
        return {"ok": False, "error": {"message": "no run of this bridge and no connected JMAG instance",
                                       "hint": "jmag_connect(pid=...) to the instance that is solving, then call "
                                               "jmag_cancel_run again"}}
    windows = await asyncio.to_thread(winui.dialogs, "Run Analysis", pid=pid)
    if not windows:
        return {"ok": False, "error": {"message": f"nothing is running in the connected JMAG (pid {pid}): no bridge "
                                                  "job and no 'Run Analysis' window",
                                       "hint": "jmag_status lists solver_processes; nothing to cancel"}}
    status = await _solver_status()
    every = status.get("calc_folders") or []
    folders = [f for f in every if sup.folder_in_project(f, project)]
    steps: list[str] = [f"no bridge-owned run: using the 'Run Analysis' window of pid {pid}"]
    if len(folders) == 1:
        await _abort_protocol(folders[0], steps, pid)
    else:  # never kill solvers that cannot be tied to this instance's project
        await _press_abort(steps, pid)
        steps.append(f"solver tree not killed: {len(folders)} calc folder(s) of project {project!r} among {every}; "
                     "check jmag_status.solver_processes")
    leftover = await asyncio.to_thread(winui.dialogs, "", pid=pid)
    return {"ok": True, "steps": steps, "dialogs_left": leftover, "owned": False,
            "hint": "confirm with jmag_status that solver_processes is empty; inspect dialogs_left with jmag_dialogs; "
                    "the study keeps only the steps solved before the stop"}


@mcp.tool()
async def jmag_cleanup() -> dict:
    """Kill orphaned JMAG helper processes (Geometry Editor 'modeller.exe' left behind by Designer instances that
    exited) and report solver processes. Safe: only touches helpers whose parent Designer no longer exists."""
    t0 = time.time()
    try:
        return wrap(await asyncio.to_thread(solver_monitor.cleanup_orphans), t0=t0)
    except (OSError, subprocess.SubprocessError) as e:
        return wrap(ok=False, t0=t0, error={"message": f"process listing failed: {e}",
                                             "hint": "run it again; if it keeps failing use Task Manager"})


@mcp.tool(annotations=READ_ONLY)
async def jmag_solver_report(study: str, model: str | None = None) -> dict:
    """Solver report of a study: errors, warnings (e.g. nonlinear non-convergence), element count and the calculation
    folder. Blocks up to 120 s. First stop when a run aborted or a result table is empty.
    """
    return await run_op("report", 120, study=study, model=model)


# =========================================================================== results

def _case_busy(case: int | None) -> dict | None:
    """Error envelope when a per-case read would switch the current case while a job runs.

    Per-case reads call SetCurrentCase in the main worker; a jmag_run_cases loop switches cases in the runner
    between solves, so the two can race (and a plain jmag_run is solving the current case)."""
    if case is None or not B.job or B.job.get("done"):
        return None
    return {"ok": False, "error": {
        "message": f"a solve is running ({B.job.get('study')}): per-case reads (case={case}) switch the current "
                   "case and could disturb it",
        "hint": "poll jmag_run_status until finished, then read case results; omit case to read the current case"}}


@mcp.tool()
async def jmag_list_results(study: str, case: int | None = None, model: str | None = None) -> dict:
    """Result tables of a solved study (name, unit, columns, rows): torque, flux linkage, currents, voltages, losses ...
    case: design-table case index (0-based, see jmag_case_table); default = current case."""
    busy = _case_busy(case)
    if busy:
        return busy
    return await run_op("list_results", 120, study=study, case=case, model=model)


@mcp.tool()
async def jmag_get_result(study: str, table: str, columns: list[str] | None = None, electrical_frequency_hz: float | None = None,
                          max_points: int = 120, case: int | None = None, model: str | None = None,
                          n_harmonics: int = 10) -> dict:
    """One result table with statistics (mean, rms, min, max, pk-pk, ripple) and, if electrical_frequency_hz is given,
    harmonics/THD over the last electrical period. table = display name ('Torque', 'Coil Flux-Linkage') or JMAG key
    ('FEMCoilFlux', 'LineCurrent', 'TerminalVoltage'). Series are downsampled to max_points (use jmag_export_results for all).
    last_period.harmonic_amplitudes: PEAK amplitudes, index k = order k of the electrical frequency ([0] = DC,
    [1] = fundamental), over the last full electrical period (endpoint excluded). n_harmonics: their count =
    orders 0 .. n_harmonics-1 (default 10 -> orders 0-9, max 64; also limited by samples per period / 2). A
    harmonic above the limit is not reported, not zero. thd_pct is always over orders 2..15 (whatever
    n_harmonics is; None for DC-dominant tables such as torque). periodicity compares the last two periods (steady flag).
    case: design-table case index (0-based) to read instead of the current case (refused while a solve runs).
    Loss-study tables have x_name 'frequency' (Hz).
    result.unverified lists the checks not yet run on this study's current results this session
    ("post-check" = jmag_check(stage='post'), "physics-check" = jmag_physics_check); [] when both ran.
    """
    busy = _case_busy(case)
    if busy:
        return busy
    if not isinstance(n_harmonics, int) or isinstance(n_harmonics, bool) or not 1 <= n_harmonics <= 64:
        return _bad_input(ValueError(f"n_harmonics must be an integer 1..64, got {n_harmonics!r}"),
                          "use the default 10 (orders 0-9) or up to 64")
    env = await run_op("get_result", 300, study=study, table=table, columns=columns, case=case, model=model)
    if not env["ok"]:
        return env
    r = env["result"]
    x = r["x"]
    stats = {}
    for col, ys in r["columns"].items():
        s = _round(health.waveform_stats(ys))
        if electrical_frequency_hz and r["x_name"] == "time" and len(x) > 4:
            px, py = health.last_period(x, ys, 1.0 / electrical_frequency_hz)
            h = health.harmonics(py, orders=max(THD_ORDERS, n_harmonics - 1))
            h_thd = health.harmonics(py, orders=THD_ORDERS)   # THD over orders 2..15, independent of n_harmonics
            if h:
                s["last_period"] = {"mean": sum(py) / len(py), "fundamental": h["fundamental"],
                                    "thd_pct": h_thd.get("thd_pct"),  # None for DC-dominant tables (torque)
                                    "harmonic_amplitudes": [float(f"{a:.4g}") for a in h["amplitudes"][:n_harmonics]]}
            p = health.periodicity(x, ys, 1.0 / electrical_frequency_hz)
            if p:
                s["periodicity"] = p
        stats[col] = s
    stride = max(1, len(x) // max_points)
    env["result"] = {"table": r["table"], "unit": r["unit"], "x_name": r["x_name"], "x_unit": r["x_unit"],
                     "rows": len(x), "stats": stats, "stride": stride, "x": x[::stride],
                     "series": {c: v[::stride] for c, v in r["columns"].items()},
                     "unverified": VERIFY.unverified(study, model, case)}
    return env


@mcp.tool()
async def jmag_export_results(study: str, out_dir: str, tables: list[str] | None = None, model: str | None = None) -> dict:
    """Export result tables to CSV, one file per table (x column plus data columns), into out_dir.
    tables: table names or titles as jmag_list_results shows them (default: all). Full resolution; use jmag_get_result
    for statistics and downsampled series. result.unverified lists the checks (post-check, physics-check) not yet run
    on these results this session. Blocks up to 900 s.
    """
    env = await run_op("export_results", 900, study=study, out_dir=out_dir, tables=tables, model=model)
    if env.get("ok") and isinstance(env.get("result"), dict):
        env["result"]["unverified"] = VERIFY.unverified(study, model)
    return env


@mcp.tool()
async def jmag_contour_image(study: str, result_type: str = "MagneticFluxDensity", component: str = "Absolute",
                             step: int | None = None, path: str | None = None, model: str | None = None) -> list:
    """Contour plot image (default |B| at the last step) with min/max values. The fastest sanity check of a field
    solution: saturation hot-spots, magnetization direction, flux paths, leakage."""
    return with_image(await run_op("contour_image", 300, study=study, path=out_path("contour", path),
                                   result_type=result_type, component=component, step=step, model=model))


# =========================================================================== fields outside JMAG

READ_ONLY_OPS |= {"field_export"}
_FIELD_CACHE: dict = {}      # (jplot, mtime, step, quantities) -> {"data", "path"}; the last few exports
_FIELD_CACHE_SIZE = 4
B_FAMILY = ("B", "Br", "Bt")
RENDER_ERRORS = (AttributeError, RuntimeError)
RENDER_HINT = ('the field renderer failed inside matplotlib; field plots need matplotlib >= 3.8: '
               'pip install -U "jmag-agent-suite[plots]"')






async def _load_field(study: str | None, step: int | None, quantities: list[str], case: int | None,
                      model: str | None, data_path: str | None = None) -> tuple[dict | None, dict, dict | None]:
    """Field data from a saved JSON (data_path) or exported from the study's result files. Returns (data, envelope,
    cache entry {"data", "path"} or None); data is None when the envelope is an error."""
    if data_path:
        try:
            data = await asyncio.to_thread(fielddata.load, data_path)
        except fielddata.FieldDataError as e:
            return None, _bad_input(e, e.hint or "pass the path jmag_field_data returned"), None
        missing = [q for q in quantities if q not in data.get("values", {})]
        if missing:
            return None, _bad_input(ValueError(f"{data_path} has no {missing}; it has {sorted(data.get('values', {}))}"),
                                    "export them with jmag_field_data(quantities=[...]) or drop them"), None
        return data, {"ok": True, "result": {"data_path": data_path}}, None
    if not study:
        return None, _bad_input(ValueError("pass study (export from JMAG) or data_path (a saved field-data JSON)"),
                                "e.g. study='Load' (needs a solved 2D study)"), None
    try:
        quantities = _field_quantities(quantities)
    except fielddata.FieldDataError as e:
        return None, _bad_input(e, e.hint or ""), None
    env = await run_op("field_export", 300, study=study, step=step, case=case, model=model,
                       out_dir=str(state_dir() / "outputs"))
    if not env["ok"]:
        return None, env, None
    r = env["result"]
    try:
        mtime = os.path.getmtime(r["jplot"])
    except OSError:
        mtime = None
    key = (r["jplot"], mtime, r["step"], tuple(sorted(quantities)))
    if key in _FIELD_CACHE:
        return _FIELD_CACHE[key]["data"], env, _FIELD_CACHE[key]
    meta = {"study": r["study"], "model": r["model"], "jplot": r["jplot"], "mesh": r["mesh"],
            "mesh_source": r["mesh_source"], "solved_steps": r["solved_steps"],
            "jmag_version": B.install.version if B.install else None}
    try:
        data = await asyncio.to_thread(fielddata.export, B.install.home if B.install else None, r["jplot"], r["mesh"],
                                       r["step"], quantities, "mm", None, case, meta, r.get("solved_steps"),
                                       r.get("times"))
    except fielddata.FieldDataError as e:
        return None, _bad_input(e, e.hint or ""), None
    entry = {"data": data, "path": None}
    _FIELD_CACHE[key] = entry
    while len(_FIELD_CACHE) > _FIELD_CACHE_SIZE:
        _FIELD_CACHE.pop(next(iter(_FIELD_CACHE)))
    return data, env, entry


def _save_field(data: dict, entry: dict | None, out: str | None = None) -> str:
    """Write the field JSON once per export: reuse the cached file unless an explicit `out` path is asked for."""
    if out is None and entry is not None and entry.get("path") and os.path.isfile(entry["path"]):
        return entry["path"]
    path = fielddata.save(data, out_path("field", out, ".json"))
    if entry is not None and (out is None or not entry.get("path")):
        entry["path"] = path
    return path




@mcp.tool(annotations=READ_ONLY)
async def jmag_field_data(study: str, step: int | None = None, quantities: list[str] | None = None,
                          out: str | None = None, case: int | None = None, model: str | None = None,
                          per_part: list[str] | None = None) -> dict:
    """Export a solved 2D study's field at one step as plain data (JSON file) for plots and numbers outside JMAG,
    plus per-part statistics. The arrays are written to the file, never returned in the envelope.

    Reads the study's own result file (Designer.jplot, via JMAG's JplotReader.dll) and the case folder's mesh
    (Designer_input.jcf or Designer.jcf, else WriteMeshJcf; always checked against the result file, part ids
    taken from it - see result.warnings) - no contour definitions, no GUI, ~0.5 s for a 28k-element motor.
    When the result file has no element B (seen for every run after the first in a project) B/Bx/By are the
    curl of Az per element, equal to JMAG's element B to 1e-8 T (quantities[...].source says so). Verified on
    JMAG-Designer 23.0 (12s/4p SPM, Transient2D with rotation): max |B| equals jmag_contour_image's max at the
    same step; node/element ids match JMAG's.
    step: 1-based (default the last step stored in the result file; result.available_steps gives count / first /
    last - output-step control can store only some steps). case: design-table case (0-based; unverified - tested on the
    current case only). quantities (default ["B", "Az"]):
      B (|B|, T, per element), Bx, By (T, per element), Az (vector potential, Wb/m, per node: flux lines),
      J (current density z, A/m^2, conductors only, verified = N*I/slot area);
      unverified meaning: M (|magnetisation vector|, A/m), mu_r_diff (differential relative permeability),
      PermeanceFactor (magnets); raw escape hatch "key:<jplot key>:<node|element>:<0|1|2|abs>" (KeyList.xml in
      the JMAG install lists the keys; a key the study did not output gives an error).
    out: JSON path (default: the bridge's outputs folder).
    Format: {"dimension": 2, "length_unit": "mm", "nodes": [[x, y]], "elements": [[i, j, k]] (0-based triangles,
    quads split), "element_part", "parts", "part_materials", "values": {name: [...]} (len nodes or len elements,
    null where undefined), "quantities": {name: {result_type, component, unit, location, verified}}, "step",
    "time", "case", "node_ids"/"element_ids" (JMAG ids), "source"}. Lengths in mm. JMAG's own air regions are
    named "Air_<id>" (material "Air"). Re-plot it with jmag_field_plot(data_path=...) without JMAG.
    per_part: quantities to summarise per part (default: B and J when exported; Az only when asked for).
    Returns path, sizes, parts, per_part {quantity: {part: {max, min, mean (area-weighted), area mm^2, at_max}}},
    max / at_max / part_at_max per summarised quantity.
    2D only. Refused while a solve runs.
    """
    qs = list(quantities) if quantities else ["B", "Az"]
    data, env, entry = await _load_field(study, step, qs, case, model)
    if data is None:
        return env
    try:
        path = await asyncio.to_thread(_save_field, data, entry, out)
        want = per_part if per_part is not None else [q for q in ("B", "J") if q in data["values"]]
        stats = {q: fielddata.part_stats(data, q) for q in want if q in data["values"]}
    except (OSError, fielddata.FieldDataError) as e:
        return _bad_input(e, getattr(e, "hint", None) or "check the out path")
    res = {"path": path, "bytes": os.path.getsize(path), **_field_summary(data),
           "quantities": data["quantities"], "slide": data.get("slide"),
           "mesh_source": (data.get("source") or {}).get("mesh_source"),
           "per_part": {q: _sig(s["per_part"]) for q, s in stats.items()},
           "max": {q: _sig(s.get("max")) for q, s in stats.items()},
           "at_max": {q: s.get("at_max") for q, s in stats.items()},
           "part_at_max": {q: s.get("part_at_max") for q, s in stats.items()}}
    if data.get("warnings"):
        res["warnings"] = data["warnings"]
    return {"ok": True, "result": res, **({"seconds": env["seconds"]} if env.get("seconds") is not None else {})}




@mcp.tool(annotations=READ_ONLY)
async def jmag_field_plot(study: str | None = None, step: int | None = None, quantity: str = "B",
                          flux_lines: bool = True, parts: list[str] | None = None, hide_air: bool = True,
                          show_mesh: bool = False, smooth: bool = True, kind: str = "field", out: str | None = None, cmap: str | None = None,
                          vmin: float | None = None, vmax: float | None = None, title: str | None = None,
                          data_path: str | None = None, case: int | None = None, model: str | None = None,
                          dpi: int | None = None) -> list:
    """Field contour plot rendered by the bridge from exported data (cleaner than jmag_contour_image: perceptual
    colour map, flux lines from Az, part outlines, scale bar), returned as an image plus the numbers drawn on it.

    kind: "field" (shaded quantity + flux lines) | "mesh" (elements and parts). quantity: any jmag_field_data
    quantity (B default; elemental values are drawn flat per element, nodal ones smooth). flux_lines: contour
    lines of Az. parts: only these part names (e.g. ["StatorCore"]); hide_air (default True, both kinds): drop
    parts whose material is Air (JMAG's air regions, the gap) and zoom to the rest - the statistics cover the
    drawn parts only; hide_air=False shows the full domain incl. the outer air region. smooth (default True):
    elemental values (B) are averaged onto the nodes of each part for smooth shading like JMAG's; max/min/mean
    and the max marker stay the element values (annotations "values": "element", "shading"); smooth=False
    draws them flat per element. cmap: matplotlib name
    (default magma); vmin/vmax clip the colour scale (compare runs on one scale).
    Source: study (+ step, case, model) exports from the solved study (see jmag_field_data), or data_path = a
    JSON written by jmag_field_data / a previous jmag_field_plot (no JMAG needed).
    Annotations: max, min, mean (area-weighted), at_max [x, y] mm, part_at_max, per_part, flux_lines, bbox.
    When the image came from JMAG, result.data_path is the saved JSON for re-plotting. dpi: image resolution
    (default 160, about 1150 px wide; 195 gives about 1400 px).
    Use jmag_contour_image when you need JMAG's own rendering or a 3D model.
    """
    if kind not in ("field", "mesh"):
        return with_image(_bad_input(ValueError(f"kind {kind!r} unknown"), "use kind='field' or kind='mesh'"))
    need = ([quantity] if kind == "field" else []) + (["Az"] if kind == "field" and flux_lines and quantity != "Az" else [])
    data, env, entry = await _load_field(study, step, need if not data_path else [q for q in need if q != "Az"],
                                         case, model, data_path)
    if data is None:
        return with_image(env)
    try:
        show = _visible_parts(data, parts, hide_air)
    except ValueError as e:
        return with_image(_bad_input(e, "pass part names from the list above (jmag_field_data lists parts)"))
    try:
        path = out_path(f"field_{kind}", out)
        if kind == "field":
            res = await asyncio.to_thread(plotting.field, data, path, quantity=quantity,
                                          flux_lines=flux_lines and "Az" in data["values"], parts=show,
                                          show_mesh=show_mesh, cmap=cmap, vmin=vmin, vmax=vmax, title=title,
                                          smooth=smooth, **({"dpi": int(dpi)} if dpi else {}))
        else:
            res = await asyncio.to_thread(plotting.mesh, data, path, title=title, parts=show,
                                          **({"dpi": int(dpi)} if dpi else {}))
    except plotting.PlottingUnavailable as e:
        return with_image(_bad_input(e, plotting.INSTALL_HINT))
    except (ValueError, TypeError, KeyError) as e:
        return with_image(_bad_input(e, "check quantity / parts against jmag_field_data's parts and quantities"))
    except RENDER_ERRORS as e:
        return with_image(_bad_input(e, RENDER_HINT))
    saved = data_path or await asyncio.to_thread(_save_field, data, entry)
    res["annotations"] = _sig(res.get("annotations"))
    res.update({"data_path": saved, **_field_summary(data)})
    if data.get("warnings"):
        res["warnings"] = data["warnings"]
    res.pop("parts", None)
    return with_image({"ok": True, "result": res})




@mcp.tool(annotations=READ_ONLY)
async def jmag_field_probe(study: str | None = None, step: int | None = None, quantity: str = "B",
                           circle: dict | None = None, segment: dict | None = None, pole_pairs: int | None = None,
                           method: str = "auto", out: str | None = None, data_path: str | None = None,
                           case: int | None = None, model: str | None = None, dpi: int | None = None) -> list:
    """Field values along a path, from exported data: the air-gap flux density waveform and its harmonics, or B
    along a tooth / yoke line. Returns a PNG (the requested quantity; on a circle with harmonics a second
    panel of harmonic bars) plus the numbers.

    Path (mm, model coordinates) - exactly one of:
      circle = {"r": 30.5, "n": 360, "cx": 0, "cy": 0, "start_deg": 0}: n samples over one revolution (endpoint
        excluded), x axis = angle [deg]. Put r mid-gap (between magnet/rotor surface and stator bore).
      segment = {"from": [x, y], "to": [x, y], "n": 100}: x axis = distance [mm].
    quantity: B (|B|), Br / Bt (radial / tangential about (cx, cy), from Bx, By - Br is the usual air-gap
    waveform), Bx, By, Az, J or any jmag_field_data quantity. For B on a circle the result also carries Br and Bt.
    method: auto/element = the element's own FE value (B is constant per first-order element, so this is the
    solution itself; a staircase at mesh resolution), nodal = linear interpolation of area-weighted nodal
    averages (smoother, slightly lower peaks). Nodal quantities (Az) are always interpolated linearly.
    pole_pairs (circle only): report electrical harmonics (order k*p), fundamental = order p, THD over k >= 2,
    and any non-multiple orders > 1 % (asymmetry / eccentricity / slotting). Without it the strongest order is
    taken as fundamental. Harmonics need a full circle inside the mesh (a symmetry-sector model gives None).
    Source: study (+ step, case, model) or data_path (saved JSON, no JMAG). dpi: image resolution (default 160).
    Verified on JMAG-Designer 23.0
    (12s/4p SPM) against the exported element values; JMAG's own probe was not compared.
    """
    if (circle is None) == (segment is None):
        return with_image(_bad_input(ValueError("pass exactly one of circle or segment"),
                                     "e.g. circle={'r': 30.5, 'n': 360} (mid air gap, mm)"))
    try:
        if circle is not None:
            cx, cy = float(circle.get("cx", 0.0)), float(circle.get("cy", 0.0))
            n = int(circle.get("n", 360))
            pts = fielddata.circle_points(float(circle["r"]), n, cx, cy, float(circle.get("start_deg", 0.0)))
            start = float(circle.get("start_deg", 0.0))
            x = [start + 360.0 * k / n for k in range(n)]
            x_label, center = "angle [deg]", (cx, cy)
        else:
            p0, p1 = [float(v) for v in segment["from"]], [float(v) for v in segment["to"]]
            n = int(segment.get("n", 100))
            pts = fielddata.segment_points(p0, p1, n)
            length = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
            x = [length * k / (n - 1) for k in range(n)]
            x_label, center = "distance [mm]", (float(segment.get("cx", 0.0)), float(segment.get("cy", 0.0)))
    except (KeyError, TypeError, ValueError, fielddata.FieldDataError) as e:
        return with_image(_bad_input(e, "circle={'r': mm, 'n': 360} or segment={'from': [x, y], 'to': [x, y], 'n': 100}"))
    series_names = [quantity] + (["Br", "Bt"] if quantity == "B" and circle is not None else [])
    need = ["B", "Bx", "By"] if quantity in B_FAMILY else [quantity]
    data, env, _ = await _load_field(study, step, need, case, model, data_path)
    if data is None:
        return with_image(env)
    try:
        probes = await asyncio.to_thread(fielddata.probe_many, data, series_names, pts, method, center)
    except fielddata.FieldDataError as e:
        return with_image(_bad_input(e, e.hint or "check quantity and method"))
    series = {q: p["values"] for q, p in probes.items()}
    first = probes[quantity]
    res: dict = {"points": n, "inside": first["inside"], "method": first["method"], "location": first["location"],
                 **_field_summary(data)}
    res.pop("parts", None)
    unit = ((data.get("quantities") or {}).get("B" if quantity in B_FAMILY else quantity) or {}).get("unit", "")
    res["unit"] = unit
    if first["inside"] < n:
        res["warning"] = (f"{n - first['inside']} of {n} points lie outside the mesh (None); for a sector model "
                          f"keep the path inside the modelled sector")
    if circle is not None:
        # |B| is rectified (even orders only, no sign): a spectrum of it misleads, so it is analysed via Br / Bt.
        res["harmonics"] = {q: _sig(_probe_harmonics(v, pole_pairs)) for q, v in series.items() if q != "B"}
        if quantity == "B":
            res["harmonics_note"] = "no spectrum of |B| (rectified magnitude); see Br (radial) and Bt (tangential)"
    bars, bars_label = None, ""
    h = (res.get("harmonics") or {}).get(quantity)
    if h and pole_pairs:
        bars, bars_label = h.get("electrical_harmonics"), f"electrical harmonic order (mechanical order / {pole_pairs})"
    elif h:
        amps = health.harmonics(series[quantity], orders=min(n // 2, 30)).get("amplitudes") or []
        bars, bars_label = {k: a for k, a in enumerate(amps) if k >= 1}, "harmonic order (per revolution)"
    where = f"r = {float(circle['r']):.4g} mm" if circle is not None else "segment"
    ttl = f"{quantity} along {where}, step {data.get('step')}"
    if h and h.get("fundamental") is not None:
        ttl += f" - fundamental {h['fundamental']:.4g} {unit}".rstrip()
        if h.get("thd_pct") is not None:
            ttl += f", THD {h['thd_pct']:.3g} %"
    path = out_path("field_probe", out)
    try:
        img = await asyncio.to_thread(plotting.probe, x, series[quantity], path, quantity, unit, ttl, x_label,
                                      bars, bars_label, **({"dpi": int(dpi)} if dpi else {}))
        res["path"] = img["path"]
        res["annotations"] = img["annotations"]
    except plotting.PlottingUnavailable as e:
        res["plot_error"] = f"{e} - {plotting.INSTALL_HINT}"
    except RENDER_ERRORS as e:
        res["plot_error"] = f"{type(e).__name__}: {e} - {RENDER_HINT}"
    res["x"] = _sig(x)
    res["values"] = {q: _sig(v) for q, v in series.items()}
    return with_image({"ok": True, "result": res})


@mcp.tool()
async def jmag_physics_check(study: str, check: str, coil: str | None = None, probe: str | None = None,
                             resistance: float | None = None, speed_rpm: float | None = None,
                             electrical_frequency_hz: float | None = None, model: str | None = None) -> dict:
    """Physics invariants that prove the model behaves like physics, not just that it ran.

    check='kvl' (transient): v_probe = R*i + dpsi/dt for a coil (coil=component, probe=VoltageProbe, resistance=ohm).
      Residual < 1e-3 proves wiring polarity, connectivity and the FEM-coil link.
    check='energy' (static Current conditions or FEM coils): W ~= 1/2 sum(i*psi) (exact for linear iron;
      a few % off when saturated).
    check='power_balance' (rotating machine, transient): mean(sum i*dpsi/dt) over the last electrical period
      = mean(torque)*omega (+ eddy losses). Proves torque, flux linkage, winding layout and motion agree.
    Omitted arguments are taken from the study (explicit ones always win; result.defaults_used says which):
      speed_rpm = RotationMotion AngularVelocity; electrical_frequency_hz from a 'poles'/'p' equation or the
      3-phase source Frequency (as jmag_check); coil = the only circuit Coil, probe = VP_<coil> (jmag_circuit_quick)
      or the only VoltageProbe, resistance = that Coil's Resistance. So jmag_physics_check(study, 'power_balance')
      is one call for a standard PMSM transient study.
    Envelope: ok=true means the check ran. result.passed / result.verdict (PASS | FAIL) say whether the physics held;
    a failed invariant is ok=true, passed=false with result.hint naming the likely causes.
    """
    t0 = time.time()
    if check not in ("kvl", "energy", "power_balance"):
        return wrap(ok=False, t0=t0, error={"message": f"unknown check {check!r}", "hint": "kvl | energy | power_balance"})
    defaults: dict = {}
    need_kvl = check == "kvl" and not (coil and probe and resistance is not None)
    need_pb = check == "power_balance" and not (speed_rpm and electrical_frequency_hz)
    if need_kvl or need_pb:
        f = await _facts(study, model)
        if f.get("ok"):
            vals, defaults = sup.physics_defaults(f["result"], check, coil, probe, resistance, speed_rpm,
                                                  electrical_frequency_hz)
            coil, probe, resistance = vals["coil"], vals["probe"], vals["resistance"]
            speed_rpm, electrical_frequency_hz = vals["speed_rpm"], vals["electrical_frequency_hz"]
    res = await _physics(study, check, coil, probe, resistance, speed_rpm, electrical_frequency_hz, model)
    if not res.get("ok"):
        return {**res, "seconds": round(time.time() - t0, 3)}
    body = sup.invariant_verdict(check, res["result"])
    if defaults:
        body["defaults_used"] = defaults
    VERIFY.mark_physics(study, model, {k: body[k] for k in ("check", "passed", "verdict") if k in body})
    return wrap(body, t0=t0)


async def _physics(study: str, check: str, coil: str | None, probe: str | None, resistance: float | None,
                   speed_rpm: float | None, electrical_frequency_hz: float | None, model: str | None) -> dict:
    """{ok, result: health.* invariant dict (with its own "ok" = physics held)} or an error envelope."""
    if check == "kvl":
        if not (coil and probe and resistance is not None):
            missing = [n for n, v in (("coil", coil), ("probe", probe), ("resistance", resistance)) if v is None]
            return {"ok": False, "error": {"message": f"kvl needs coil, probe and resistance; could not derive {missing}",
                                           "hint": "pass them: coil = circuit Coil name, probe = its VoltageProbe, "
                                                   "resistance = the Coil's Resistance [ohm] (jmag_circuit_summary)"}}
        cur = await run_op("get_result", 300, study=study, table="LineCurrent", columns=[coil], model=model)
        if not cur["ok"]:
            cur = await run_op("get_result", 300, study=study, table="Circuit Current", columns=[coil], model=model)
        vol = await run_op("get_result", 300, study=study, table="TerminalVoltage", columns=[probe], model=model)
        if not vol["ok"]:
            vol = await run_op("get_result", 300, study=study, table="Circuit Voltage", columns=[probe], model=model)
        psi = await run_op("get_result", 300, study=study, table="FEMCoilFlux", columns=[coil], model=model)
        if not psi["ok"]:
            psi = await run_op("get_result", 300, study=study, table="Coil Flux-Linkage", columns=[coil], model=model)
        for e in (cur, vol, psi):
            if not e["ok"]:
                return e
        res = health.kvl_residual(cur["result"]["x"], cur["result"]["columns"][coil], vol["result"]["columns"][probe],
                                  psi["result"]["columns"][coil], resistance)
        return {"ok": True, "result": {"coil": coil, "probe": probe, "resistance_ohm": resistance, **res}}
    if check == "energy":
        lr = await run_op("list_results", 120, study=study, model=model)
        if not lr["ok"]:
            return lr
        titles = {t["name"]: t for t in lr["result"]["tables"]}
        energy = await run_op("get_result", 120, study=study, table="Stored Energy", columns=["Total"], model=model)
        if not energy["ok"]:
            return {"ok": False, "error": {"message": "no 'Stored Energy' table",
                                           "hint": "enable output: jmag_set_properties(kind='study', values={'Energy': 1}) and re-run"}}
        W = energy["result"]["columns"]["Total"]
        if "Magnetic Flux of Current Condition" in titles:
            psi = (await run_op("get_result", 120, study=study, table="Magnetic Flux of Current Condition", model=model))["result"]
            cur = (await run_op("get_result", 120, study=study, table="Current of Current Condition", model=model))["result"]
            cols = list(psi["columns"])
            k = len(W) - 1
            res = health.inductance_consistency([psi["columns"][c][k] for c in cols], [cur["columns"][c][k] for c in cols], W[k])
            return {"ok": True, "result": {"conditions": cols, **res}}
        if "Coil Flux-Linkage" in titles:
            psi = (await run_op("get_result", 300, study=study, table="Coil Flux-Linkage", model=model))["result"]
            cur = (await run_op("get_result", 300, study=study, table="Circuit Current", model=model))["result"]
            cols = [c for c in psi["columns"] if c in cur["columns"]]
            k = max(range(len(W)), key=lambda j: abs(W[j]))
            res = health.inductance_consistency([psi["columns"][c][k] for c in cols], [cur["columns"][c][k] for c in cols], W[k])
            return {"ok": True, "result": {"coils": cols, "at_step_index": k, **res}}
        return {"ok": False, "error": {"message": "no coil flux tables found",
                                       "hint": "energy needs 'Magnetic Flux of Current Condition' (static Current "
                                               "conditions) or 'Coil Flux-Linkage' (FEM coils); jmag_list_results"}}
    if check == "power_balance":
        if not (speed_rpm and electrical_frequency_hz):
            return {"ok": False, "error": {
                "message": "power_balance needs speed_rpm and electrical_frequency_hz; "
                           f"could not derive {[n for n, v in (('speed_rpm', speed_rpm), ('electrical_frequency_hz', electrical_frequency_hz)) if not v]}",
                "hint": "pass speed_rpm=<rpm> and electrical_frequency_hz=<poles/2 * rpm/60>"}}
        cur = await run_op("get_result", 300, study=study, table="LineCurrent", model=model)
        psi = await run_op("get_result", 300, study=study, table="FEMCoilFlux", model=model)
        tq = await run_op("get_result", 300, study=study, table="Torque", model=model)
        for e in (cur, psi, tq):
            if not e["ok"]:
                return e
        coils = [c for c in psi["result"]["columns"] if c in cur["result"]["columns"]]
        tcol = list(tq["result"]["columns"])[0]
        res = health.power_balance(cur["result"]["x"], {c: cur["result"]["columns"][c] for c in coils},
                                   {c: psi["result"]["columns"][c] for c in coils}, tq["result"]["columns"][tcol],
                                   speed_rpm, 1.0 / electrical_frequency_hz)
        return {"ok": True, "result": {"coils": coils, "torque_column": tcol, "speed_rpm": speed_rpm,
                                       "electrical_frequency_hz": electrical_frequency_hz, **res}}
    return {"ok": False, "error": {"message": f"unknown check {check!r}", "hint": "kvl | energy | power_balance"}}


# =========================================================================== hand calcs, plots, reports

def _bad_input(e: Exception, hint: str) -> dict:
    return {"ok": False, "error": {"message": str(e), "hint": hint}}


@mcp.tool(annotations=READ_ONLY)
async def jmag_estimate(kind: str, params: dict | None = None) -> dict:
    """Analytic hand calculation to know the expected answer BEFORE trusting FEA (no JMAG needed).

    Returns {value, unit, formula, assumptions, expected_fea_tolerance_pct, details}. A correct FEA result should
    land within the tolerance; a larger gap means the model or the assumptions are wrong - find out which.
    Inputs: lengths in mm, areas mm^2, B in T, currents A (peak unless named *_rms), poles = number of poles (2p).
    kind='list' returns every kind with its parameters. Kinds and examples:
      airgap_flux_density {"Br": 1.2, "magnet_mm": 4, "airgap_mm": 1, "carter": 1.05}
      carter_factor {"slot_opening_mm": 2.5, "slot_pitch_mm": 12, "airgap_mm": 1}
      winding_factor {"slots": 12, "poles": 10, "layers": 2}
      emf_constant / torque_constant {"turns_per_phase": 80, "kw1": 0.933, "Bg": 0.9, "bore_mm": 60, "stack_mm": 50, "poles": 10}
      torque_dq {"poles": 8, "psi_m": 0.05, "iq": 50, "id": -20, "Ld": 2e-4, "Lq": 4e-4}
      magnetizing_inductance / dq_inductance {"turns_per_phase": 80, "kw1": 0.933, "bore_mm": 60, "stack_mm": 50,
          "poles": 10, "g_eff_mm": 4.8, "saliency": 1.0}
      gapped_core_inductance {"turns": 100, "core_area_mm2": 750, "gap_mm": 1, "iron_path_mm": 180, "core_dims_mm": [15, 50]}
      tooth_yoke_flux_density {"Bg": 0.9, "bore_mm": 60, "slots": 12, "poles": 10, "tooth_width_mm": 8, "yoke_height_mm": 6}
      slot_fill {"slot_area_mm2": 80, "conductors_per_slot": 40, "wire_diameter_mm": 0.9}
      copper_loss {"current_rms": 10, "turns_per_phase": 80, "conductor_area_mm2": 0.64, "stack_mm": 50,
          "end_winding_mm": 20, "temperature_C": 120}
      electrical_frequency {"rpm": 3000, "poles": 10}
      step_plan {"rpm": 3000, "poles": 10, "steps_per_period": 60, "periods": 2, "slots": 12}  -> jmag_set_step args
      iron_loss_steinmetz {"kh": 0.025, "ke": 4e-5, "B": 1.5, "f": 250, "mass_kg": 2.1}
      dq_to_phase {"id": -20, "iq": 50, "theta_e_deg": 0} -> phase currents U/V/W and a jmag_circuit_quick source
          {"type": "sin", "amplitude", "phase"}. Verified on 23.0 (12s/4p SPM): the 3-phase source gives
          i_U(t=0) = amplitude * sin(PhaseU), U-V-W sequence; id=0, iq=10 A at theta_e_deg = 90 (the no-load flux
          vector angle at t=0) gave PhaseU -90 and the maximum torque 1.774 Nm (1.536 / 1.536 Nm at +-30 deg).
          theta_e_deg = angle of the no-load flux-linkage vector at t=0: phase_to_dq of psi_abc(0) has q = 0 there.
      phase_to_dq {"abc": [ia, ib, ic], "theta_e_deg": 30} -> d, q (amplitude-invariant Park, d axis at theta_e
          from the phase-U axis; same transform as jmag_experiment's Ld/Lq)
    """
    if kind in ("list", "help"):
        return {"ok": True, "result": {**{k: estimate.signature(k) for k in estimate.KINDS},
                                       **{k: dict(v) for k, v in sup.DQ_KINDS.items()}}}
    try:
        if kind in sup.DQ_KINDS:
            return {"ok": True, "result": sup.dq_estimate(kind, params)}
        return {"ok": True, "result": estimate.run(kind, params)}
    except (ValueError, TypeError) as e:
        return _bad_input(e, "fix the parameter named in the message; jmag_estimate(kind='list') shows every kind's parameters")


@mcp.tool(annotations=READ_ONLY)
async def jmag_units(convert: str, value: float, from_unit: str, to_unit: str, poles: int | None = None,
                     base: float | None = None) -> dict:
    """Convert a value between engineering units (pure, no JMAG).

    convert: quantity - speed | length | flux_density | angle | area | per_unit | auto (detect from from_unit).
    Units: speed rpm, rad/s, rps (mechanical) and Hz, rad/s_e (electrical, need poles); length m, cm, mm, um, in;
    flux_density T, mT, G, kG; angle deg, rad (mechanical), deg_e, rad_e (electrical, need poles);
    area m2, cm2, mm2, kcmil, AWG; per_unit SI <-> pu with base=<base value>.
    Examples: (speed, 3000, 'rpm', 'Hz', poles=8) -> 200 Hz electrical; (area, 18, 'AWG', 'mm2') -> 0.823;
    (angle, 15, 'deg', 'deg_e', poles=8) -> 60.
    """
    try:
        res = units.convert(value, from_unit, to_unit, poles=poles, base=base)
        if convert not in ("auto", "") and convert != res["quantity"]:
            raise ValueError(f"{from_unit} is a {res['quantity']} unit, not {convert}")
        return {"ok": True, "result": res}
    except ValueError as e:
        return _bad_input(e, "check the unit spelling (see this tool's description) and pass poles for electrical units")


@mcp.tool(annotations=READ_ONLY)
async def jmag_winding_plan(slots: int, poles: int, phases: int = 3, layers: int = 2, coil_pitch: int | None = None,
                            turns: int | None = None, parallel_paths: int = 1) -> dict:
    """Winding layout from slot/pole/phase numbers (star of slots; pure, no JMAG).

    Checks feasibility (balanced: Q/(m*gcd(Q,p)) integer), then returns: slot_map (slot, layer a/b, phase, sign,
    direction up/down, suggested part name Slot<kk>_<phase><+|-> - double layer adds a/b: Slot01a_U+),
    coil_list, kw1 and winding factors per harmonic, MMF harmonic table (sub-harmonics show up as order < pp),
    symmetry sector (smallest model: periodic/anti-periodic), and labels_coils in the labels-JSON shape of the
    jmag-geometry skill ({"U": {"up": [...], "down": [...]}}) ready for jmag_add_fem_coil sides.
    turns = turns per coil -> series turns per phase for the circuit Coil component.
    coil_pitch defaults to round(Q/poles) (1 = tooth coils). Examples: 12s/10p double layer kw1=0.933;
    24s/4p full pitch kw1=0.966; 9s/8p kw1=0.945.
    """
    try:
        return {"ok": True, "result": winding.plan(slots, poles, phases, layers, coil_pitch, turns, parallel_paths)}
    except ValueError as e:
        return _bad_input(e, "pick a feasible combination (the message names the rule) or change layers/coil_pitch")


@mcp.tool()
async def jmag_plot(kind: str, data: dict | None = None, csv_path: str | None = None, study: str | None = None,
                    table: str | None = None, x: str | None = None, y: list[str] | None = None, out: str | None = None,
                    title: str | None = None, electrical_frequency_hz: float | None = None,
                    model: str | None = None) -> list:
    """PNG plot rendered by the server, returned as an image plus the plotted numbers (so you can check them
    without vision). Needs matplotlib: pip install "jmag-agent-suite[plots]".

    kind: waveform (lines vs time, mean/rms annotated) | fft (harmonic bars of one period; give
    electrical_frequency_hz to cut out the last electrical period) | sweep (x vs y with markers) |
    map (2D grid with contour lines, e.g. efficiency) | torque_speed (envelope + power, base speed annotated).
    Source - exactly one of:
      data = {"x": [...], "series": {"Torque": [...]}} (what jmag_get_result returns; "columns" also accepted)
             map: {"x": [...], "y": [...], "z": [[row per y]], "x_label", "y_label", "z_label"}
      csv_path = a CSV with a header row, first column = x (jmag_export_results files work)
      study + table = read a result table from JMAG directly (full resolution, e.g. table='Torque')
    x: use this series as the x axis (sweeps, torque_speed); y: series to plot (default all). out: PNG path.
    With study=..., result.unverified lists the checks not yet run on that study's results (see jmag_get_result).
    Field plots (kind field / mesh) of a solved study: use jmag_field_plot (study=... or data_path=... of a
    jmag_field_data JSON) - it forwards parts, flux_lines, show_mesh, cmap, vmin, vmax; here they only work with
    data = the field-data dict and default options (y=["B"] selects the quantity).
    """
    if sum(v is not None for v in (data, csv_path, study)) != 1:
        return with_image(_bad_input(ValueError("pass exactly one of data, csv_path or study(+table)"),
                                     "e.g. jmag_plot(kind='waveform', study='Load', table='Torque')"))
    if study is not None:
        if not table:
            return with_image(_bad_input(ValueError("study needs table"), "list tables with jmag_list_results"))
        cols = (list(y) + ([x] if x else [])) if y else None
        env = await run_op("get_result", 300, study=study, table=table, columns=cols, model=model)
        if not env["ok"]:
            return with_image(env)
        data = env["result"]
    elif csv_path is not None:
        try:
            data = plotting.load_csv(csv_path)
        except (OSError, ValueError) as e:
            return with_image(_bad_input(e, "the CSV needs a header row and numeric columns; first column = x"))
    path = out_path(f"plot_{kind}", out)
    try:
        res = await asyncio.to_thread(plotting.plot, kind, data or {}, path, x, y, title, electrical_frequency_hz)
    except plotting.PlottingUnavailable as e:
        return with_image(_bad_input(e, plotting.INSTALL_HINT))
    except (ValueError, TypeError, KeyError) as e:
        return with_image(_bad_input(e, "check kind, x/y series names and the data shape in this tool's description"))
    res["source"] = {"study": study, "table": table} if study else ({"csv": csv_path} if csv_path else "data")
    if study:
        res["unverified"] = VERIFY.unverified(study, model)
    return with_image({"ok": True, "result": res})




async def _report_study(study: str, model: str | None, f_e: float | None, include_images: bool) -> dict:
    s: dict = {"name": study, "images": []}
    facts = await _facts(study, model)
    if not facts["ok"]:
        s["error"] = (facts.get("error") or {}).get("message", "study_facts failed")
        return s
    f = facts["result"]
    s["setup"] = _setup_table(f)
    s["precheck"] = health.summarize(health.preflight(f, f_e))
    if f.get("has_result"):
        post = await _post(study, model, f_e, record=False)
        s["health"], s["convergence"], s["result_summary"] = post["health"], post["convergence"], post["result_summary"]
    if include_images:
        if f.get("has_result"):
            c = await run_op("contour_image", 300, study=study, path=out_path("contour", None), model=model)
            if c["ok"]:
                s["images"].append(c["result"]["path"])
        if (f.get("circuit") or {}).get("has_circuit"):
            c = await run_op("circuit_image", 120, study=study, path=out_path("circuit", None), model=model)
            if c["ok"]:
                s["images"].append(c["result"]["path"])
    return s


@mcp.tool()
async def jmag_make_report(title: str, studies: list[str], out_dir: str, include_images: bool = True,
                           notes: str | list[str] | None = None, electrical_frequency_hz: float | None = None,
                           invariants: dict | None = None, sweeps: list[dict] | None = None,
                           doubts: list[str] | None = None, model: str | None = None) -> dict:
    """Write a self-contained Markdown report folder (report.md + images/ + replay.py) for one or more studies.

    Per study: setup table (type, stack length, mesh, materials, conditions, circuit, steps), pre-run and
    post-run verdicts with EVERY error/warning finding (FAIL/CHECK blocks are never omitted), convergence,
    result statistics (each number names its table/column), contour and circuit images when include_images.
    invariants: {study: [jmag_physics_check results, e.g. {"check": "kvl", "passed": true, ...}]} - run the checks
    first; when a study is not in invariants, the jmag_physics_check results recorded this session are used.
    A study whose results had no jmag_check(stage='post') / jmag_physics_check this session gets an open doubt
    naming the missing check. sweeps: [{"title", "columns": [...], "rows": [[...]], "study": <name, optional>}].
    notes / doubts: free text; doubts are merged with automatic ones (unchecked studies, failed invariants).
    The reproduction appendix and replay.py come from this session's journal. out_dir: a folder outside the repo.
    """
    rows = []
    for st in studies:
        s = await _report_study(st, model, electrical_frequency_hz, include_images)
        s["invariants"] = (invariants or {}).get(st) or VERIFY.physics(st, model)
        s["unverified"] = VERIFY.unverified(st, model)
        s["sweeps"] = [w for w in (sweeps or []) if w.get("study") == st or (len(studies) == 1 and not w.get("study"))]
        rows.append(s)
    if include_images and rows:
        m = await run_op("model_image", 120, path=out_path("model", None), **({"model": model} if model else {}))
        if m["ok"]:
            rows[0]["images"].insert(0, m["result"]["path"])
    placed = {id(w) for s in rows for w in s.get("sweeps", [])}
    loose = [w for w in (sweeps or []) if id(w) not in placed]
    try:
        res = await asyncio.to_thread(reporting.build_report, out_dir, title, rows, notes, include_images,
                                      _journal_rows(True), B.install, doubts, loose)
    except OSError as e:
        return _bad_input(e, "choose a writable out_dir outside the repository")
    return {"ok": True, "result": res}


@mcp.tool(annotations=READ_ONLY)
async def jmag_compare(study_a: str, study_b: str, tables: list[str] | None = None, threshold_pct: float = 1.0,
                       model: str | None = None, model_b: str | None = None) -> dict:
    """Compare two studies (mesh refinement, design variant, before/after a fix): setup differences that could
    explain a result change (stack length, mesh, materials, condition/circuit properties, steps, equations,
    analysis-control properties) and result statistics side by side with deltas in %.

    tables: result tables to compare (default: all tables both studies have). threshold_pct: deltas at or above
    this are listed under results.significant. A < 1-2 % torque change after mesh refinement = mesh-converged.
    model_b: model of study_b when it differs from model.
    """
    side = {}
    for key, st, mdl in (("a", study_a, model), ("b", study_b, model_b or model)):
        f = await _facts(st, mdl)
        if not f["ok"]:
            return f
        target = {"study": st, "kind": "study", **({"model": mdl} if mdl else {})}
        p = await run_op("get_properties", 120, target=target)
        flat = compare.flatten_facts(f["result"])
        flat.update(compare.flatten_properties((p.get("result") or {}).get("properties", [])))
        side[key] = {"flat": flat, "has_result": f["result"].get("has_result"), "study": st, "model": mdl}
    out: dict = {"a": study_a, "b": study_b, "setup_diff": compare.diff_flat(side["a"]["flat"], side["b"]["flat"])}
    if not (side["a"]["has_result"] and side["b"]["has_result"]):
        out["results"] = None
        out["hint"] = "one or both studies have no results; run them (jmag_run) to compare numbers"
        return {"ok": True, "result": out}
    names: dict = {}
    for key in ("a", "b"):
        lr = await run_op("list_results", 120, study=side[key]["study"], model=side[key]["model"])
        names[key] = [t["name"] for t in (lr.get("result") or {}).get("tables", [])]
    common = [n for n in names["a"] if n in names["b"]]
    wanted = [t for t in (tables or common) if t in common]
    stats: dict = {"a": {}, "b": {}}
    for key in ("a", "b"):
        for t in wanted:
            r = await run_op("get_result", 300, study=side[key]["study"], table=t, model=side[key]["model"])
            if r["ok"]:
                stats[key][t] = {c: health.waveform_stats(v) for c, v in r["result"]["columns"].items()}
    res = compare.compare_results(stats["a"], stats["b"], threshold_pct)
    res["not_compared"] = sorted(set(names["a"]) ^ set(names["b"]))
    if tables:
        res["missing_requested"] = [t for t in tables if t not in common]
    out["results"] = res
    return {"ok": True, "result": out}


@mcp.tool(annotations=READ_ONLY)
async def jmag_inventory() -> dict:
    """Whole project tree: project path, models (dimension, part count) -> studies (type, solved, condition count
    and types, result-table count, calc folder). Flags unsolved studies and studies without conditions.
    Superset of jmag_summary; use it first in a project you did not build."""
    info = await run_op("app_info", 60)
    if not info["ok"]:
        return info
    r = info["result"]
    models = []
    for m in r.get("models", []):
        studies = []
        for st in m.get("studies", []):
            studies.append(await _inventory_study(st, m["name"]))
        models.append({"name": m["name"], "dimension": m.get("dimension"), "parts": m.get("parts"), "studies": studies})
    return {"ok": True, "result": {"project_path": r.get("project_path"), "project_name": r.get("project_name"),
                                   "version": r.get("version"), "models": models,
                                   "flags": compare.inventory_flags(models)}}


async def _inventory_study(st: dict, model: str) -> dict:
    row = {"name": st["name"], "type": st.get("type"), "solved": bool(st.get("has_result"))}
    lc = await run_op("list_conditions", 60, study=st["name"], model=model)
    if lc["ok"]:
        conds = lc["result"]["conditions"]
        row["conditions"] = len(conds)
        row["condition_types"] = sorted({c["type"] for c in conds})
    else:
        row["error"] = (lc.get("error") or {}).get("message")
    cf = await run_op("calc_folder", 60, study=st["name"], model=model)
    if cf["ok"]:
        row["calc_folder"] = cf["result"].get("folder")
        row["solved"] = row["solved"] or bool(cf["result"].get("has_result"))
    if row["solved"]:
        lr = await run_op("list_results", 120, study=st["name"], model=model)
        if lr["ok"]:
            row["result_tables"] = len(lr["result"]["tables"])
    return row


# =========================================================================== knowledge

_NO_DOCS = {"message": "JMAG-Designer installation not found, so its manual cannot be searched",
            "hint": "set JMAG_HOME to the JMAG-Designer folder; jmag_doctor diagnoses the install"}


@mcp.tool()
async def jmag_docs_search(query: str, limit: int = 6) -> dict:
    """Search the JMAG scripting manual installed on this machine (property names, flags, result types, API pages).
    limit: hits returned (default 6). result = {index, hits}; read a hit with jmag_docs_page. Confirm any property
    name on the live object with jmag_get_properties before relying on it.
    BLOCKING_TOOLS: the first call builds a local index (~60 s); later calls are fast.
    """
    t0 = time.time()
    if not B.install or B.docs is None:
        return wrap(ok=False, t0=t0, error=dict(_NO_DOCS))
    try:
        info = await asyncio.to_thread(B.docs.ensure)
        hits = await asyncio.to_thread(B.docs.search, query, limit)
    except (OSError, ValueError) as e:
        return wrap(ok=False, t0=t0, error={"message": f"manual index failed: {e}",
                                             "hint": "check the JMAG install's help folder; jmag_doctor"})
    return wrap({"index": info, "hits": hits}, t0=t0)


@mcp.tool()
async def jmag_docs_page(page: str, max_chars: int = 20000) -> dict:
    """Full text of one manual page returned by jmag_docs_search, e.g. 'scriptMagneticSettings.html'. max_chars
    truncates (default 20000). result = {page, text}; an unknown page gives ok=false.
    """
    t0 = time.time()
    if not B.install or B.docs is None:
        return wrap(ok=False, t0=t0, error=dict(_NO_DOCS))
    try:
        return wrap({"page": page, "text": await asyncio.to_thread(B.docs.page, page, max_chars)}, t0=t0)
    except KeyError:
        return wrap(ok=False, t0=t0, error={"message": f"page {page!r} not found",
                                             "hint": "use a 'file' value returned by jmag_docs_search"})


@mcp.tool()
async def jmag_api_lookup(query: str, limit: int = 15) -> dict:
    """Look up JMAG Python API methods in the installed jmag/designer.py: 'Study.CreateCondition', 'CreateWire', or
    keywords like 'export image'. limit: hits (default 15). result = {hits}. Use before writing jmag_exec_python code,
    and prefer the idioms in the jmag-scripting skill.
    """
    t0 = time.time()
    if not B.install or B.docs is None:
        return wrap(ok=False, t0=t0, error=dict(_NO_DOCS))
    try:
        return wrap({"hits": await asyncio.to_thread(B.docs.api_lookup, query, limit)}, t0=t0)
    except (OSError, ValueError) as e:
        return wrap(ok=False, t0=t0, error={"message": f"API lookup failed: {e}",
                                             "hint": "jmag_doctor checks the JMAG Python install"})


@mcp.tool()
async def jmag_exec_python(code: str, timeout_s: float = 600) -> dict:
    """Escape hatch for advanced features (PWM circuits, windings, parametric cases, optimisation ...).

    Runs Python 3.8 inside the worker with `app` (JMAG Application) in scope; set `result = ...` to return data,
    print() is captured. JMAG errors are still detected via the message log. Look up APIs with jmag_api_lookup first.
    BLOCKING_TOOLS: blocks up to timeout_s (default 600 s); keep timeout_s below your client's tool timeout.
    """
    return await run_op("exec_python", timeout_s, code=code)


# =========================================================================== meta

@mcp.tool()
async def jmag_self_test(keep_open: bool = False, work_dir: str | None = None) -> dict:
    """Known-answer test of the whole toolchain on this machine/license (~1-2 min, separate JMAG instance).

    Builds a gapped C-core inductor from an external DXF, solves a static and a transient (circuit) study, and checks
    inductance vs analytic, static vs transient consistency, KVL residual and flux density range.
    Run it after installing, after JMAG updates, or whenever results look suspicious.
    Envelope: ok=true means the test ran to the end; result.passed / result.verdict (PASS | FAIL) say whether every
    known-answer check held (result.checks lists them). ok=false: it could not run (result has the diagnostics).
    BLOCKING_TOOLS: blocks about 120 s (it starts its own JMAG instance).
    """
    from .selftest import run_self_test
    t0 = time.time()
    if not B.install:
        return wrap(ok=False, t0=t0, error={"message": "JMAG-Designer installation not found",
                                             "hint": "set JMAG_HOME; jmag_doctor diagnoses the install"})
    raw = dict(await asyncio.to_thread(run_self_test, B.install, keep_open, work_dir))
    passed = bool(raw.pop("ok", False))
    if raw.get("error"):
        err = {"message": f"self-test could not run: {raw['error']}",
               "hint": "read result.worker_log; jmag_doctor checks the install; close leftover JMAG dialogs"}
        return with_first_run_note(wrap(raw, ok=False, error=err, t0=t0))
    body = {**raw, "passed": passed, "verdict": "PASS" if passed else "FAIL"}
    if not passed:
        body["hint"] = "failed checks: " + ", ".join(c["check"] for c in raw.get("checks", []) if not c.get("pass"))
    return with_first_run_note(wrap(body, t0=t0))


@mcp.tool(annotations=READ_ONLY)
async def jmag_doctor() -> dict:
    """Diagnose the installation without touching JMAG (no session, never attaches to or launches Designer; ~5 s).

    Call it when the server answers but something underneath does not: jmag_status says JMAG was not found,
    jmag_connect fails with "worker failed to start" / "JMAG Python not found", plots fail for lack of matplotlib,
    or the user says the skills are missing - and always BEFORE telling the user to reinstall.
    Returns result {summary: "n pass / n warn / n fail", checks: [{name, status: PASS|WARN|FAIL, detail, fix}]};
    the envelope's ok is true whenever the checks ran, so judge the install by the FAIL count in summary.
    Checks: host python, mcp package, jmag install (JMAG_HOME), designer.exe, jmag python (JMAG_PYTHON), that python
    runs, PyJmag import under it, state dir writable, matplotlib, claude cli, skills in ~/.claude/skills, running
    designer.exe PIDs. Relay each non-PASS row's `fix` to the user verbatim; WARN rows are optional features.
    The same report is `python -m jmag_bridge --doctor` in a terminal.
    """
    from . import doctor
    checks = await asyncio.to_thread(doctor.run_checks, False)  # False: no `claude mcp list` from inside a session
    rep = doctor.report(checks)
    return {"ok": True, "result": {"summary": rep["summary"], "checks": rep["checks"]}}


def with_first_run_note(out: Any) -> Any:
    """Copy of a dict tool result plus `first_run_note` (user text of the one-time star offer) and `first_run_action`
    (what the agent must do with it) until the user has answered it."""
    prompt = star.first_run_prompt()
    if prompt is None or not isinstance(out, dict):
        return out
    return {**out, **prompt}


@mcp.tool()
async def jmag_star_repo(action: str) -> dict:
    """Act on the user's answer to the one-time `first_run_note` (offer to star the repo on GitHub).

    Call ONLY after the user explicitly answered that question - never on your own initiative, never as a guess.
    action='star' (user said yes): stars through the GitHub CLI (`gh`); if gh is missing, not logged in or fails,
      opens the repo page in the browser instead. action='open': opens the repo page only. action='dismiss' (no).
    Any answer is recorded in state.json and the note never appears again on this machine.
    Returns {ok, result: {method: gh | already_starred | browser | dismissed, message, url}, seconds}.
    """
    t0 = time.time()
    try:
        r = await asyncio.to_thread(star.star_repo, action)
    except ValueError as e:
        return wrap(ok=False, t0=t0, error={"message": str(e), "hint": "star | open | dismiss"})
    body = {k: v for k, v in r.items() if k != "ok"}
    if r.get("ok"):
        return wrap(body, t0=t0)
    return wrap(body, ok=False, t0=t0, error={"message": r.get("message") or "could not star or open the repository",
                                               "hint": f"tell the user the page: {r.get('url')}"})


def _journal_rows(session_only: bool = True) -> list[dict]:
    rows = []
    if B.journal.is_file():
        for line in B.journal.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not session_only or r.get("session") == B.session:
                rows.append(r)
    return rows


@mcp.tool()
async def jmag_journal(export_script: str | None = None, session_only: bool = True) -> dict:
    """Show the log of state-changing operations; export_script=path writes a replayable Python script
    (runs with JMAG's bundled Python) that reproduces the session - a reproducible record of the model build."""
    t0 = time.time()
    try:
        rows = _journal_rows(session_only)
        out: dict = {"entries": len(rows), "last": rows[-30:]}
        if export_script:
            from .replay import write_replay_script
            out["script"] = write_replay_script(rows, export_script, B.install)
    except OSError as e:
        return wrap(ok=False, t0=t0, error={"message": f"journal could not be read or written: {e}",
                                             "hint": "choose a writable export_script path"})
    return wrap(out, t0=t0)


# =========================================================================== advanced API: capabilities, design-table
# cases, loss study, efficiency map, winding, table export, case report, optimisation table, experiments.
# Status per call (VERIFIED / UNVERIFIED on JMAG-Designer 23.0) is in each docstring and in
# .claude/skills/jmag-simulation/references/advanced-api.md.

READ_ONLY_OPS |= {"capabilities", "case_table_get", "loss_results", "optimization_get"}


@mcp.tool()
async def jmag_capabilities(model: str | None = None) -> dict:
    """What this JMAG install, licence and API can do - call it before planning loss/map/optimisation work.

    Returns: JMAG version, worker Python, study types JMAG offers per model grouped by family (magnetic static/
    transient/frequency, thermal 'HeatSteadyPseudo2D'..., structural, electric field), API members present for
    parametric cases, loss study, efficiency map, optimisation, winding, table export, case report, batch jobs,
    and which solver executables exist in the install folder. No JMAG call that could log an error is made.
    Pitfall: JMAG 23 has no licence query API - a listed study type can still fail at Run if the licence lacks
    the solver module (the run report names it). VERIFIED on 23.0.
    """
    return with_first_run_note(await run_op("capabilities", 120, model=model))


@mcp.tool()
async def jmag_case_table(study: str, action: str = "get", add_parameters: list[str] | None = None,
                          cases: list[dict] | None = None, set_cases: list[dict] | None = None,
                          remove_cases: list[int] | None = None, active: dict | None = None,
                          current_case: int | None = None, candidates: str | None = None, max_cases: int = 200,
                          model: str | None = None) -> dict:
    """Parametric cases (JMAG design table) of a study. Cases are 0-based; case 0 is the base case.

    action='get': parameters (name, type, variable), cases with values/active/has_result, equations and their
      value per case. candidates='Motion' lists addable parameter names containing that text.
    action='set' (all optional, applied in this order, everything read back):
      add_parameters=['Motion: AngularVelocity', 'speed'] - full names look like
        'Motion (RotationMotion2D): AngularVelocity' or 'Source3Ph (3PhaseCurrentSource): Amplitude'; the short
        'Owner: Prop' form is accepted. Equations made with jmag_set_equations join the table by themselves as
        'Equation parameters: <name>' (drive geometry/conditions through equations for the cleanest sweeps).
      cases=[{'speed': 3000}, {'speed': 6000}] appends one case per dict (keys: parameter full/short/equation name).
      set_cases=[{'case': 0, 'values': {'speed': 1500}}], remove_cases=[2, 3], active={'1': false},
      current_case=1 (what the GUI and jmag_run solve).
    Unknown names are rejected with closest matches before anything changes. Then solve with jmag_run_cases and
    read case k with jmag_get_result(..., case=k). VERIFIED on 23.0 (equation and condition parameters).
    """
    if action == "get":
        return await run_op("case_table_get", 120, study=study, max_cases=max_cases, candidates=candidates, model=model)
    if action != "set":
        return {"ok": False, "error": {"message": f"unknown action {action!r}", "hint": "get | set"}}
    return await run_op("case_table_set", 300, study=study, add_parameters=add_parameters, cases=cases,
                        set_cases=set_cases, remove_cases=remove_cases, active=active, current_case=current_case,
                        model=model)


def _jobs_dir() -> Path:
    d = state_dir() / "jobs"
    d.mkdir(exist_ok=True)
    return d


def _read_progress(job: dict) -> dict:
    p = job.get("progress_file")
    if not p or not os.path.isfile(p):
        return {}
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _cases_cancel_prepare(job: dict) -> None:
    """Called by jmag_cancel_run: stop starting new cases and point the solver kill at the current case folder."""
    if job.get("cancel_file"):
        Path(job["cancel_file"]).touch()
    prog = _read_progress(job)
    if prog.get("folder"):
        job["folder"] = prog["folder"]


async def _run_cases_status(job: dict) -> dict:
    elapsed = (job.get("finished") or time.time()) - job["started"]
    st: dict = {"job": job["id"], "kind": "cases", "study": job["study"], "elapsed_s": round(elapsed, 1)}
    prog = _read_progress(job)
    sel = prog.get("cases") or job.get("cases")
    done = prog.get("done") or []
    if sel is not None:
        st["cases"] = sel
        st["cases_done"] = f"{len(done)}/{len(sel)}"
    st["done"] = [{k: r.get(k) for k in ("case", "state", "seconds", "errors") if k in r} for r in done]
    if not job["done"]:
        st["state"] = "running"
        st["current_case"] = prog.get("current_case")
        folder = prog.get("folder")
        if folder:
            job["folder"] = folder
            pr = solver_monitor.progress(folder)
            st["current_case_progress"] = pr
            tot = job.get("total_steps")
            if tot and pr.get("steps_done"):
                st["current_case_percent"] = round(100 * min(1.0, pr["steps_done"] / float(tot)), 1)
        dlg = await asyncio.to_thread(winui.dialogs)
        blocking = [d for d in dlg if d.get("kind") != "progress"]
        if blocking:
            st["dialogs"] = blocking
            st["hint"] = "JMAG shows a dialog - it may be waiting for input (see jmag_dialogs / jmag_screenshot)"
        st["ok"] = True
        return st
    resp = job["response"]
    st["ok"] = bool(resp.get("ok"))
    st["state"] = "finished" if resp.get("ok") else "failed"
    st["run"] = resp
    st["hint"] = ("read case k with jmag_list_results(study, case=k) / jmag_get_result(study, table, case=k); "
                  "jmag_check(stage='post') checks the current case only")
    return st


@mcp.tool()
async def jmag_run_cases(study: str, cases: list[int] | None = None, method: str = "loop", overwrite: bool = True,
                         wait_seconds: float = 60, model: str | None = None) -> dict:
    """Solve design-table cases (all, or cases=[0, 2]) in the separate runner process, like jmag_run.

    method='loop' (default, VERIFIED on 23.0): SetCurrentCase(k) + Run per case; jmag_run_status shows
      cases_done, the current case and its step progress; jmag_cancel_run aborts the current case and starts no
      new ones. method='all_cases' (UNVERIFIED): activates exactly these cases and calls Study.RunAllCases once
      (no per-case progress). method='optimization' (UNVERIFIED): Study.RunOptimization for the table set with
      jmag_optimization_table. Returns per-case state/seconds/solver errors when done.
    Budget: each case costs one full solve - estimate with one jmag_run first. Then read case k with
    jmag_get_result(study, table, case=k) or compare cases with jmag_compare.
    """
    B.need_install()
    if B.job and not B.job.get("done"):
        return {"ok": False, "error": {"message": f"already running {B.job['study']}", "hint": "jmag_run_status / jmag_cancel_run"}}
    info = await run_op("app_info", 60)
    if not info["ok"]:
        return info
    pid = info["result"]["jmag_pid"]
    facts = await _facts(study, model)
    if not facts["ok"]:
        return facts
    total_steps = (facts["result"].get("step") or {}).get("Step")
    jid = uuid.uuid4().hex[:8]
    progress = _jobs_dir() / f"{jid}.progress.json"
    cancel = _jobs_dir() / f"{jid}.cancel"
    runner = Worker(B.install, "runner")

    def work() -> dict:
        runner.start()
        c = runner.call("connect", 120, pid=pid, launch=False, visible=True)
        if not c.get("ok"):
            return c
        args: dict = {"study": study, "overwrite": overwrite, "method": method, "progress_file": str(progress),
                      "cancel_file": str(cancel)}
        if cases is not None:
            args["cases"] = cases
        if model:
            args["model"] = model
        return runner.call("run_cases", 30 * 24 * 3600, **args)

    job = {"id": jid, "kind": "cases", "study": study, "model": model, "started": time.time(), "done": False,
           "runner": runner, "folder": None, "total_steps": total_steps, "cases": cases,
           "progress_file": str(progress), "cancel_file": str(cancel), "pid": pid}
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, work)
    job["future"] = fut
    fut.add_done_callback(lambda f: _finish_job(job, f.result() if not f.exception() else
                                                {"ok": False, "error": {"message": repr(f.exception())}}))
    B.job = job
    VERIFY.invalidate(study, model)   # every case gets new results: earlier checks no longer apply
    B._journal("run_cases", {"study": study, "model": model, "cases": cases, "method": method,
                             "overwrite": overwrite}, None)
    await asyncio.wait({fut}, timeout=wait_seconds)
    await asyncio.sleep(0.05)
    return await _run_cases_status(job)


@mcp.tool()
async def jmag_loss_study(action: str = "create", study: str = "", name: str | None = None,
                          parts: list | None = None, frequency_hz: float | None = None,
                          reference_steps: list[int] | None = None, properties: dict | None = None,
                          study_properties: dict | None = None, replace: bool = False, run: bool = True,
                          case: int | None = None, tables: list[str] | None = None, include_spectrum: bool = False,
                          model: str | None = None) -> dict:
    """Iron-loss post-processing study (JMAG 'loss' study) from a solved Transient study. VERIFIED on 23.0.

    action='create': study=<solved Transient study>, name=<new loss study>, parts=[iron cores, e.g. 'StatorCore',
      'RotorCore'], frequency_hz=<electrical frequency> (REQUIRED in practice: with BasicFrequency 0 the solver
      aborts 'Frequency is 0[Hz]'), reference_steps=[first, last] = the last electrical period of the source study
      (e.g. [70, 129] for 60 steps/period over 2 periods + JMAG's motion step update). properties= any other
      Ironloss condition property (HysteresisLossCalcType, JouleLossCalcType, LaminationThickness, Cyclicity ...);
      study_properties= loss-study properties (IronIronLossDensity, IronHysteresisLossDensity, CsvOutputPath ...).
      The response lists all condition/study property names and the parts' materials. Library loss data is not
      readable through the API (protected materials); a core part with 0 W after the run means missing data.
      run=true solves it right away (seconds) and returns the loss summary.
      BLOCKING_TOOLS: with run=true it blocks until the loss solve ends (up to 3600 s; usually seconds).
    action='results': study=<loss study>: every table whose name contains 'Loss' (Iron Loss, Joule Loss,
      Hysteresis Loss and their '(Each Component)' variants) with the total [W] per part and overall.
      Row 0 (0 Hz) holds the total; rows 1.. are frequency components (x in Hz) - verified: Iron = Hysteresis +
      Joule on row 0, and Joule rows 1.. sum to row 0. include_spectrum=true adds the rows.
    """
    if action == "results":
        busy = _case_busy(case)
        if busy:
            return busy
        return await run_op("loss_results", 300, study=study, case=case, tables=tables,
                            include_spectrum=include_spectrum, model=model)
    if action != "create":
        return {"ok": False, "error": {"message": f"unknown action {action!r}", "hint": "create | results"}}
    if not name:
        return {"ok": False, "error": {"message": "name (of the new loss study) is required"}}
    env = await run_op("create_loss_study", 300, study=study, name=name, parts=parts, frequency_hz=frequency_hz,
                       reference_steps=reference_steps, properties=properties, study_properties=study_properties,
                       replace=replace, model=model)
    if not env["ok"] or not run:
        return env
    if not parts or frequency_hz is None:
        env["result"]["run"] = "skipped: pass parts and frequency_hz, then jmag_run(study=name)"
        return env
    r = await run_op("run", 3600, study=name, model=model)
    env["result"]["run"] = {k: (r.get("result") or {}).get(k) for k in ("has_result", "seconds", "errors", "warnings")}
    if not r["ok"]:
        env["ok"] = False
        env["error"] = r.get("error")
        return env
    res = await run_op("loss_results", 300, study=name, model=model)
    env["result"]["losses"] = res.get("result") if res["ok"] else res.get("error")
    return env


@mcp.tool()
async def jmag_efficiency_map(action: str = "create", study: str = "", name: str | None = None,
                              settings: dict | None = None, magnet_temperatures: dict | None = None,
                              out_dir: str | None = None, result_types: list[str] | None = None,
                              points: list[list[float]] | None = None, image: bool = False,
                              model: str | None = None) -> dict:
    """Efficiency-map definition and results (EfficiencyMapDefinition / EfficiencyMapPlot). UNVERIFIED on 23.0
    beyond the API signatures: JMAG evaluates maps in an efficiency-map (response) study, normally created with
    JMAG's Efficiency Map wizard; on an ordinary Transient study create may return an invalid object (then the
    error says so).

    action='create': name=<map>, settings={max_voltage (V, DC bus limit), max_current (A, line), voltage_limit_type
      ('peak'|'fundamental'), motor_control_type ('maximum_efficiency'|'basic'), control ('mtpa'|'mtpa+fw'|
      'unitypf'|'id=0'), motor_mode ('motor'|'generation'), torque_type ('shaft'|'electromagnetic'),
      speed_divisions, torque_divisions, mechanical_loss_factor, add_pwm_iron_loss, dc_bus_voltage,
      carrier_frequency (Hz), add_ac_loss, add_magnet_loss, extrapolation, cluster_points, loss_correction,
      coil_temperature_correction, magnet_temperature_correction, magnet_correction_base_temperature, table},
      magnet_temperatures={'initial': 20, 'end': 120, 'divisions': 3}. Every value is read back ('mismatch' flags).
    action='results': speed/torque ranges, result types; out_dir= exports CSV per result type (image=true also
      PNG); points=[[rpm, Nm], ...] queries values (e.g. 'Efficiency', 'TotalLoss', 'IronLoss', 'CopperLoss').
    """
    if action == "create":
        if not name:
            return {"ok": False, "error": {"message": "name is required"}}
        return await run_op("efficiency_map_create", 300, study=study, name=name, settings=settings,
                            magnet_temperatures=magnet_temperatures, model=model)
    if action == "results":
        return await run_op("efficiency_map_results", 900, study=study, name=name, out_dir=out_dir,
                            result_types=result_types, points=points, image=image, model=model)
    return {"ok": False, "error": {"message": f"unknown action {action!r}", "hint": "create | results"}}


@mcp.tool()
async def jmag_winding(study: str, coils: list[dict] | dict, mode: str = "fem_coil", name: str = "Phase",
                       components: dict | None = None, slot_parts: dict | None = None,
                       phase_turns: dict | None = None, slots: int | None = None, poles: int | None = None,
                       layers: int | None = None, winding_type: str = "ThreePhase",
                       reference_slot_parts: dict | None = None, replace: bool = False, force: bool = False,
                       model: str | None = None) -> dict:
    """Apply a winding layout (from jmag_winding_plan) to a Transient/Frequency study in one call.

    coils: jmag_winding_plan's slot_map ([{slot, layer, phase, sign, direction, part}, ...]) or labels_coils
      ({'U': {'up': [parts], 'down': [parts]}, ...}); entries without 'part' are resolved through
      slot_parts={'1': 'Slot01_U+'} or {'1/a': ...} (slot/layer).
    mode='fem_coil' (VERIFIED on 23.0 - it is jmag_add_fem_coil per phase): one FEMCoil condition '<name><phase>'
      per phase linked to circuit component components[phase] (default 'Coil<phase>', e.g. from jmag_circuit_quick),
      sides grouped by direction. phase_turns={'U': 80, ...} writes Turn = series turns per phase (use
      jmag_winding_plan's series_turns_per_phase); if omitted and coils carry 'turns', Turn = sum(turns)/2.
      A part may carry only one phase+direction (double layer: one part per layer).
    mode='jmag_winding' (CRASHED Designer 23.0 in the live test -> refused unless force=true): JMAG's winding definition - WindingRegion(slots, poles, reference-slot parts
      reference_slot_parts={'1': ['Slot01a_U+'], '2': ['Slot01b_U+']}) + CreateWinding(winding_type, name) with a
      manual coil table per phase built from coil_list ([{phase, go_slot, return_slot}]) or slot_map pairs.
    Read back: conditions with parts/directions/links (fem_coil) or the stored coil tables (jmag_winding).
    """
    return await run_op("winding_apply", 300, study=study, coils=coils, mode=mode, name=name, components=components,
                        slot_parts=slot_parts, phase_turns=phase_turns, slots=slots, poles=poles, layers=layers,
                        winding_type=winding_type, reference_slot_parts=reference_slot_parts, replace=replace,
                        force=force, model=model)


@mcp.tool()
async def jmag_export_table(study: str, path: str, kind: str = "all_tables", table: str | None = None,
                            result_type: str | None = None, component: str | None = None, steps: str | None = None,
                            axis: str = "Time", filetype: int = 0, case: int | None = None,
                            overwrite: bool = True, force: bool = False, model: str | None = None) -> dict:
    """Export results with JMAG's own table writers (format from the extension: .csv/.txt/.htm).

    kind='all_tables' (ResultTable.WriteAllTables, axis 'Time'|'Step'|'Angle'|'Distance'), 'all_cases'
      (WriteAllCaseTables: every design-table case in one file), 'table' (one table, table='Torque').
    kind='field' (Study.ExportTable with a TableDefinition: result_type='MagneticFluxDensity', component=
      'Absolute', steps='all' or '1,10,20', filetype 0=Designer/1=Studio), 'field_values'
      (ExportResultValuesTable), 'field_minmax' (ExportMinMaxTable).
    Status on 23.0: all_tables, all_cases, table VERIFIED (header rows: study title, '<table>: <unit> <Value>',
    then 'Time s',<columns>). field: UNVERIFIED - returns 'There is no selected item' (needs a GUI selection).
    field_values/field_minmax: a probe sequence with ExportResultValuesTable crashed Designer -> refused unless
    force=true (save the project first). See references/advanced-api.md.
    Prefer jmag_export_results for plain per-table CSVs written by the bridge itself.
    """
    busy = _case_busy(case)
    if busy:
        return busy
    return await run_op("export_table", 1800, study=study, path=str(Path(path).resolve()), kind=kind, table=table,
                        result_type=result_type, component=component, steps=steps, axis=axis, filetype=filetype,
                        case=case, overwrite=overwrite, force=force, model=model)


@mcp.tool()
async def jmag_case_report(study: str, path: str, case: int | None = None, model: str | None = None) -> dict:
    """JMAG's own case report (Study.WriteCaseReport) for the current case or case=k; path e.g. report.html.
    VERIFIED on 23.0: writes <name>.html plus a <name>_images/ folder (~2 s). jmag_make_report builds the
    bridge's own report with verdicts, invariants and plots."""
    return await run_op("write_case_report", 1800, study=study, path=str(Path(path).resolve()), case=case, model=model)


@mcp.tool()
async def jmag_optimization_table(study: str, action: str = "get", parameters: list[dict] | None = None,
                                  objectives: list[dict] | None = None, constraints: list[dict] | None = None,
                                  csv_output_path: str | None = None, immediate_csv_output: bool | None = None,
                                  stop_on_failed_case: bool | None = None, settings: dict | None = None,
                                  model: str | None = None) -> dict:
    """JMAG optimisation table of a study. action='get' VERIFIED on 23.0; 'set' and the optimisation run are
    UNVERIFIED (API signatures only; needs the optimiser licence).

    action='get': design-variable ranges, objectives, constraints, CSV/run settings.
    action='set': parameters=[{'name': <design-table parameter>, 'min': 1.0, 'max': 3.0}] (add it to the design
      table first with jmag_case_table), objectives=[{'name': 'T', 'expression': '<response name>',
      'type': 'maximize'|'minimize', 'weight': 1}], constraints=[{'name': 'ripple', 'expression': '...',
      'type': '<='|'>='|'==', 'value': 10}], csv_output_path (monitor progress without GUI),
      immediate_csv_output, stop_on_failed_case, settings={'create_initial_cases': 1} (OptimizationTable.SetParameter).
    Run: jmag_run_cases(study, method='optimization') calls Study.RunOptimization in the runner; status via
    jmag_run_status, progress also in the CSV. Objective expressions refer to response values defined in JMAG.
    """
    if action == "get":
        return await run_op("optimization_get", 120, study=study, model=model)
    if action != "set":
        return {"ok": False, "error": {"message": f"unknown action {action!r}", "hint": "get | set"}}
    return await run_op("optimization_set", 300, study=study, parameters=parameters, objectives=objectives,
                        constraints=constraints, csv_output_path=csv_output_path,
                        immediate_csv_output=immediate_csv_output, stop_on_failed_case=stop_on_failed_case,
                        settings=settings, model=model)


@mcp.tool()
async def jmag_experiment(kind: str, study: str, params: dict, action: str = "run", model: str | None = None) -> dict:
    """Standard machine tests in one call, on a duplicate of a working Transient study (or a Static study for
    inductance). Everything it changes is read back; the metric math is unit-tested.

    kind='cogging': zero current (current sources' Amplitude -> 0), rpm (default 60), exactly one cogging period
      360/lcm(Q, 2p) with steps_per_span (default 90) -> torque pk-pk, mean, dominant order per revolution.
      params: {slots, poles, rpm?, steps_per_span?}.
    kind='back_emf': zero current, one electrical period (periods?) at rpm -> per phase psi1, E1 peak/rms
      = omega_e * psi1, EMF THD from the flux-linkage spectrum (E_k = k omega psi_k), phase imbalance and shift,
      and the E = omega psi check against VP_<coil> probe voltages if present. params: {poles, rpm, periods?,
      steps_per_period?}.
    kind='load_point': current_peak [A], phase_u_deg (PhaseU of the 3-phase source; the current angle depends on
      the rotor position at t=0), rpm, periods (default 2), steps_per_period (60) -> torque mean/pk-pk/ripple over
      the last period + power balance (sum i dpsi/dt vs T omega).
    kind='inductance_static' (Static study with jmag_add_current conditions; runs blocking in the main worker):
      params {current_peak, conditions: [...]} -> L = sum(psi)/I; or {current_peak, phases: {'U': [conds], 'V':
      [...], 'W': [...]}, d_axis_elec_deg} -> three solves (0, d, q) -> Ld, Lq, psi_pm, saliency.
    Units: poles = number of poles (2p, e.g. 4 for 2 pole pairs). rpm defaults: cogging 60, back_emf and
    load_point 1000.
    Common params: duplicate (default true -> works on a copy named '<study>_<kind>'), new_study (another copy
    name), source/motion (component/condition names if ambiguous), wait_seconds (600),
    estimate={'kind': 'emf_constant', 'params': {...}} attaches the jmag_estimate expectation.
    Duplicate name: if '<study>_<kind>' already exists (an earlier experiment), the call fails with ok=false and the
    hint: pass new_study=..., jmag_delete_study it, or analyze it with duplicate=false, action='analyze'.
    Failures are never silent: a refused pre-check, a failed or aborted solve returns ok=false with the findings
    (result.precheck / result.run) and a hint naming the leftover duplicated study and jmag_delete_study.
    If the solve is still running after wait_seconds, ok=true with result.next = poll jmag_run_status, then
    action='analyze'. result.unverified lists the checks (post-check, physics-check) not run on the copy's results.
    BLOCKING_TOOLS: blocks up to wait_seconds (default 600 s) - keep it below your client's tool timeout.
    Status: all four kinds VERIFIED on 23.0 (12s/4p SPM: cogging dominant order 12/rev, back-EMF psi1 0.0586 Wb,
    load-point power balance 0.9 %, Ld = Lq = 0.37 mH); numbers in references/advanced-api.md.
    """
    from . import experiments

    async def call(op: str, timeout: float, **args: Any) -> dict:
        return await run_op(op, timeout, **args)

    async def solve(target: str, wait_s: float, f_e: float | None) -> dict:
        return await jmag_run(study=target, wait_seconds=wait_s, check_first=True, electrical_frequency_hz=f_e,
                              model=model)

    try:
        res = await experiments.run_experiment(kind, study, params, call, solve, model=model, action=action)
    except experiments.ExperimentFailed as e:
        out: dict = {"ok": False, "error": {"message": e.message, "hint": e.hint}}
        if e.data:
            out["result"] = e.data
        return out
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": {"message": str(e),
                                       "hint": "fix the params or the study setup named above, then call again"}}
    except KeyError as e:
        return {"ok": False, "error": {"message": f"missing param or result column {e}",
                                       "hint": "check params against the kind's required keys (see this tool's "
                                               "docstring) and the table columns (jmag_list_results)"}}
    except TypeError as e:
        return {"ok": False, "error": {"message": f"bad param type: {e}",
                                       "hint": "numbers must be numbers, conditions/phases lists or dicts as documented"}}
    if isinstance(res, dict) and res.get("study") and (res.get("metrics") or res.get("kind") == "inductance_static"):
        res["unverified"] = VERIFY.unverified(res["study"], model)
    return {"ok": True, "result": res}


# =========================================================================== operating manual (skills)

@mcp.tool(annotations=READ_ONLY)
async def jmag_guide(topic: str | None = None, reference: str | None = None) -> dict:
    """The jmag operating manual (the jmag-* skills): safety rules, feedback ladder, verified JMAG property
    names and the tool map. Pure, no JMAG needed.

    Call this first if your client did not load the jmag skills (i.e. you are not in Claude Code), and follow
    the safety rules it returns before driving JMAG.
    No args: index of all skills {name, description} plus the full text of the 'jmag' router skill (entry point:
      safety rules, which skill for which task, phase-by-phase tool map).
    topic='<skill name>' (e.g. 'jmag-simulation', 'jmag-troubleshooting'): that skill's text and its reference files.
    topic + reference='<file>' (e.g. topic='jmag-simulation', reference='properties.md'; '.md' optional): one file
      from the skill's references/ folder. reference without topic searches every skill for that file name.
    Skills tell you when to read a reference ("see references/x.md") - fetch it with this tool.
    """
    from . import guide

    try:
        names = guide.skill_names()
        if topic is None and reference is None:
            return {"ok": True, "result": {"skills": guide.list_skills(), "topic": guide.ROUTER,
                                           "text": guide.read_skill(guide.ROUTER),
                                           "references": guide.list_references(guide.ROUTER),
                                           "next": "jmag_guide(topic=<skill>) for the skill that fits the task"}}
        if topic is None:
            hits = [n for n in names if _guide_has_ref(guide, n, reference)]
            if len(hits) != 1:
                where = f"found in {hits}" if hits else "not found in any skill"
                return {"ok": False, "error": {"message": f"reference {reference!r} {where}",
                                               "hint": "pass topic=<skill> too; jmag_guide(topic=<skill>) lists "
                                                       "its references"}}
            topic = hits[0]
        if topic not in names:
            return {"ok": False, "error": {"message": f"unknown topic {topic!r}",
                                           "hint": "valid topics: " + ", ".join(names)}}
        refs = guide.list_references(topic)
        if reference is not None:
            try:
                text = guide.read_skill(topic, reference)
            except KeyError:
                return {"ok": False, "error": {"message": f"skill {topic!r} has no reference {reference!r}",
                                               "hint": "valid references: " + (", ".join(refs) or "(none)")}}
            return {"ok": True, "result": {"topic": topic, "reference": reference, "text": text}}
        return {"ok": True, "result": {"topic": topic, "text": guide.read_skill(topic), "references": refs}}
    except ValueError as e:
        return _bad_input(e, "use a plain skill name from jmag_guide() and a file name from its references list")
    except FileNotFoundError as e:
        return {"ok": False, "error": {"message": str(e), "hint": "reinstall jmag-agent-suite (pip install -U "
                                                                  "jmag-agent-suite) so the skills are packaged"}}


def _guide_has_ref(guide: Any, name: str, reference: str) -> bool:
    try:
        guide.read_skill(name, reference)
        return True
    except KeyError:
        return False


def _register_guide_resources() -> None:
    """jmag://guide (router skill) and jmag://guide/{topic}[/{reference}] for clients that read resources."""
    from . import guide

    def _read(topic: str, reference: str | None = None) -> str:
        try:
            return guide.read_skill(topic, reference)
        except KeyError:
            raise ValueError(f"unknown jmag guide topic/reference {topic!r}/{reference!r}; "
                             "valid topics: " + ", ".join(guide.skill_names())) from None

    @mcp.resource("jmag://guide", name="jmag-guide", mime_type="text/markdown",
                  description="jmag router skill: safety rules, skill index and tool map. Read first.")
    def _guide_router() -> str:
        return _read(guide.ROUTER)

    @mcp.resource("jmag://guide/{topic}", name="jmag-guide-topic", mime_type="text/markdown",
                  description="One jmag skill by name (e.g. jmag-simulation).")
    def _guide_topic(topic: str) -> str:
        return _read(topic)

    @mcp.resource("jmag://guide/{topic}/{reference}", name="jmag-guide-reference", mime_type="text/markdown",
                  description="One file from a jmag skill's references/ folder.")
    def _guide_reference(topic: str, reference: str) -> str:
        return _read(topic, reference)


try:  # resources are optional: an SDK without @mcp.resource (or templates) still serves the tools
    _register_guide_resources()
except Exception:  # noqa: BLE001
    pass


#: After the MCP client closes stdio the process must be gone within this many seconds (the MCP Python client kills
#: the whole process tree after 2 s; other clients may wait for ever).
SHUTDOWN_GRACE_S = 5.0


def _release_on_disconnect(exit_code: int = 0, grace: float = SHUTDOWN_GRACE_S) -> None:
    """The stdio session ended (client closed stdin, or the serve loop failed): make the process exit promptly.

    The serve loop itself returns at once, but asyncio then joins the default executor, and a thread still inside a
    tool call (Worker.call waits up to the op timeout: 600 s by default, 30 days for jmag_run) kept the process
    alive long after the client had gone. Release every JMAG worker (blocked calls raise, workers exit after their
    current op; nothing is killed), and arm a daemon timer that hard-exits if anything else still blocks.
    """
    abandon_all()
    t = threading.Timer(grace, os._exit, args=(exit_code,))
    t.daemon = True
    t.start()


async def _serve_stdio() -> None:
    code = 0
    try:
        await mcp.run_stdio_async()
    except BaseException:
        code = 1
        raise
    finally:
        _release_on_disconnect(code)


def main(argv: list[str] | None = None) -> None:
    """Console entry (`jmag-mcp`, `python -m jmag_bridge`): MCP server on stdio, or --doctor / --version."""
    import argparse
    import sys

    from . import __version__, doctor
    p = argparse.ArgumentParser(prog="jmag-mcp", description="JMAG Agent Suite MCP server (stdio).")
    p.add_argument("--doctor", action="store_true", help="check the installation and exit (0 = no FAIL)")
    p.add_argument("--json", action="store_true", help="with --doctor: print the report as JSON")
    p.add_argument("--version", action="version", version=f"jmag-agent-suite {__version__}")
    args, unknown = p.parse_known_args(argv)
    if args.doctor:
        sys.exit(doctor.cli(as_json=args.json))
    if args.json:
        p.error("--json only works together with --doctor")
    if unknown:
        print(f"jmag-agent-suite: ignoring unknown arguments {unknown}", file=sys.stderr)
    import anyio
    try:
        anyio.run(_serve_stdio)  # what mcp.run() does for stdio, plus _release_on_disconnect
    except Exception as e:  # Claude Code hides the traceback: leave a log and a pointer to --doctor
        doctor.record_startup_failure(e)
        raise


if __name__ == "__main__":
    main()
