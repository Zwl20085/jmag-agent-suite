"""JMAG MCP server: tools for driving JMAG-Designer with feedback at every step.

Every tool returns an envelope:

    {"ok": bool, "result": ..., "error": {"message", "hint"}, "jmag_messages": [...]}

``ok`` is False when the operation raised *or* JMAG logged an error while it ran
(JMAG rarely raises; it logs). Read ``hint`` - it usually names the fix.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

try:  # MCP Python SDK v2
    from mcp.server.mcpserver import Image, MCPServer as _Server
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP as _Server, Image  # type: ignore

from . import health, materials, solver_monitor, winui
from .config import find_jmag, load_state, save_state, state_dir
from .docs_index import DocsIndex
from .worker_client import Worker, WorkerError

INSTRUCTIONS = """\
Bridge to JMAG-Designer (electromagnetic FEA). Typical loop:
jmag_status -> jmag_connect -> jmag_new_project/jmag_open_project -> jmag_import_dxf (2D) or jmag_import_cad (3D STEP)
-> jmag_rename_parts -> jmag_create_study -> jmag_set_material -> stack length via jmag_set_properties(kind='study',
{'ModelThickness': mm}) -> excitation (static: jmag_add_current; transient: jmag_circuit_quick + jmag_add_fem_coil)
-> jmag_set_step -> jmag_check(stage='pre') -> jmag_run -> jmag_check(stage='post') -> jmag_get_result / jmag_contour_image.
Rules: never trust a step that returned ok=false; read error.hint and jmag_messages. Geometry is drawn outside JMAG
(DXF for 2D, STEP for 3D). If a call times out or JMAG seems stuck, call jmag_dialogs (modal dialog?) and jmag_screenshot.
Unknown property names: jmag_get_properties lists valid ones; jmag_docs_search searches the local JMAG manual.
"""

mcp = _Server("jmag", instructions=INSTRUCTIONS)

READ_ONLY_OPS = {"ping", "app_info", "list_parts", "get_properties", "get_equations", "find_materials", "list_conditions",
                 "circuit_summary", "calc_folder", "report", "list_results", "get_result", "study_facts"}


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
            return {"ok": False, "error": {"message": str(e)}}
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
    if resp.get("elapsed") is not None:
        out["seconds"] = resp["elapsed"]
    return out


async def run_op(op: str, timeout: float = 600, **args: Any) -> dict:
    return envelope(await B.call(op, timeout, **{k: v for k, v in args.items() if v is not None}))


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
    """Installation, worker and JMAG connection status, plus any modal dialogs JMAG is showing. Call this first."""
    st: dict = {"install": B.install.as_dict() if B.install else None, "state_dir": str(state_dir())}
    if not B.install:
        st["hint"] = "set JMAG_HOME to the JMAG-Designer folder (contains designer.exe and python3.8/)"
        return st
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
    return st


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
    """Close the connected JMAG-Designer (optionally saving the project)."""
    env = await run_op("quit_app", 120, save=save)
    if env["ok"]:
        save_state(jmag_pid=None)
    return env


@mcp.tool()
async def jmag_dialogs(click: str | None = None, title_filter: str | None = None) -> list | dict:
    """List modal dialogs JMAG is showing (title, text, buttons). Pass click='Cancel' (or any button label) to press it.

    JMAG blocks on dialogs (e.g. 'save project before creating geometry', license prompts). If a tool hangs or times out,
    check here first. Prefer Cancel/No unless you are sure what the dialog does.
    """
    if click:
        return await asyncio.to_thread(winui.click, click, title_filter or "")
    return await asyncio.to_thread(winui.dialogs, title_filter or "")


@mcp.tool()
async def jmag_screenshot(out_dir: str | None = None) -> list:
    """Capture all JMAG windows (main window, Geometry Editor, dialogs) as images, even if hidden behind others."""
    d = out_dir or str(state_dir() / "outputs" / f"screens_{time.strftime('%H%M%S')}")
    rows = await asyncio.to_thread(winui.screenshots, d)
    items: list = [json.dumps(rows, indent=1)]
    for r in rows[:4]:
        items.append(Image(path=r["path"]))
    return items


# =========================================================================== project

@mcp.tool()
async def jmag_new_project(path: str, overwrite: bool = False) -> dict:
    """Create a new project and save it at `path` (*.jproj). Required before DXF import (Geometry Editor needs a saved project)."""
    return await run_op("project_new", 120, path=path, overwrite=overwrite)


@mcp.tool()
async def jmag_open_project(path: str) -> dict:
    """Open an existing *.jproj. Work on a copy when experimenting with a valuable project."""
    return await run_op("project_open", 600, path=path)


@mcp.tool()
async def jmag_save_project(path: str | None = None) -> dict:
    """Save the project (Save As when `path` is given)."""
    return await run_op("project_save", 600, path=path)


@mcp.tool()
async def jmag_summary() -> dict:
    """Project overview: models (dimension, parts) and studies (type, results)."""
    return await run_op("app_info", 60)


# =========================================================================== geometry

@mcp.tool()
async def jmag_import_dxf(path: str, model_name: str | None = None) -> dict:
    """Import 2D geometry (DXF/SAT drawn by an external script): every closed loop becomes a region/part.

    Returns parts with area and centroid; rename them next (jmag_rename_parts). The project must be saved first.
    Lengths are interpreted in the model unit (mm by default).
    """
    return await run_op("import_dxf", 600, path=path, model_name=model_name)


@mcp.tool()
async def jmag_import_cad(path: str, model_name: str | None = None, healing: bool = True, remove_sheet: bool = True,
                          unit: str = "mm") -> dict:
    """Import 3D CAD (STEP/IGES/Parasolid/SAT) as a 3D model. Name solids in your CAD script to get readable part names."""
    return await run_op("import_cad", 1800, path=path, model_name=model_name, healing=healing,
                        remove_sheet=remove_sheet, unit=unit)


@mcp.tool()
async def jmag_list_parts(model: str | None = None) -> dict:
    """Parts of a model: id, name, area (2D) or volume (3D), centroid."""
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
    """Image of the current model (geometry, or mesh with show_mesh=true) for visual verification."""
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
    """Copy a study with all settings, without results (good for variants: mesh refinement, other operating point)."""
    return await run_op("duplicate_study", 300, study=study, new_name=new_name, model=model)


@mcp.tool()
async def jmag_delete_study(study: str, model: str | None = None) -> dict:
    """Delete a study and its results."""
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
    """Set properties with read-back verification. Same targets as jmag_get_properties.

    values: {"ModelThickness": 50} | {"Turn": "N_turns"} (equation name/expression) | {"X": {"value": 3, "unit": "mm"}}.
    Unknown names are rejected with closest matches. Examples: stack length -> kind='study', {'ModelThickness': 50};
    multi-core -> {'UseMultiCPU': 1, 'MultiCPU': 8}; nonlinear -> {'NonlinearMaxIteration': 50}.
    """
    target = {"study": study, "kind": kind, "name": name, "index": index, "model": model}
    return await run_op("set_properties", 120, target={k: v for k, v in target.items() if v is not None},
                        values=values, strict=strict)


@mcp.tool()
async def jmag_set_step(study: str, steps: int | None = None, time_step: float | None = None, end_time: float | None = None,
                        step_type: int | None = None, model: str | None = None) -> dict:
    """Transient step control. steps+time_step [s] (StepType 0) or steps+end_time [s] (StepType 1, uniform).

    Rule of thumb: >= 30-60 steps per electrical period; simulate enough periods to reach steady state.
    """
    return await run_op("set_step", 120, study=study, steps=steps, time_step=time_step, end_time=end_time,
                        step_type=step_type, model=model)


@mcp.tool()
async def jmag_set_equations(study: str, equations: dict, descriptions: dict | None = None, model: str | None = None) -> dict:
    """Define study parameters/equations, e.g. {"speed": 3000, "freq": "speed/60*poles/2"}. Use their names as property values."""
    return await run_op("set_equations", 120, study=study, equations=equations, descriptions=descriptions, model=model)


@mcp.tool()
async def jmag_get_equations(study: str, model: str | None = None) -> dict:
    """List study equations with expression and evaluated value."""
    return await run_op("get_equations", 60, study=study, model=model)


# =========================================================================== materials

@mcp.tool()
async def jmag_find_materials(query: str = "", limit: int = 40) -> dict:
    """Search JMAG's material library, e.g. '35JN', 'N42', 'Copper', 'Air', 'SUS'."""
    return await run_op("find_materials", 180, query=query, limit=limit)


