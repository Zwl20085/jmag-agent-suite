# -*- coding: utf-8 -*-
"""JMAG operations executed inside the worker (Python 3.8, JMAG bundled interpreter).

Design rules learned the hard way on JMAG-Designer 23.0:

* The ProcessLink API almost never raises. A failed call returns ``None`` or an
  object whose ``IsValid()`` is False, and records a line in the application
  message log. ``SetUseExceptions(True)`` does not change that. Therefore every
  op diffs ``app.GetAllMessages()`` and checks ``HasError()`` (see ``dispatch``),
  and every object we obtain goes through ``need()``.
* Everything we set is read back and reported, so silent no-ops become visible.
* Some calls crash the client with an access violation when given bad input
  (e.g. ``Load`` of a missing file). Validate paths before calling JMAG.
* Attaching to a dead PID "works" and then every call returns None. ``connected``
  checks the process and ``IsValid()``.
"""
import difflib
import io
import json
import math
import os
import re
import sys
import time
import traceback

OPS = {}
READ_ONLY = set()
_APP = [None]  # current Application, for helpers that need CreatePoint


def op(read_only=False):
    def deco(fn):
        OPS[fn.__name__] = fn
        if read_only:
            READ_ONLY.add(fn.__name__)
        return fn
    return deco


class OpError(Exception):
    def __init__(self, message, hint=None, data=None):
        Exception.__init__(self, message)
        self.message = message
        self.hint = hint
        self.data = data


# --------------------------------------------------------------------------- helpers