@mcp.tool()
async def jmag_create_material(name: str, bh: list, density: float | None = None, conductivity: float | None = None,
                               loss: dict | None = None, overwrite: bool = False) -> dict:
    """Create a custom soft-magnetic material (e.g. a measured or temperature-scaled lamination) in JMAG's
    'Custom Materials' library, then assign it with jmag_set_material like any library grade.

    bh: [[H (A/m), B (T)], ...] strictly increasing in both; (0, 0) is prepended if missing. Extend it to
        >= 1e5 A/m - JMAG continues the last point with slope mu0, so a short table under-saturates.
    density: kg/m3. conductivity: S/m (0 for a laminated core with EddyCurrentCalculation off).
    loss: iron-loss formula {"Kh", "Ke", "alpha", "beta"} (W/kg = Kh f B^alpha + Ke f^2 B^beta; beta defaults
        to 2). JMAG's unit convention for these constants is not verified by the bridge yet - cross-check a
        loss result against a hand calculation before relying on it.
    overwrite: replace an existing custom material of that name.
    Every value is read back; the response repeats the stored table ends and any validation warnings.
    """
    try:
        chk = materials.check_bh_table(bh)
        loss_v = materials.check_loss(loss)
    except materials.MaterialInputError as e:
        return {"ok": False, "error": {"message": str(e), "hint": e.hint}}
    out = await run_op("create_material", 120, name=name, bh=chk["table"], density=density, conductivity=conductivity,
                       loss=loss_v, overwrite=overwrite)
    if chk["warnings"]:
        out.setdefault("warnings", []).extend(chk["warnings"])
    return out


@mcp.tool()
async def jmag_set_material(study: str, parts: list, material: str, properties: dict | None = None,
                            orientation: dict | None = None, model: str | None = None) -> dict:
    """Assign a library material to parts (names, ids or [x,y] points), verified by read-back.

    properties: material settings e.g. {"Laminated": 1, "LaminationFactor": 98, "EddyCurrentCalculation": 0}.
    orientation (magnets): {"pattern": "Radial", "outward": true} for radially magnetised arcs (alternate outward/inward
    per pole), or {"pattern": "Parallel", "direction": [x,y,z]}; also "origin", "axis". The response lists valid patterns.
    the response lists valid patterns. Verify magnetization afterwards with a contour image.
    """
    return await run_op("set_material", 300, study=study, parts=parts, material=material, properties=properties,
                        orientation=orientation, model=model)


# =========================================================================== conditions

@mcp.tool()
async def jmag_add_current(study: str, name: str, parts: list, turns: float, current: float, direction: str = "up",
                           replace: bool = False, model: str | None = None) -> dict:
    """Static-study coil excitation ('Current' condition): total ampere-turns = turns*current through the parts.

    direction 'up' = +z (out of the 2D plane), 'down' = -z. A coil's go and return sides need opposite directions.
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
    """Any other condition: RotationMotion, Torque, Force, RotationPeriodicBoundary, SymmetryBoundary, EddyCurrent, Slide ...

    Unknown property names are rejected with suggestions; list them with jmag_get_properties(kind='condition').
    Examples: RotationMotion {"AngularVelocity": 3000} (rpm) on rotor parts; Torque on rotor parts.
    """
    return await run_op("add_condition", 120, study=study, condition_type=condition_type, name=name, parts=parts,
                        properties=properties, replace=replace, model=model)


@mcp.tool()
async def jmag_list_conditions(study: str, model: str | None = None) -> dict:
    """Conditions of a study with parts, links, sub-conditions and properties."""
    return await run_op("list_conditions", 60, study=study, model=model)


@mcp.tool()
async def jmag_delete_condition(study: str, name: str, model: str | None = None) -> dict:
    """Delete a condition."""
    return await run_op("delete_condition", 60, study=study, name=name, model=model)


# =========================================================================== circuit

@mcp.tool()
async def jmag_circuit_quick(study: str, topology: str, coils: list[dict], source: dict, probe: bool = True,
                             model: str | None = None) -> dict:
    """Build a standard, correctly wired circuit.

    topology: current_source | voltage_source (one coil) | three_phase_current | three_phase_voltage (U,V,W star, grounded neutral).
    coils=[{"name": "CoilU", "turns": 24, "resistance": 0.05}, ...]
    source={"type": "sin"|"constant", "amplitude": 10, "frequency": 50, "phase": 0}  (A or V, Hz, deg)
    Then link each coil with jmag_add_fem_coil(component=<coil name>). Check with jmag_circuit_image.
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
    """Circuit components with properties, wire count and the terminal-offset table."""
    return await run_op("circuit_summary", 60, study=study, model=model)