def jsonable(v, depth=0):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else repr(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, dict):
        return dict((str(k), jsonable(x, depth + 1)) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return [jsonable(x, depth + 1) for x in v]
    if hasattr(v, "GetX") and hasattr(v, "GetY"):
        try:
            return [v.GetX(), v.GetY(), v.GetZ()]
        except Exception:
            pass
    if hasattr(v, "GetName"):
        try:
            return v.GetName()
        except Exception:
            pass
    return str(v)


def need(obj, what, hint=None):
    ok = False
    try:
        ok = obj is not None and bool(obj.IsValid())
    except Exception:
        ok = False
    if not ok:
        raise OpError("%s not found or invalid" % what, hint)
    return obj


def close_matches(word, options, n=5):
    return difflib.get_close_matches(str(word), [str(o) for o in options], n=n, cutoff=0.4)


def strip_html(s):
    s = re.sub(r"(?i)<br\s*/?>|</tr>|</p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


DIRECTIONS = {"up": 0, "upward": 0, "+": 0, "+z": 0, "+theta": 0, "0": 0,
              "down": 1, "downward": 1, "-": 1, "-z": 1, "-theta": 1, "1": 1}


def direction_flag(d):
    if isinstance(d, bool):
        raise OpError("direction must be 'up' or 'down' (or 0/1)")
    if isinstance(d, int):
        if d in (0, 1):
            return d
    key = str(d).strip().lower()
    if key in DIRECTIONS:
        return DIRECTIONS[key]
    raise OpError("unknown direction %r" % (d,), "use 'up' (+z, into the screen-normal) or 'down' (-z)")


def _num_equal(a, b):
    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


# GetValue only works on these; 'Integer'/'Vector'/'Table' typed properties make JMAG log an error.
VALUE_TYPES = ("flag", "double", "real", "bool", "boolean")


def read_prop(obj, name):
    """Read a property without provoking JMAG errors for non-scalar types."""
    try:
        ptype = obj.GetPropertyType(name)
    except Exception:
        ptype = None
    t = (ptype or "").lower()
    if t in VALUE_TYPES:
        return ptype, obj.GetValue(name)
    if t == "string":
        return ptype, obj.GetStringValue(name)
    return ptype, None


def set_props(obj, values, strict=True):
    """Set properties with readback. Values may be numbers, strings (equations or
    string properties), lists (tables) or {"value": x, "unit": "mm"}."""
    names = list(obj.GetPropertyNames() or [])
    report = {}
    unknown = {}
    for key, val in values.items():
        if names and key not in names:
            unknown[key] = close_matches(key, names)
            continue
        if isinstance(val, dict) and "unit" in val:
            obj.SetValueWithUnit(key, val["value"], val["unit"])
            want = val["value"]
        elif isinstance(val, (list, tuple)):
            obj.SetTable(key, [list(r) if isinstance(r, (list, tuple)) else [r] for r in val])
            report[key] = {"set": "table[%d]" % len(val)}
            continue
        else:
            obj.SetValue(key, val)
            want = val
        ptype, got = read_prop(obj, key)
        entry = {"set": want, "read": got, "type": ptype}
        if isinstance(val, dict) and "unit" in val:
            entry["unit"] = val["unit"]
        elif got is not None and not isinstance(want, str) and not _num_equal(want, got):
            entry["mismatch"] = True
        report[key] = entry
    if unknown and strict:
        raise OpError("unknown property name(s): %s" % ", ".join(sorted(unknown)),
                      "closest matches: %s. Use jmag_get_properties to list valid names." % json.dumps(unknown),
                      data={"applied": report})
    if unknown:
        report["_unknown"] = unknown
    return report


def get_props(obj, names=None, with_help=False):
    out = []
    for n in (names or obj.GetPropertyNames() or []):
        if not n:
            continue
        ptype, val = read_prop(obj, n)
        row = {"name": n, "type": ptype, "value": jsonable(val)}
        if with_help:
            try:
                row["help"] = obj.GetPropertyHelp(n)
            except Exception:
                pass
        out.append(row)
    return out


# --------------------------------------------------------------------------- context

class Context(object):
    def __init__(self, jmag_home):
        self.home = jmag_home
        self.app = None
        self.designer = None
        self.pid = None
        self._material_names = None
        self._msg_count = 0

    # -- lifecycle
    def module(self):
        if self.designer is None:
            from jmag.designer import designer as d
            self.designer = d
        return self.designer

    def connected(self):
        if self.app is None:
            return False
        if self.pid and not pid_alive(self.pid):
            return False
        try:
            return bool(self.app.IsValid())
        except Exception:
            return False

    def need_app(self):
        if not self.connected():
            raise OpError("not connected to a live JMAG-Designer",
                          "call jmag_connect first (it attaches to a running instance or launches one)")
        return self.app

    # -- dispatch with message diffing
    def dispatch(self, name, args):
        fn = OPS.get(name)
        if fn is None:
            return {"ok": False, "error": {"type": "UnknownOp", "message": "unknown op %r" % name,
                                           "hint": "closest: %s" % close_matches(name, OPS)}}
        live = self.connected()
        _APP[0] = self.app
        if live:
            try:
                self.app.ClearError()
                self.app.ClearWarning()
                self._msg_count = len(self.app.GetAllMessages() or [])
            except Exception:
                live = False
        resp = {"ok": True}
        try:
            resp["result"] = jsonable(fn(self, **args))
        except OpError as e:
            resp["ok"] = False
            resp["error"] = {"type": "OpError", "message": e.message, "hint": e.hint}
            if e.data is not None:
                resp["error"]["data"] = jsonable(e.data)
        except TypeError as e:
            resp["ok"] = False
            resp["error"] = {"type": "BadArguments", "message": str(e),
                             "hint": (fn.__doc__ or "").strip().split("\n")[0]}
        except Exception as e:
            resp["ok"] = False
            resp["error"] = {"type": type(e).__name__, "message": str(e), "trace": traceback.format_exc()[-1500:]}
            if self.pid and not pid_alive(self.pid):
                resp["error"] = {"type": "JmagCrashed",
                                 "message": "JMAG-Designer (pid %s) is no longer running - it crashed or was closed during '%s'"
                                            % (self.pid, name),
                                 "hint": "jmag_connect launches a new instance; reopen the last saved project. Save before "
                                         "risky steps (mesh/run of a new setup). jmag_cleanup removes orphaned helpers."}
                self.app = None
        if (live or self.connected()) and self.connected():
            try:
                msgs = self.app.GetAllMessages() or []
                new = list(msgs[self._msg_count:]) if len(msgs) >= self._msg_count else list(msgs)
                has_err = bool(self.app.HasError())
                has_warn = bool(self.app.HasWarning())
                resp["jmag"] = {"messages": new, "error": has_err, "warning": has_warn}
                if has_err and resp["ok"]:
                    resp["ok"] = False
                    resp["error"] = {"type": "JmagError",
                                     "message": "JMAG reported an error during this operation: %s" % (new[-1] if new else "?"),
                                     "hint": "partial result attached; inspect jmag.messages"}
            except Exception:
                pass
        return resp

    # -- object lookup
    def model(self, ref=None):
        app = self.need_app()
        if ref is None or ref == "":
            m = app.GetCurrentModel()
            if (m is None or not m.IsValid()) and app.NumModels() > 0:
                m = app.GetModel(0)
            return need(m, "current model", "import geometry first (jmag_import_dxf / jmag_import_cad)")
        m = app.GetModel(ref)
        if m is None or not m.IsValid():
            raise OpError("model %r not found" % (ref,), "models: %s" % self.model_names())
        return m

    def model_names(self):
        app = self.need_app()
        return [app.GetModel(i).GetName() for i in range(app.NumModels())]

    def study(self, ref=None, model=None):
        """Resolve a study by name/index. Searches all models when model is None."""
        app = self.need_app()
        if ref is None or ref == "":
            s = app.GetCurrentStudy()
            return need(s, "current study", "create one with jmag_create_study")
        if model is not None:
            m = self.model(model)
            s = m.GetStudy(ref)
            if s is None or not s.IsValid():
                raise OpError("study %r not found in model %r" % (ref, m.GetName()),
                              "studies: %s" % [m.GetStudy(i).GetName() for i in range(m.NumStudies())])
            return s
        hits = []
        for i in range(app.NumModels()):
            m = app.GetModel(i)
            for j in range(m.NumStudies()):
                s = m.GetStudy(j)
                if (isinstance(ref, int) and j == ref) or s.GetName() == ref:
                    hits.append((m, s))
        if not hits:
            raise OpError("study %r not found" % (ref,), "existing: %s" % self.all_studies())
        if len(hits) > 1 and not isinstance(ref, int):
            raise OpError("study name %r is ambiguous" % (ref,), "pass model= to disambiguate")
        return hits[0][1]

    def model_of(self, study):
        app = self.need_app()
        name = study.GetName()
        sid = study.GetStudyId() if hasattr(study, "GetStudyId") else None
        for i in range(app.NumModels()):
            m = app.GetModel(i)
            for j in range(m.NumStudies()):
                s = m.GetStudy(j)
                if s.GetName() == name and (sid is None or s.GetStudyId() == sid):
                    return m
        return self.model()

    def all_studies(self):
        app = self.need_app()
        out = []
        for i in range(app.NumModels()):
            m = app.GetModel(i)
            out += ["%s/%s" % (m.GetName(), m.GetStudy(j).GetName()) for j in range(m.NumStudies())]
        return out

    def material_names(self):
        if self._material_names is None:
            lib = self.need_app().GetMaterialLibrary()
            names = []
            for i in range(lib.NumMaterials()):
                mm = lib.GetMaterial(i)
                if mm is not None and mm.IsValid():
                    names.append(mm.GetName())
            self._material_names = sorted(set(names))
        return self._material_names


def pid_alive(pid):
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def part_rows(model):
    dim = model.GetDimension()
    rows = []
    for i in range(model.NumParts()):
        p = model.GetPartByIndex(i)
        c = p.CentroidPosition()
        row = {"id": p.ID(), "name": p.GetName(),
               "centroid": [round(c.GetX(), 6), round(c.GetY(), 6), round(c.GetZ(), 6)]}
        if dim == 2:
            try:  # centroids of C/L-shaped parts lie outside the part; this one is always inside
                q = p.PointInPart()
                row["inside_point"] = [round(q.GetX(), 6), round(q.GetY(), 6)]
            except Exception:
                pass
        if dim == 2:
            row["area"] = p.Area()
        else:
            row["volume"] = p.Volume()
        rows.append(row)
    return rows


def resolve_part(model, ref, app=None):
    """Part reference: name (str), id (int) or point [x, y(, z)] inside the part."""
    if isinstance(ref, (list, tuple)):
        x, y = float(ref[0]), float(ref[1])
        z = float(ref[2]) if len(ref) > 2 else 0.0
        p = model.GetPartByPosition(_APP[0].CreatePoint(x, y, z))
        if p is not None and p.IsValid():
            return p.GetName()
        _APP[0].ClearError()
        raise OpError("no part contains point %s" % (list(ref),),
                      "use a part's inside_point from jmag_list_parts (centroids of C/L shapes lie outside)")
    names = [model.GetPartByIndex(i).GetName() for i in range(model.NumParts())]
    if isinstance(ref, int) and not isinstance(ref, bool):
        p = model.GetPart(ref)
        need(p, "part id %s" % ref)
        return p.GetName()
    if ref in names:
        return ref
    raise OpError("part %r not found" % (ref,), "closest: %s" % close_matches(ref, names))


def select_parts(cond, model, parts):
    """Attach parts to a condition/sub-condition and verify the count."""
    names = [resolve_part(model, p) for p in parts]
    cond.ClearParts()
    for n in names:
        sel = cond.GetSelection()
        sel.Clear()
        sel.SelectPart(n)
        cond.AddSelected(sel)
    got = cond.GetSelection().NumParts()
    if got != len(set(names)):
        raise OpError("condition %r has %s parts selected, expected %s" % (cond.GetName(), got, len(set(names))))
    return names


# --------------------------------------------------------------------------- session ops

@op(read_only=True)
def ping(ctx):
    """Health of the worker and its JMAG connection."""
    return {"worker_pid": os.getpid(), "python": sys.version.split()[0], "connected": ctx.connected(), "jmag_pid": ctx.pid}


@op()
def connect(ctx, pid=None, launch=True, visible=True):
    """Attach to JMAG-Designer by pid, or launch a new instance."""
    d = ctx.module()
    if pid:
        if not pid_alive(pid):
            raise OpError("process %s is not running" % pid, "omit pid to launch a new JMAG-Designer")
        ctx.app = d.AttachApplication(int(pid))
        ctx.pid = int(pid)
    elif launch:
        ctx.app = d.CreateApplication()
        ctx.pid = int(d.GetApplicationPid(ctx.app))
    else:
        raise OpError("no pid given and launch=False")
    _APP[0] = ctx.app
    if not ctx.connected():
        ctx.app = None
        raise OpError("JMAG application handle is not valid after connect", "is a license available? check jmag_dialogs / jmag_screenshot")
    if visible:
        ctx.app.Show()
    else:
        ctx.app.Hide()
    ctx._material_names = None
    # An attached JMAG keeps the error flag and message log of whatever ran before (another
    # session's failed op). dispatch() only baselines them for ops that start connected, so
    # without this a clean connect would report that stale error as its own failure.
    ctx.app.ClearError()
    ctx.app.ClearWarning()
    ctx._msg_count = len(ctx.app.GetAllMessages() or [])
    return app_info(ctx)


@op(read_only=True)
def app_info(ctx):
    """Version, PID, project and model/study overview."""
    app = ctx.need_app()
    models = []
    for i in range(app.NumModels()):
        m = app.GetModel(i)
        studies = []
        for j in range(m.NumStudies()):
            s = m.GetStudy(j)
            studies.append({"name": s.GetName(), "type": s.GetScriptTypeName(), "has_result": bool(s.AnyCaseHasResult())})
        models.append({"name": m.GetName(), "dimension": m.GetDimension(), "parts": m.NumParts(), "studies": studies})
    return {"jmag_pid": ctx.pid, "version": app.VersionString(True), "project_path": app.GetProjectPath(),
            "project_name": app.GetProjectName(), "running_process": bool(app.HasRunningProcess()), "models": models}


@op()
def quit_app(ctx, save=False):
    """Close JMAG-Designer (optionally saving first)."""
    app = ctx.need_app()
    if save:
        app.Save()
    app.Quit()
    ctx.app = None
    ctx.pid = None
    return {"quit": True}


# --------------------------------------------------------------------------- project ops

@op()
def project_new(ctx, path, name=None, overwrite=False):
    """Create a project and save it to `path` (*.jproj). Geometry import needs a saved project."""
    app = ctx.need_app()
    path = os.path.abspath(path)
    if not path.lower().endswith(".jproj"):
        raise OpError("path must end with .jproj")
    if os.path.exists(path) and not overwrite:
        raise OpError("%s already exists" % path, "pass overwrite=true or open it with jmag_open_project")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    app.NewProject(name or os.path.splitext(os.path.basename(path))[0])
    app.SaveAs(path)
    if not os.path.exists(path):
        raise OpError("JMAG did not write %s" % path)
    return {"project_path": app.GetProjectPath()}


@op()
def project_open(ctx, path):
    """Open an existing *.jproj."""
    app = ctx.need_app()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OpError("file not found: %s" % path)
    app.Load(path)
    return app_info(ctx)


@op()
def project_save(ctx, path=None):
    """Save the project (Save As when path is given)."""
    app = ctx.need_app()
    if path:
        path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        app.SaveAs(path)
    else:
        if not app.GetProjectPath():
            raise OpError("project has never been saved", "pass path=...jproj")
        app.Save()
    p = app.GetProjectPath()
    return {"project_path": p, "bytes": os.path.getsize(p) if p and os.path.exists(p) else None}


# --------------------------------------------------------------------------- geometry ops

@op()
def import_dxf(ctx, path, model_name=None, create_regions=True):
    """Import a 2D DXF/SAT through Geometry Editor and create a 2D model (closed loops -> regions)."""
    app = ctx.need_app()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OpError("file not found: %s" % path)
    if not app.GetProjectPath():
        raise OpError("the project must be saved before Geometry Editor can be used",
                      "JMAG would otherwise block on a modal 'save project' dialog; call jmag_new_project(path=...) first")
    before = app.NumModels()
    geom = app.CreateGeometryEditor(False)
    if geom is None:
        raise OpError("could not start Geometry Editor")
    try:
        geom.SetFileSaveConfirmation(False)
    except Exception:
        pass
    geom.Load(path)
    asm = geom.GetDocument().GetAssembly()
    sketches = []
    for i in range(asm.NumItems()):
        it = asm.GetItem(i)
        if it.GetScriptTypeName() == "Sketch":
            sketches.append(it)
    if not sketches:
        raise OpError("no sketch found after loading %s" % path, "is it a 2D DXF with LINE/ARC/LWPOLYLINE entities?")
    if create_regions:
        for sk in sketches:
            sk.OpenSketch()
            sk.CreateRegions()
            sk.CloseSketch()
    errs = ""
    try:
        errs = geom.GetAllErrors()
    except Exception:
        pass
    app.ImportDataFromGeometryEditor()
    after = app.NumModels()
    if after <= before:
        raise OpError("no model was created from %s" % path,
                      "check that the outlines are closed loops; Geometry Editor said: %r" % errs)
    m = app.GetModel(after - 1)
    if model_name:
        m.SetName(model_name)
    app.SetCurrentModel(after - 1)
    return {"model": m.GetName(), "dimension": m.GetDimension(), "parts": part_rows(m),
            "sketches": [s.GetName() for s in sketches], "geometry_editor_errors": errs.strip() if errs else ""}


@op()
def import_cad(ctx, path, model_name=None, healing=True, remove_sheet=True, unit="mm"):
    """Import 3D CAD (STEP/IGES/Parasolid/SAT...) as a 3D model."""
    app = ctx.need_app()
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise OpError("file not found: %s" % path)
    name = model_name or os.path.splitext(os.path.basename(path))[0]
    before = app.NumModels()
    m = app.LoadCadDataWithOption(path, name, bool(healing), bool(remove_sheet), unit)
    if m is None or not m.IsValid():
        if app.NumModels() > before:
            m = app.GetModel(app.NumModels() - 1)
        else:
            raise OpError("CAD import failed for %s" % path)
    app.SetCurrentModel(app.NumModels() - 1)
    return {"model": m.GetName(), "dimension": m.GetDimension(), "parts": part_rows(m)}


@op(read_only=True)
def list_parts(ctx, model=None):
    """Parts with id, name, area/volume and centroid (model units)."""
    m = ctx.model(model)
    return {"model": m.GetName(), "dimension": m.GetDimension(), "parts": part_rows(m)}


@op()
def rename_parts(ctx, renames, model=None):
    """renames: [{"part": name|id|[x,y,(z)], "name": "NewName"}]."""
    m = ctx.model(model)
    done = []
    for r in renames:
        old = resolve_part(m, r["part"])
        new = r["name"]
        p = None
        for i in range(m.NumParts()):
            q = m.GetPartByIndex(i)
            if q.GetName() == old:
                p = q
                break
        need(p, "part %r" % old).SetName(new)
        done.append({"from": old, "to": new})
    names = [x["name"] for x in part_rows(m)]
    dup = sorted(set(n for n in names if names.count(n) > 1))
    return {"renamed": done, "parts": part_rows(m), "duplicate_names": dup}


# --------------------------------------------------------------------------- study ops

@op()
def create_study(ctx, study_type, name, model=None):
    """Create a study, e.g. Static2D, Transient2D, Frequency2D, Static, Transient."""
    m = ctx.model(model)
    types = list(m.GetStudyTypeNames() or [])
    if study_type not in types:
        raise OpError("study type %r not available for this %dD model" % (study_type, m.GetDimension()),
                      "available: %s" % types)
    existing = [m.GetStudy(i).GetName() for i in range(m.NumStudies())]
    if name in existing:
        raise OpError("study %r already exists in model %r" % (name, m.GetName()))
    s = need(m.CreateStudy(study_type, name), "new study")
    return {"model": m.GetName(), "study": s.GetName(), "type": s.GetScriptTypeName(),
            "condition_types": list(s.GetConditionTypes() or [])}


@op()
def delete_study(ctx, study, model=None):
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    m.DeleteStudy(s.GetName())
    return {"deleted": study}


@op()
def duplicate_study(ctx, study, new_name, model=None):
    """Copy a study with all its settings (results are not copied)."""
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    m.DuplicateStudyName(s.GetName(), new_name, True)
    return {"study": need(m.GetStudy(new_name), "duplicated study").GetName()}


def _target(ctx, target):
    """Resolve a property target.

    target = {"study": ..., "model": ..., "kind": study|step|mesh|condition|subcondition|component|material|equation,
              "name": condition/component name or part name (material), "index": sub-condition index}
    """
    kind = target.get("kind", "study")
    s = ctx.study(target.get("study"), target.get("model"))
    if kind in ("study", "study_properties"):
        return need(s.GetStudyProperties(), "study properties")
    if kind == "step":
        return need(s.GetStep(), "step control")
    if kind == "mesh":
        return need(s.GetMeshControl(), "mesh control")
    if kind in ("condition", "subcondition"):
        c = _find_condition(s, target["name"])
        if c is None:
            raise OpError("condition %r not found" % target["name"],
                          "conditions: %s" % [s.GetCondition(i).GetName() for i in range(s.NumConditions())])
        if kind == "subcondition":
            return need(c.GetSubCondition(int(target.get("index", 0))), "sub-condition %s" % target.get("index", 0))
        return c
    if kind == "component":
        if not s.HasCircuit():
            raise OpError("study has no circuit")
        c = s.GetCircuit().GetComponent(target["name"])
        return need(c, "circuit component %r" % target["name"])
    if kind == "material":
        m = ctx.model_of(s)
        part = resolve_part(m, target["name"])
        return need(s.GetMaterial(part), "material of part %r" % part, "assign one with jmag_set_material")
    raise OpError("unknown target kind %r" % kind,
                  "use study|step|mesh|condition|subcondition|component|material")


@op(read_only=True)
def get_properties(ctx, target, names=None, with_help=False):
    """List property names, types and current values of any settable object."""
    obj = _target(ctx, target)
    return {"target": target, "properties": get_props(obj, names, with_help)}


@op()
def set_properties(ctx, target, values, strict=True):
    """Set properties with readback (numbers, equation strings, tables, {value, unit})."""
    obj = _target(ctx, target)
    return {"target": target, "applied": set_props(obj, values, strict)}


@op()
def set_step(ctx, study, steps=None, time_step=None, end_time=None, step_type=None, model=None):
    """Transient step control. Simple: steps + time_step. Uniform: steps + end_time."""
    s = ctx.study(study, model)
    st = need(s.GetStep(), "step control")
    vals = {}
    if step_type is None:
        step_type = 1 if (end_time is not None and time_step is None) else 0
    vals["StepType"] = step_type
    if steps is not None:
        vals["Step"] = steps
    if time_step is not None:
        vals["TimeStep"] = time_step
    if end_time is not None:
        vals["EndPoint"] = end_time
    applied = set_props(st, vals)
    return {"applied": applied, "step": get_props(st, ["StepType", "Step", "TimeStep", "EndPoint", "StepDivision"])}


@op()
def set_equations(ctx, study, equations, descriptions=None, model=None):
    """Create/update study parameters (DesignTable equations). Values: number or expression string."""
    s = ctx.study(study, model)
    t = need(s.GetDesignTable(), "design table")
    out = {}
    for name, val in equations.items():
        if not t.HasEquation(name):
            t.AddEquation(name)
        eq = need(t.GetEquation(name), "equation %r" % name)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            eq.SetType(0)
            eq.SetExpression(repr(float(val)) if isinstance(val, float) else str(val))
        else:
            eq.SetType(1)
            eq.SetExpression(str(val))
        if descriptions and name in descriptions:
            eq.SetDescription(descriptions[name])
        out[name] = {"expression": eq.GetExpression(), "value": eq.GetValue()}
    return {"equations": out}


@op(read_only=True)
def get_equations(ctx, study, model=None):
    s = ctx.study(study, model)
    t = need(s.GetDesignTable(), "design table")
    out = {}
    names = []
    try:
        names = list(t.AllParameterNames() or [])
    except Exception:
        pass
    for i in range(1000):
        eq = t.GetEquation(i)
        if eq is None or not eq.IsValid():
            break
        out[eq.GetName()] = {"type": "value" if eq.GetType() == 0 else "expression",
                             "expression": eq.GetExpression(), "value": eq.GetValue(), "description": eq.GetDescription()}
    ctx.app.ClearError()  # the loop ends on an out-of-range index by design
    return {"equations": out, "parameters": names}


# --------------------------------------------------------------------------- materials

@op(read_only=True)
def find_materials(ctx, query="", limit=40):
    """Search the material library (case-insensitive substring; fuzzy fallback)."""
    names = ctx.material_names()
    q = (query or "").lower()
    hits = [n for n in names if q in n.lower()] if q else names
    if not hits and q:
        hits = close_matches(query, names, n=limit)
    return {"count": len(hits), "materials": hits[:limit], "library_size": len(names)}


@op()
def set_material(ctx, study, parts, material, properties=None, orientation=None, model=None):
    """Assign a library material to parts; optional material properties and magnet orientation.

    orientation = {"pattern": "RadialCircular"|"ParallelCircular"|..., "direction": [x,y,z],
                   "origin": [x,y,z], "axis": [x,y,z]}
    """
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    names = ctx.material_names()
    if material not in names:
        raise OpError("material %r not in library" % material, "closest: %s" % close_matches(material, names))
    done = []
    for p in parts:
        pn = resolve_part(m, p)
        s.SetMaterialByName(pn, material)
        got = s.GetMaterial(pn)
        gname = got.GetName() if got is not None and got.IsValid() else None
        if gname != material:
            raise OpError("material readback for %r is %r, expected %r" % (pn, gname, material))
        mat = got
        row = {"part": pn, "material": gname}
        if properties:
            row["applied"] = set_props(mat, properties)
        if orientation:
            row["orientation"] = _orient(ctx, mat, orientation)
        done.append(row)
    return {"assigned": done}


#: Material-Editor flags of a non-linear soft-magnetic steel (``lib.GetUserMaterial(name)``,
#: scriptMaterialEditor): MagneticPropertyType 0 = MagneticSteel (1 = Magnet!),
#: MagneticSteelPermeabilityType 2 = BHCurve. Verified on 23.0: the MaterialObject flag
#: "MagneticType" = 0 ("PermON_BH") that protected library steels report is a MAGNET with a B-H
#: demagnetisation table (IsMagnet() True, the solver aborts with "Check the point array setting").
MAGNETIC_STEEL = 0
BH_CURVE = 2


@op()
def create_material(ctx, name, bh, density=None, conductivity=None, loss=None, overwrite=False,
                    folder="Custom Materials"):
    """Create a custom soft-magnetic material: B-H table (validated by the server), density,
    conductivity, iron-loss formula constants. Every value is read back."""
    app = ctx.need_app()
    lib = app.GetMaterialLibrary()
    if name in ctx.material_names():
        if not overwrite:
            raise OpError("material %r already exists" % name, "pass overwrite=true to replace it, or choose another name")
        lib.DeleteCustomMaterial(name, folder)
        app.ClearError()
    m = need(lib.CreateCustomMaterial(name, folder), "custom material %r" % name)
    um = need(lib.GetUserMaterial(name), "material-editor view of %r" % name)
    um.SetValue("MagneticPropertyType", MAGNETIC_STEEL)
    um.SetValue("MagneticSteelPermeabilityType", BH_CURVE)
    um.SetTable("BhTable", [[float(h), float(b)] for h, b in bh])
    ds = um.GetTable("BhTable")
    back = [[ds.GetValue(i, 0), ds.GetValue(i, 1)] for i in range(ds.GetRows())]
    if len(back) != len(bh) or any(abs(a[0] - float(b[0])) > 1e-9 * max(1.0, abs(b[0])) or
                                   abs(a[1] - float(b[1])) > 1e-9 for a, b in zip(back, bh)):
        raise OpError("B-H table read-back differs from the input (%d of %d rows)" % (len(back), len(bh)),
                      "inspect with jmag_exec_python: lib.GetUserMaterial(name).GetTable('BhTable')")
    flags = (um.GetValue("MagneticPropertyType"), um.GetValue("MagneticSteelPermeabilityType"))
    if flags != (MAGNETIC_STEEL, BH_CURVE) or um.IsMagnet():
        raise OpError("material %r is not a B-H steel after setup (MagneticPropertyType, "
                      "MagneticSteelPermeabilityType) = %s, IsMagnet = %s" % (name, flags, um.IsMagnet()),
                      "check the Material-Editor flags with jmag_exec_python")
    report = {"name": m.GetName(), "magnetic_property": "MagneticSteel", "permeability": "BHCurve",
              "is_magnet": False, "bh_rows": len(back), "bh_first": back[0], "bh_last": back[-1]}
    if density is not None:
        um.SetValueWithUnit("Density", float(density), "kg/m^3")    # plain SetValue is g/cm^3
        report["density_kg_m3"] = um.GetValueWithUnit("Density", "kg/m^3")
    if conductivity is not None:
        um.SetValue("ConductivityType", 0)
        um.SetValue("Conductivity", float(conductivity))
        report["conductivity"] = um.GetValue("Conductivity")
    if loss:
        m.SetValue("Loss_Type", 1)
        for key, prop in (("Kh", "Kh"), ("Ke", "Ke"), ("alpha", "Alpha"), ("beta", "Beta")):
            m.SetValue("Loss_Type" + prop, 1)
            m.SetValue("Loss_Constant" + prop, float(loss[key]))
        report["loss"] = {k: m.GetValue("Loss_Constant" + p) for k, p in
                          (("Kh", "Kh"), ("Ke", "Ke"), ("alpha", "Alpha"), ("beta", "Beta"))}
    ctx._material_names = None
    if name not in ctx.material_names():
        raise OpError("material %r was created but is not listed in the library" % name,
                      "check the folder %r in JMAG's material library" % folder)
    return report


def _orient(ctx, mat, o):
    pats = list(mat.GetListPatterns() or [])
    pat = o.get("pattern")
    if pat:
        if pats and pat not in pats:
            raise OpError("orientation pattern %r invalid" % pat, "valid: %s" % pats)
        mat.SetPattern(pat)
    if "direction" in o:
        mat.SetDirectionXYZ(*[float(v) for v in o["direction"]])
    if "origin" in o:
        mat.SetOriginXYZ(*[float(v) for v in o["origin"]])
    if "axis" in o:
        mat.SetAxisXYZ(*[float(v) for v in o["axis"]])
    if "outward" in o:  # Radial / circular patterns: True = outward (or clockwise), False = inward
        mat.SetOrientation(bool(o["outward"]))
    out = {"pattern": mat.GetPattern(), "valid_patterns": pats}
    try:
        out["outward"] = bool(mat.GetOrientation())
    except Exception:
        pass
    return out


@op()
def suppress_parts(ctx, parts, suppress=True, study=None, model=None):
    """Suppress parts (treated as absent; JMAG's own air fills the space) in a study, or model-wide if study is None."""
    if study:
        s = ctx.study(study, model)
        m = ctx.model_of(s)
        target = s
    else:
        m = ctx.model(model)
        target = m
    names = [resolve_part(m, p) for p in parts]
    for n in names:
        target.SuppressPart(n, 1 if suppress else 0)
    state = dict((n, bool(target.IsPartSuppressed(n))) for n in names)
    bad = [n for n, v in state.items() if v != bool(suppress)]
    if bad:
        raise OpError("suppression readback mismatch for %s" % bad)
    return {"suppressed" if suppress else "restored": names, "scope": "study %s" % study if study else "model"}


# --------------------------------------------------------------------------- conditions

def _check_type(s, ctype):
    types = list(s.GetConditionTypes() or [])
    if ctype not in types:
        hint = "available: %s" % types
        if ctype in ("FEMCoil", "FEMConductor") and s.GetScriptTypeName().startswith("Static"):
            hint = "Static studies take no circuit in JMAG 23: use the 'Current' condition (jmag_add_current) or a Transient/Frequency study"
        raise OpError("condition type %r not available in a %s study" % (ctype, s.GetScriptTypeName()), hint)


def _find_condition(s, name):
    """Lookup without provoking JMAG's 'No condition with the name' error."""
    for i in range(s.NumConditions()):
        c = s.GetCondition(i)
        if c.GetName() == name:
            return c
    return None


LINKED_TYPES = ("FEMCoil", "FEMConductor", "GroupFEMCoil", "GroupFEMConductor")


def _cond_summary(s, c):
    row = {"name": c.GetName(), "type": c.GetScriptTypeName()}
    try:
        sel = c.GetSelection()
        row["parts"] = [sel.PartID(i) for i in range(sel.NumParts())] if sel.IsValid() else []
    except Exception:
        pass
    if row["type"] in LINKED_TYPES:
        row["link"] = c.GetLink() or None
    row["properties"] = get_props(c)
    n = c.NumSubConditions()
    if n:
        subs = []
        for k in range(n):
            sc = c.GetSubCondition(k)
            sel = sc.GetSelection()
            subs.append({"index": k, "name": sc.GetName(),
                         "parts": [sel.PartID(i) for i in range(sel.NumParts())] if sel.IsValid() else [],
                         "properties": get_props(sc)})
        row["subconditions"] = subs
    return row


@op()
def add_condition(ctx, study, condition_type, name, parts=None, properties=None, replace=False, model=None):
    """Generic condition: Torque, Force, RotationMotion, RotationPeriodicBoundary, EddyCurrent, ..."""
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    _check_type(s, condition_type)
    old = _find_condition(s, name)
    if old is not None:
        if not replace:
            raise OpError("condition %r already exists" % name, "pass replace=true")
        s.DeleteCondition(name)
    c = need(s.CreateCondition(condition_type, name), "new %s condition" % condition_type)
    applied = set_props(c, properties) if properties else {}
    sel = select_parts(c, m, parts) if parts else []
    return {"condition": _cond_summary(s, c), "applied": applied, "parts": sel}


@op()
def add_current(ctx, study, name, parts, turns, current, direction="up", replace=False, model=None):
    """Static 'Current' condition (coil excitation without a circuit)."""
    props = {"Turn": turns, "Current": current, "Direction2D": direction_flag(direction)}
    return add_condition(ctx, study, "Current", name, parts, props, replace, model)


def _fem_group(ctx, study, ctype, subtype, name, component, sides, comp_props, replace, model):
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    _check_type(s, ctype)
    if not s.HasCircuit():
        raise OpError("study has no circuit", "create the circuit first (jmag_circuit_quick / jmag_circuit_add)")
    comp = s.GetCircuit().GetComponent(component)
    need(comp, "circuit component %r" % component,
         "components: %s" % _comp_names(s.GetCircuit()))
    if comp_props:
        set_props(comp, comp_props)
    old = _find_condition(s, name)
    if old is not None:
        if not replace:
            raise OpError("condition %r already exists" % name, "pass replace=true")
        s.DeleteCondition(name)
    c = need(s.CreateCondition(ctype, name), ctype)
    c.SetLink(component)
    if c.GetLink() != component:
        raise OpError("link readback %r != %r" % (c.GetLink(), component))
    dim = m.GetDimension()
    for k, side in enumerate(sides):
        auto = "%s%s%d" % (name, "+" if direction_flag(side.get("direction", "up")) == 0 else "-", k + 1)
        sc = c.GetSubCondition(k) if k < c.NumSubConditions() else c.CreateSubCondition(subtype, side.get("name") or auto)
        need(sc, "sub-condition %d" % k)
        sc.SetName(side.get("name") or auto)
        select_parts(sc, m, side["parts"])
        if dim == 2:
            sc.SetValue("Direction2D", direction_flag(side.get("direction", "up")))
        if side.get("properties"):
            set_props(sc, side["properties"])
    while c.NumSubConditions() > len(sides):
        c.RemoveSubCondition(c.NumSubConditions() - 1)
    return {"condition": _cond_summary(s, c), "component": {"name": component, "properties": get_props(comp)}}


@op()
def add_fem_coil(ctx, study, name, component, sides, turns=None, resistance=None, replace=False, model=None):
    """FEM Coil linked to circuit Coil component. sides: [{"parts": [...], "direction": "up"|"down"}]."""
    cp = {}
    if turns is not None:
        cp["Turn"] = turns
    if resistance is not None:
        cp["Resistance"] = resistance
    return _fem_group(ctx, study, "FEMCoil", "FEMCoilData", name, component, sides, cp, replace, model)


@op()
def add_fem_conductor(ctx, study, name, component, conductors, replace=False, model=None):
    """FEM Conductor (eddy currents in solid bars) linked to circuit FEMConductor component."""
    return _fem_group(ctx, study, "FEMConductor", "FEMConductorData", name, component, conductors, None, replace, model)


@op(read_only=True)
def list_conditions(ctx, study, model=None):
    s = ctx.study(study, model)
    return {"study": s.GetName(), "conditions": [_cond_summary(s, s.GetCondition(i)) for i in range(s.NumConditions())],
            "available_types": list(s.GetConditionTypes() or [])}


@op()
def delete_condition(ctx, study, name, model=None):
    s = ctx.study(study, model)
    if _find_condition(s, name) is None:
        raise OpError("condition %r not found" % name)
    s.DeleteCondition(name)
    return {"deleted": name}


# --------------------------------------------------------------------------- circuit

# Terminal offsets (grid units) relative to the instance position at rotation 0,
# calibrated on JMAG-Designer 23.0 from exported circuit images.
TERMINALS = {
    "Coil": [(-2, 0), (2, 0)], "FEMConductor": [(-2, 0), (2, 0)], "Resistor": [(-2, 0), (2, 0)],
    "Inductor": [(-2, 0), (2, 0)], "Capacitor": [(-2, 0), (2, 0)], "CurrentProbe": [(-2, 0), (2, 0)],
    "TwoTerminalCurrentSource": [(-2, 0), (2, 0)], "TwoTerminalVoltageSource": [(-2, 0), (2, 0)],
    "CurrentSource": [(2, 0)], "VoltageSource": [(2, 0)],
    "3PhaseCurrentSource": [(2, 2), (2, 0), (2, -2)], "3PhaseVoltageSource": [(2, 2), (2, 0), (2, -2)],
    "Ground": [(0, 2)], "VoltageProbe": [(0, -2)],
}


def _comp_names(c):
    return [c.GetComponent(i).GetName() for i in range(c.NumComponents())]


def _function(app, f):
    kind = str(f.get("type", "sin")).lower()
    ff = app.FunctionFactory()
    if kind == "constant":
        return ff.Constant(f["amplitude"])
    if kind in ("sin", "sine", "sinusoidal"):
        return ff.Sin(f["amplitude"], f["frequency"], f.get("phase", 0.0))
    raise OpError("unsupported function type %r" % kind, "use 'constant' or 'sin' here; tables via set_properties")


def _get_circuit(s, create=True):
    if s.HasCircuit():
        return s.GetCircuit()
    if not create:
        raise OpError("study has no circuit")
    c = s.CreateCircuit()
    if c is None or not c.IsValid():
        hint = None
        if s.GetScriptTypeName().startswith("Static"):
            hint = "JMAG 23 static studies do not accept circuits; use jmag_add_current, or a Transient2D study for circuit-driven coils"
        raise OpError("could not create a circuit in study %r (%s)" % (s.GetName(), s.GetScriptTypeName()), hint)
    return c


@op()
def circuit_add(ctx, study, components=None, wires=None, model=None):
    """Add components [{"type","name","x","y","angle"?,"properties"?,"function"?}] and wires [[x1,y1,x2,y2]]."""
    s = ctx.study(study, model)
    app = ctx.need_app()
    c = _get_circuit(s)
    added = []
    for comp in components or []:
        ctype = comp["type"]
        name = comp.get("name") or ctype
        if ctype == "Ground":
            name = "Ground"  # JMAG ignores custom names for grounds
            if "Ground" not in _comp_names(c):
                c.CreateComponent("Ground", "Ground")
        else:
            if name in _comp_names(c):
                raise OpError("component %r already exists" % name)
            need(c.CreateComponent(ctype, name), "component %s (%s)" % (name, ctype))
        inst = need(c.CreateInstance(name, int(comp["x"]), int(comp["y"])), "instance of %s" % name)
        if comp.get("angle"):
            inst.RotateTo(int(comp["angle"]))
        cobj = c.GetComponent(name)
        row = {"name": name, "type": ctype, "at": [int(comp["x"]), int(comp["y"])]}
        if comp.get("properties"):
            row["applied"] = set_props(cobj, comp["properties"])
        if comp.get("function"):
            cobj.SetFunction(_function(app, comp["function"]))
        offs = TERMINALS.get(ctype)
        if offs and not comp.get("angle"):
            row["terminals"] = [[int(comp["x"]) + dx, int(comp["y"]) + dy] for dx, dy in offs]
        added.append(row)
    wired = []
    for w in wires or []:
        wi = c.CreateWire(*[int(v) for v in w])
        wired.append({"wire": list(w), "valid": bool(wi is not None and wi.IsValid())})
    bad = [w for w in wired if not w["valid"]]
    if bad:
        raise OpError("some wires were rejected: %s" % bad, "wire endpoints must sit on grid points; see terminals")
    return {"added": added, "wires": wired, "components": _comp_names(c)}


@op()
def circuit_quick(ctx, study, topology, coils, source, probe=True, model=None):
    """Standard circuits with calibrated layout.

    topology: "current_source" | "voltage_source" (1 coil) or
              "three_phase_current" | "three_phase_voltage" (3 coils, star, grounded neutral).
    coils: [{"name": "CoilU", "turns": 30, "resistance": 0.05}, ...]
    source: {"type": "sin"|"constant", "amplitude": A or V, "frequency": Hz, "phase": deg}
    """
    s = ctx.study(study, model)
    app = ctx.need_app()
    c = _get_circuit(s)
    if c.NumComponents() > 0:
        raise OpError("circuit already has components: %s" % _comp_names(c), "delete the study circuit or use circuit_add")

    def mk(ctype, name, x, y):
        if ctype == "Ground":
            if "Ground" not in _comp_names(c):
                c.CreateComponent("Ground", "Ground")
            name = "Ground"
        else:
            need(c.CreateComponent(ctype, name), "component %s" % name)
        need(c.CreateInstance(name, x, y), "instance %s" % name)
        return c.GetComponent(name)

    def coil(spec, x, y):
        comp = mk("Coil", spec["name"], x, y)
        vals = {}
        if "turns" in spec:
            vals["Turn"] = spec["turns"]
        if "resistance" in spec:
            vals["Resistance"] = spec["resistance"]
        if vals:
            set_props(comp, vals)
        return comp

    layout = {}
    if topology in ("current_source", "voltage_source"):
        if len(coils) != 1:
            raise OpError("%s takes exactly one coil" % topology)
        stype = "CurrentSource" if topology == "current_source" else "VoltageSource"
        src = mk(stype, "Source", 0, 0)
        src.SetFunction(_function(app, source))
        coil(coils[0], 6, 0)
        mk("Ground", "Ground", 12, -2)
        c.CreateWire(2, 0, 4, 0)
        c.CreateWire(8, 0, 12, 0)
        layout = {"source": "Source", "coil_terminals": [[4, 0], [8, 0]]}
        if probe:
            mk("VoltageProbe", "VP_" + coils[0]["name"], 4, 4)
            c.CreateWire(4, 2, 4, 0)
            layout["probes"] = {coils[0]["name"]: "VP_" + coils[0]["name"]}
    elif topology in ("three_phase_current", "three_phase_voltage"):
        if len(coils) != 3:
            raise OpError("%s takes exactly three coils (U, V, W)" % topology)
        stype = "3PhaseCurrentSource" if topology == "three_phase_current" else "3PhaseVoltageSource"
        src = mk(stype, "Source3Ph", 0, 0)
        names = list(src.GetPropertyNames() or [])
        vals = {}
        for key, val in (("Amplitude", source.get("amplitude")), ("Frequency", source.get("frequency")),
                         ("PhaseU", source.get("phase", 0.0))):
            if val is not None and key in names:
                vals[key] = val
        if vals:
            set_props(src, vals)
        for k, (spec, y) in enumerate(zip(coils, (2, 0, -2))):
            coil(spec, 6, y)
            c.CreateWire(2, y, 4, y)
        c.CreateWire(8, 2, 8, 0)
        c.CreateWire(8, 0, 8, -2)
        mk("Ground", "Ground", 10, -2)
        c.CreateWire(8, 0, 10, 0)
        layout = {"source": "Source3Ph", "source_properties": get_props(src), "neutral": [8, 0]}
    else:
        raise OpError("unknown topology %r" % topology,
                      "current_source | voltage_source | three_phase_current | three_phase_voltage")
    return {"components": _comp_names(c), "layout": layout,
            "next": "link coils with jmag_add_fem_coil(component=<coil name>)"}


@op(read_only=True)
def circuit_summary(ctx, study, model=None):
    s = ctx.study(study, model)
    if not s.HasCircuit():
        return {"has_circuit": False}
    c = s.GetCircuit()
    comps = []
    for i in range(c.NumComponents()):
        cc = c.GetComponent(i)
        comps.append({"name": cc.GetName(), "type": cc.GetType(), "properties": get_props(cc)})
    return {"has_circuit": True, "components": comps, "wires": c.NumWires(), "terminal_offsets": TERMINALS}


@op()
def circuit_image(ctx, study, path, width=1600, height=1000, model=None):
    s = ctx.study(study, model)
    if not s.HasCircuit():
        raise OpError("study has no circuit")
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    s.GetCircuit().ExportImageWithSize(path, int(width), int(height))
    if not os.path.isfile(path):
        raise OpError("image was not written")
    return {"path": path}


# --------------------------------------------------------------------------- mesh

@op()
def mesh(ctx, study, model=None):
    """Generate the mesh and report its size."""
    s = ctx.study(study, model)
    t0 = time.time()
    s.CreateMesh()
    ok = bool(s.HasMesh())
    r = s.GetReport()
    out = {"has_mesh": ok, "elements": r.NumElements(), "vertices": r.NumVertices(), "seconds": round(time.time() - t0, 2)}
    if not ok:
        raise OpError("mesh generation failed", "inspect geometry (overlaps / tiny edges) and jmag.messages", data=out)
    return out


@op()
def mesh_size(ctx, study, parts, size, name=None, model=None):
    """Local mesh size on parts (model length unit)."""
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    mc = need(s.GetMeshControl(), "mesh control")
    types = list(mc.GetConditionTypeNames() or [])
    ctype = "Part" if "Part" in types else None
    if ctype is None:
        raise OpError("no 'Part' mesh condition type", "available: %s" % types)
    cond = need(mc.CreateCondition(ctype, name or "size_%s" % "_".join(str(p) for p in parts)), "mesh size condition")
    applied = set_props(cond, {"Size": size})
    sel = select_parts(cond, m, parts)
    return {"applied": applied, "parts": sel}


# --------------------------------------------------------------------------- run & results

def _report_messages(s):
    r = s.GetReport()
    out = {"errors": [], "warnings": []}
    for i in range(r.NumErrorMessages()):
        mm = r.GetErrorMessage(i)
        out["errors"].append(strip_html(mm.GetText()))
    for i in range(r.NumWarningMessages()):
        mm = r.GetWarningMessage(i)
        out["warnings"].append(strip_html(mm.GetText()))
    return out


@op(read_only=True)
def calc_folder(ctx, study, model=None):
    s = ctx.study(study, model)
    folder = None
    try:
        folder = s.CalculationFolder()
    except Exception:
        pass
    if not folder:
        try:
            folder = s.GetReport().GetCalculationFolder()
        except Exception:
            folder = None
    return {"folder": folder, "has_result": bool(s.HasResult())}


@op()
def run(ctx, study, overwrite=True, model=None):
    """Solve the current case in the foreground (blocking). Returns report + result status."""
    s = ctx.study(study, model)
    if s.HasResult():
        if not overwrite:
            raise OpError("study already has results", "pass overwrite=true to delete and re-run")
        s.DeleteResult()
        if s.HasResult():
            raise OpError("could not delete existing results")
    if ctx.app.HasRunningProcess():
        raise OpError("JMAG is already running an analysis", "wait or cancel it (jmag_cancel_run)")
    t0 = time.time()
    s.Run()
    took = round(time.time() - t0, 2)
    out = {"has_result": bool(s.HasResult()), "seconds": took}
    out.update(_report_messages(s))
    out["calc_folder"] = calc_folder(ctx, study, model)["folder"]
    out["elements"] = s.GetReport().NumElements()
    if not out["has_result"]:
        raise OpError("the solver finished without results (failed or cancelled)",
                      "read errors below and the solver log (jmag_run_status / jmag_report)", data=out)
    return out


@op(read_only=True)
def report(ctx, study, model=None):
    s = ctx.study(study, model)
    out = _report_messages(s)
    out["has_result"] = bool(s.HasResult())
    out["elements"] = s.GetReport().NumElements()
    out["calc_folder"] = calc_folder(ctx, study, model)["folder"]
    return out


def _tables(s):
    rt = s.GetResultTable()
    rows = []
    for i in range(rt.NumTables()):
        d = rt.GetData(i)
        title = d.GetName()
        base, _, unit = title.partition(":")
        rows.append({"index": i, "name": base.strip(), "unit": unit.strip(), "title": title,
                     "rows": d.GetRows(), "columns": [d.GetColName(j) for j in range(d.GetCols())]})
    return rt, rows


@op(read_only=True)
def list_results(ctx, study, model=None):
    s = ctx.study(study, model)
    if not s.HasResult():
        raise OpError("study has no results", "run it first (jmag_run)")
    _, rows = _tables(s)
    return {"study": s.GetName(), "tables": rows}


def _table_data(s, table):
    rt, rows = _tables(s)
    key = str(table).strip().lower()
    for r in rows:
        if key in (r["name"].lower(), r["title"].lower()) or (isinstance(table, int) and r["index"] == table):
            return rt.GetData(r["index"]), r
    d = rt.GetDataFromName(str(table))  # internal keys like LineCurrent, FEMCoilFlux, TerminalVoltage
    ctx_app_err = d is None or not d.IsValid() or d.GetRows() == 0
    if not ctx_app_err:
        return d, {"name": str(table), "unit": "", "title": d.GetName()}
    raise OpError("result table %r not found" % (table,),
                  "tables: %s" % [r["name"] for r in rows])


@op(read_only=True)
def get_result(ctx, study, table, columns=None, model=None):
    """Return a result table: x axis (time [s] or step) and the selected columns."""
    s = ctx.study(study, model)
    if not s.HasResult():
        raise OpError("study has no results", "run it first (jmag_run)")
    d, meta = _table_data(s, table)
    all_cols = [d.GetColName(j) for j in range(d.GetCols())]
    cols = columns or all_cols
    missing = [c for c in cols if c not in all_cols]
    if missing:
        raise OpError("columns %s not in table" % missing, "columns: %s" % all_cols)
    n = d.GetRows()
    time_unit = ""
    try:
        time_unit = d.GetTimeUnit()
    except Exception:
        pass
    x = [d.GetTime(k) for k in range(n)] if time_unit else [d.GetStep(k) for k in range(n)]
    data = {}
    for cname in cols:
        data[cname] = [d.GetValue(k, cname) for k in range(n)]
    try:
        unit = d.GetValueUnit()
    except Exception:
        unit = meta.get("unit", "")
    return {"table": meta.get("title"), "unit": unit, "x_name": "time" if time_unit else "step",
            "x_unit": time_unit, "x": x, "columns": data, "steps": [d.GetStep(k) for k in range(n)]}


@op()
def export_results(ctx, study, out_dir, tables=None, model=None):
    """Write each result table to CSV (x + columns)."""
    s = ctx.study(study, model)
    if not s.HasResult():
        raise OpError("study has no results")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    _, rows = _tables(s)
    written = []
    for r in rows:
        if tables and r["name"] not in tables and r["title"] not in tables:
            continue
        res = get_result(ctx, study, r["index"], None, model)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", r["name"]).strip("_") or "table%d" % r["index"]
        path = os.path.join(out_dir, "%s__%s.csv" % (re.sub(r"[^A-Za-z0-9_.-]+", "_", s.GetName()), safe))
        cols = list(res["columns"].keys())
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            xh = "%s[%s]" % (res["x_name"], res["x_unit"]) if res["x_unit"] else res["x_name"]
            f.write(",".join([xh] + ['"%s[%s]"' % (c, res["unit"]) for c in cols]) + "\n")
            for k in range(len(res["x"])):
                f.write(",".join([repr(res["x"][k])] + [repr(res["columns"][c][k]) for c in cols]) + "\n")
        written.append({"table": r["title"], "path": path, "rows": len(res["x"])})
    return {"written": written, "out_dir": out_dir}


@op()
def contour_image(ctx, study, path, result_type="MagneticFluxDensity", component="Absolute", step=None,
                  width=1400, height=1000, name="bridge_contour", model=None):
    """Render a contour plot to PNG (e.g. |B| at a given step; default = last step)."""
    app = ctx.need_app()
    s = ctx.study(study, model)
    if not s.HasResult():
        raise OpError("study has no results")
    m = ctx.model_of(s)
    for i in range(app.NumModels()):
        if app.GetModel(i).GetName() == m.GetName():
            app.SetCurrentModel(i)
    for j in range(m.NumStudies()):
        if m.GetStudy(j).GetName() == s.GetName():
            app.SetCurrentStudy(j)
    cd = None
    for i in range(s.NumContours()):
        if s.GetContour(i).GetName() == name:
            cd = s.GetContour(i)
    if cd is None:
        cd = need(s.CreateContour(name), "contour definition")
    cd.SetResultType(result_type, "")
    comps = list(cd.GetComponentNames() or [])
    if component and comps and component not in comps:
        raise OpError("component %r invalid for %s" % (component, result_type), "valid: %s" % comps)
    if component:
        cd.SetComponent(component)
    s.SetCurrentContour(name)
    v = app.View()
    v.SetContourView(True)
    rt = s.GetResultTable()
    solved = rt.GetData(0).GetRows() if rt.NumTables() else 1  # an aborted run has fewer steps than requested
    if step is None:
        step = solved
    if int(step) > solved or int(step) < 1:
        raise OpError("step %s not available; results exist for steps 1..%d" % (step, solved))
    v.SetStep(int(step))
    if m.GetDimension() == 2:
        v.Xy()
    else:
        v.Iso()
    v.Fit()
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    app.ExportImageWithSize(path, int(width), int(height))
    if not os.path.isfile(path):
        raise OpError("image was not written")
    return {"path": path, "step": step, "max": v.ContourMaxValue(), "min": v.ContourMinValue(), "result_type": result_type}


@op()
def model_image(ctx, path, width=1400, height=1000, show_mesh=False):
    """PNG of the current model view (geometry or mesh)."""
    app = ctx.need_app()
    v = app.View()
    v.SetContourView(False)
    # before any result exists JMAG logs "The contour is not available to show" for the call above
    # although the view is fine; clear it so a valid image is not reported as a failure
    app.ClearError()
    if show_mesh:
        v.ShowMesh()
    else:
        v.ShowModel()
    v.Fit()
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    app.ExportImageWithSize(path, int(width), int(height))
    return {"path": path}


# --------------------------------------------------------------------------- health facts

@op(read_only=True)
def study_facts(ctx, study, model=None):
    """Everything the server-side health rules need, in one round trip."""
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    sp = s.GetStudyProperties()
    facts = {"model": m.GetName(), "study": s.GetName(), "type": s.GetScriptTypeName(), "dimension": m.GetDimension(),
             "has_result": bool(s.HasResult()), "has_mesh": bool(s.HasMesh()), "elements": s.GetReport().NumElements(),
             "model_thickness": sp.GetValue("ModelThickness") if "ModelThickness" in (sp.GetPropertyNames() or []) else None,
             "nonlinear_max_iteration": sp.GetValue("NonlinearMaxIteration") if "NonlinearMaxIteration" in (sp.GetPropertyNames() or []) else None,
             "length_unit": None}
    try:
        facts["length_unit"] = m.GetCurrentUnit("Length")
    except Exception:
        pass
    parts = []
    for row in part_rows(m):
        mat = s.GetMaterial(row["name"])
        row["material"] = mat.GetName() if mat is not None and mat.IsValid() else None
        if row["material"]:
            _, eddy = read_prop(mat, "EddyCurrentCalculation") if "EddyCurrentCalculation" in (mat.GetPropertyNames() or []) else (None, None)
            row["eddy_current"] = eddy
            try:
                row["is_magnet"] = bool(mat.IsMagnet())
            except Exception:
                pass
        parts.append(row)
    facts["parts"] = parts
    facts["conditions"] = [_cond_summary(s, s.GetCondition(i)) for i in range(s.NumConditions())]
    facts["circuit"] = circuit_summary(ctx, s.GetName(), m.GetName())
    if s.GetScriptTypeName().startswith(("Transient", "Frequency")):
        facts["step"] = dict((r["name"], r["value"]) for r in get_props(s.GetStep()))
    try:
        facts["equations"] = get_equations(ctx, s.GetName(), m.GetName())["equations"]
    except Exception:
        facts["equations"] = {}
    ctx.app.ClearError()
    return facts


# --------------------------------------------------------------------------- escape hatch

@op()
def exec_python(ctx, code):
    """Run arbitrary Python inside the worker with `app`, `designer`, `ctx` in scope.
    Assign to `result` to return a value; print() output is captured."""
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    g = {"app": ctx.app, "designer": ctx.designer, "ctx": ctx, "result": None, "OpError": OpError}
    try:
        exec(compile(code, "<jmag_exec>", "exec"), g)
    finally:
        sys.stdout = old
    return {"result": g.get("result"), "stdout": buf.getvalue()[-20000:]}