@mcp.tool()
async def jmag_circuit_image(study: str, path: str | None = None, model: str | None = None) -> list:
    """Render the study circuit to an image to verify wiring visually."""
    return with_image(await run_op("circuit_image", 120, study=study, path=out_path("circuit", path), model=model))


# =========================================================================== mesh

@mcp.tool()
async def jmag_mesh(study: str, model: str | None = None) -> dict:
    """Generate the mesh now (fails loudly on bad geometry) and report element/vertex counts."""
    return await run_op("mesh", 3600, study=study, model=model)


@mcp.tool()
async def jmag_mesh_size(study: str, parts: list, size: float, name: str | None = None, model: str | None = None) -> dict:
    """Local mesh size (model length unit) on parts, e.g. air-gap bands, magnets, thin bridges."""
    return await run_op("mesh_size", 120, study=study, parts=parts, size=size, name=name, model=model)


# =========================================================================== checks

async def _facts(study: str, model: str | None) -> dict:
    return await run_op("study_facts", 120, study=study, model=model)


async def _post(study: str, model: str | None, electrical_frequency_hz: float | None, run_env: dict | None = None) -> dict:
    rep = await run_op("report", 120, study=study, model=model)
    info = dict(rep.get("result") or {})
    if run_env and run_env.get("result"):
        info.update({k: v for k, v in run_env["result"].items() if k in ("errors", "warnings", "has_result")})
    facts = (await _facts(study, model)).get("result") or {}
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
    findings = health.postflight(info, conv, tables, electrical_frequency_hz, (facts.get("step") or {}).get("Step"))
    summary = {title: {col: _round(health.waveform_stats(v)) for col, v in tb["columns"].items()}
               for title, tb in tables.items()}
    return {"health": health.summarize(findings), "convergence": conv, "result_summary": summary,
            "calc_folder": info.get("calc_folder")}


def _round(d: dict, n: int = 6) -> dict:
    return {k: (float(f"{v:.{n}g}") if isinstance(v, float) else v) for k, v in d.items()}


@mcp.tool()
async def jmag_check(study: str, stage: str = "pre", electrical_frequency_hz: float | None = None,
                     model: str | None = None) -> dict:
    """Engineering health check.

    stage='pre': lint the setup (materials, stack length, excitation, circuit links/sources/ground, coil directions,
    turns, step resolution vs electrical_frequency_hz, mesh). stage='post': solver errors/warnings, nonlinear
    convergence per step, NaN/zero results, steady-state periodicity, waveform summaries.
    Verdict PASS / CHECK (warnings) / FAIL (errors). Fix errors before trusting any number.
    """
    if stage == "pre":
        f = await _facts(study, model)
        if not f["ok"]:
            return f
        findings = health.preflight(f["result"], electrical_frequency_hz)
        return {"ok": True, "stage": "pre", **health.summarize(findings)}
    return {"ok": True, "stage": "post", **await _post(study, model, electrical_frequency_hz)}


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
           "runner": runner, "folder": folder, "total_steps": total_steps, "freq": electrical_frequency_hz, "precheck": pre}
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
    When finished: the solver report plus the post-run health check."""
    job = B.job
    if not job:
        return {"ok": False, "error": {"message": "no run started in this session"}}
    elapsed = (job.get("finished") or time.time()) - job["started"]
    st: dict = {"job": job["id"], "study": job["study"], "elapsed_s": round(elapsed, 1)}
    if not job.get("folder"):
        r = await run_op("calc_folder", 60, study=job["study"], model=job["model"])
        job["folder"] = (r.get("result") or {}).get("folder")
    if not job["done"]:
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
    if job.get("folder"):
        st["solver_log_warnings"] = solver_monitor.log_messages(job["folder"])[:20]
    return st


@mcp.tool()
async def jmag_cancel_run() -> dict:
    """Stop the running solve safely.

    Protocol (verified on JMAG 23): press Abort in the 'Run Analysis' window -> answer the 'Stop calculation' prompt
    -> if the solver process is still alive after 10 s, terminate the ExecSolver/jam tree -> release the runner.
    Killing the solver *before* answering JMAG's prompt can take Designer down, hence this order.
    The study is left without results; re-run it after fixing whatever made you cancel.
    """
    job = B.job
    if not job or job.get("done"):
        return {"ok": False, "error": {"message": "nothing is running"}}
    steps: list[str] = []
    r = await asyncio.to_thread(winui.click, "Abort", "Run Analysis", 3.0)
    if r.get("clicked"):
        steps.append("pressed Abort")
        for _ in range(10):
            a = await asyncio.to_thread(winui.click, "Yes, and open results", "Stop calculation", 20.0)
            if a.get("clicked"):
                steps.append("answered 'Yes, and open results'")
                break
            await asyncio.sleep(0.5)
    folder = job.get("folder")
    for _ in range(20):
        if not await asyncio.to_thread(solver_monitor.solver_processes, folder):
            break
        await asyncio.sleep(0.5)
    else:
        killed = await asyncio.to_thread(solver_monitor.kill_solver, folder)
        steps.append(f"terminated solver processes {killed}")
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


@mcp.tool()
async def jmag_cleanup() -> dict:
    """Kill orphaned JMAG helper processes (Geometry Editor 'modeller.exe' left behind by Designer instances that
    exited) and report solver processes. Safe: only touches helpers whose parent Designer no longer exists."""
    return await asyncio.to_thread(solver_monitor.cleanup_orphans)


@mcp.tool()
async def jmag_report(study: str, model: str | None = None) -> dict:
    """Solver report of a study: errors, warnings (e.g. nonlinear non-convergence), element count, calc folder."""
    return await run_op("report", 120, study=study, model=model)


# =========================================================================== results

@mcp.tool()
async def jmag_list_results(study: str, model: str | None = None) -> dict:
    """Result tables of a solved study (name, unit, columns, rows): torque, flux linkage, currents, voltages, losses ..."""
    return await run_op("list_results", 120, study=study, model=model)


@mcp.tool()
async def jmag_get_result(study: str, table: str, columns: list[str] | None = None, electrical_frequency_hz: float | None = None,
                          max_points: int = 120, model: str | None = None) -> dict:
    """One result table with statistics (mean, rms, min, max, pk-pk, ripple) and, if electrical_frequency_hz is given,
    harmonics/THD over the last electrical period. table = display name ('Torque', 'Coil Flux-Linkage') or JMAG key
    ('FEMCoilFlux', 'LineCurrent', 'TerminalVoltage'). Series are downsampled to max_points (use jmag_export_results for all).
    """
    env = await run_op("get_result", 300, study=study, table=table, columns=columns, model=model)
    if not env["ok"]:
        return env
    r = env["result"]
    x = r["x"]
    stats = {}
    for col, ys in r["columns"].items():
        s = _round(health.waveform_stats(ys))
        if electrical_frequency_hz and r["x_name"] == "time" and len(x) > 4:
            px, py = health.last_period(x, ys, 1.0 / electrical_frequency_hz)
            h = health.harmonics(py)
            if h:
                s["last_period"] = {"mean": sum(py) / len(py), "fundamental": h["fundamental"], "thd_pct": h["thd_pct"],
                                    "harmonic_amplitudes": [float(f"{a:.4g}") for a in h["amplitudes"][:10]]}
            p = health.periodicity(x, ys, 1.0 / electrical_frequency_hz)
            if p:
                s["periodicity"] = p
        stats[col] = s
    stride = max(1, len(x) // max_points)
    env["result"] = {"table": r["table"], "unit": r["unit"], "x_name": r["x_name"], "x_unit": r["x_unit"],
                     "rows": len(x), "stats": stats, "stride": stride, "x": x[::stride],
                     "series": {c: v[::stride] for c, v in r["columns"].items()}}
    return env


@mcp.tool()
async def jmag_export_results(study: str, out_dir: str, tables: list[str] | None = None, model: str | None = None) -> dict:
    """Export result tables to CSV files (one per table, x column + data columns) for post-processing outside JMAG."""
    return await run_op("export_results", 900, study=study, out_dir=out_dir, tables=tables, model=model)


@mcp.tool()
async def jmag_contour_image(study: str, result_type: str = "MagneticFluxDensity", component: str = "Absolute",
                             step: int | None = None, path: str | None = None, model: str | None = None) -> list:
    """Contour plot image (default |B| at the last step) with min/max values. The fastest sanity check of a field
    solution: saturation hot-spots, magnetization direction, flux paths, leakage."""
    return with_image(await run_op("contour_image", 300, study=study, path=out_path("contour", path),
                                   result_type=result_type, component=component, step=step, model=model))


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
      = mean(torque)*omega (+ eddy losses). Needs speed_rpm and electrical_frequency_hz. Proves torque, flux
      linkage, winding layout and motion agree with each other.
    """
    if check == "kvl":
        if not (coil and probe and resistance is not None):
            return {"ok": False, "error": {"message": "kvl needs coil, probe and resistance"}}
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
        return {"ok": True, "check": "kvl", **res}
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
            return {"ok": True, "check": "energy", "conditions": cols, **res}
        if "Coil Flux-Linkage" in titles:
            psi = (await run_op("get_result", 300, study=study, table="Coil Flux-Linkage", model=model))["result"]
            cur = (await run_op("get_result", 300, study=study, table="Circuit Current", model=model))["result"]
            cols = [c for c in psi["columns"] if c in cur["columns"]]
            k = max(range(len(W)), key=lambda j: abs(W[j]))
            res = health.inductance_consistency([psi["columns"][c][k] for c in cols], [cur["columns"][c][k] for c in cols], W[k])
            return {"ok": True, "check": "energy", "coils": cols, "at_step_index": k, **res}
        return {"ok": False, "error": {"message": "no coil flux tables found"}}
    if check == "power_balance":
        if not (speed_rpm and electrical_frequency_hz):
            return {"ok": False, "error": {"message": "power_balance needs speed_rpm and electrical_frequency_hz"}}
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
        return {"ok": True, "check": "power_balance", "coils": coils, "torque_column": tcol, **res}
    return {"ok": False, "error": {"message": f"unknown check {check!r}", "hint": "kvl | energy | power_balance"}}


# =========================================================================== knowledge

@mcp.tool()
async def jmag_docs_search(query: str, limit: int = 6) -> dict:
    """Search the JMAG scripting manual installed on this machine (condition/circuit/study property names, flags,
    result types). First call builds a local index (~1 min)."""
    B.need_install()
    assert B.docs is not None
    info = await asyncio.to_thread(B.docs.ensure)
    return {"index": info, "hits": await asyncio.to_thread(B.docs.search, query, limit)}


@mcp.tool()
async def jmag_docs_page(page: str, max_chars: int = 20000) -> dict:
    """Full text of one manual page returned by jmag_docs_search (e.g. 'scriptMagneticSettings.html')."""
    assert B.docs is not None
    try:
        return {"page": page, "text": await asyncio.to_thread(B.docs.page, page, max_chars)}
    except KeyError:
        return {"ok": False, "error": {"message": f"page {page!r} not found"}}


@mcp.tool()
async def jmag_api_lookup(query: str, limit: int = 15) -> dict:
    """Look up JMAG Python API methods from the installed jmag/designer.py: 'Study.CreateCondition', 'CreateWire',
    or keywords 'export image'. Use before writing jmag_exec_python code."""
    B.need_install()
    assert B.docs is not None
    return {"hits": await asyncio.to_thread(B.docs.api_lookup, query, limit)}


@mcp.tool()
async def jmag_exec_python(code: str, timeout_s: float = 600) -> dict:
    """Escape hatch for advanced features (PWM circuits, windings, parametric cases, optimisation ...).

    Runs Python 3.8 inside the worker with `app` (JMAG Application) in scope; set `result = ...` to return data,
    print() is captured. JMAG errors are still detected via the message log. Look up APIs with jmag_api_lookup first.
    """
    return await run_op("exec_python", timeout_s, code=code)


# =========================================================================== meta

@mcp.tool()
async def jmag_self_test(keep_open: bool = False, work_dir: str | None = None) -> dict:
    """Known-answer test of the whole toolchain on this machine/license (~1-2 min, separate JMAG instance).

    Builds a gapped C-core inductor from an external DXF, solves a static and a transient (circuit) study, and checks
    inductance vs analytic, static vs transient consistency, KVL residual and flux density range.
    Run it after installing, after JMAG updates, or whenever results look suspicious.
    """
    from .selftest import run_self_test
    B.need_install()
    return await asyncio.to_thread(run_self_test, B.install, keep_open, work_dir)


@mcp.tool()
async def jmag_journal(export_script: str | None = None, session_only: bool = True) -> dict:
    """Show the log of state-changing operations; export_script=path writes a replayable Python script
    (runs with JMAG's bundled Python) that reproduces the session - a reproducible record of the model build."""
    rows = []
    if B.journal.is_file():
        for line in B.journal.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not session_only or r.get("session") == B.session:
                rows.append(r)
    out: dict = {"ok": True, "entries": len(rows), "last": rows[-30:]}
    if export_script:
        from .replay import write_replay_script
        out["script"] = write_replay_script(rows, export_script, B.install)
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
