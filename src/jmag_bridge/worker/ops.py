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
                      "read errors below and the solver log (jmag_run_status / jmag_solver_report)", data=out)
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


class _CaseScope(object):
    """Make design-table case `case` (0-based) current while reading results; restore the previous one.

    Verified on 23.0: Study.SetCurrentCase switches what GetResultTable() returns. Refused while JMAG runs a
    solve, because switching the current case of the study being solved could disturb it."""

    def __init__(self, ctx, s, case):
        self.ctx, self.s, self.case, self.prev = ctx, s, case, None

    def __enter__(self):
        if self.case is None:
            return self
        n = self.s.GetDesignTable().NumCases()
        c = int(self.case)
        if c < 0 or c >= n:
            raise OpError("case %s out of range; study %r has %d case(s) (0..%d)" % (c, self.s.GetName(), n, n - 1),
                          "list cases with jmag_case_table(action='get')")
        if self.ctx.app.HasRunningProcess():
            raise OpError("JMAG is running a solve; switching the current case now could disturb it",
                          "read per-case results after jmag_run_status reports finished")
        self.prev = self.s.GetCurrentCase()
        if self.prev != c:
            self.s.SetCurrentCase(c)
        return self

    def __exit__(self, *exc):
        if self.prev is not None and self.prev != int(self.case):
            self.s.SetCurrentCase(self.prev)
        return False


def _need_result(s, case=None):
    has = s.CaseHasResult(int(case)) if case is not None else s.HasResult()
    if not has:
        where = "case %s of study %r" % (case, s.GetName()) if case is not None else "study %r" % s.GetName()
        raise OpError("%s has no results" % where, "run it first (jmag_run, or jmag_run_cases for design-table cases)")


@op(read_only=True)
def list_results(ctx, study, case=None, model=None):
    """Result tables of a study (current case, or design-table case `case`, 0-based)."""
    s = ctx.study(study, model)
    _need_result(s, case)
    with _CaseScope(ctx, s, case):
        _, rows = _tables(s)
    return {"study": s.GetName(), "case": case, "tables": rows}


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
def get_result(ctx, study, table, columns=None, case=None, model=None):
    """Return a result table: x axis (time [s], frequency [Hz] for loss spectra, or step) and the selected columns.
    case: design-table case index (0-based); default = the current case."""
    s = ctx.study(study, model)
    _need_result(s, case)
    with _CaseScope(ctx, s, case):
        out = _read_table(s, table, columns)
    out["case"] = case
    return out


def _read_table(s, table, columns=None):
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
    x_name = "step"
    if time_unit:  # loss studies report spectra: GetTime() is the frequency and GetTimeUnit() is "Hz" (23.0)
        x_name = "frequency" if time_unit.strip().lower() in ("hz", "khz", "mhz") else "time"
    return {"table": meta.get("title"), "unit": unit, "x_name": x_name,
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
        res = get_result(ctx, study, r["index"], None, model=model)
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
    v.ShowModel()          # a previous mesh view (model_image show_mesh) would draw every element edge
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
    if show_mesh:
        v.ShowModel()      # leave the view as found: later contour images must not carry the mesh
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


# =========================================================================== advanced API (parametric cases,
# loss study, efficiency map, winding, table export, case report, optimisation table, capabilities)
#
# Everything below follows the same rules as the ops above: Python 3.8 syntax, need() on every object,
# read-back of everything set, no probing calls that make JMAG log errors. Members marked "verified" were
# exercised on JMAG-Designer 23.0; see .claude/skills/jmag-simulation/references/advanced-api.md.

#: feature -> API members that must exist in this JMAG's Python wrapper. hasattr() on the jmag.designer
#: classes is pure Python (no JMAG call, no error log). Presence does not prove the licence has the module.
API_FEATURES = [
    ("parametric_cases", ["Study.GetDesignTable", "DesignTable.AddCases", "DesignTable.SetValue",
                          "DesignTable.AllParameterNames", "Study.SetCurrentCase", "Study.CaseHasResult",
                          "Study.RunAllCases"]),
    ("loss_study", ["Study.CreateLossStudy", "MaterialObject.LoadLossData"]),
    ("efficiency_map", ["Study.CreateEfficiencyMapDefinition", "Study.GetEfficiencyMapPlot",
                        "EfficiencyMapDefinition.SetMaxVoltage", "EfficiencyMapPlot.ExportData"]),
    ("optimization", ["Study.GetOptimizationTable", "Study.RunOptimization", "OptimizationTable.AddObjectiveItem",
                      "OptimizationTable.AddParametricItem"]),
    ("winding", ["Study.CreateWinding", "Study.CreateWindingRegion", "WindingDefinition.SetCoilTable",
                 "WindingDefinition.CreateCoil"]),
    ("table_export", ["Study.CreateTableDefinition", "Study.ExportTable", "Study.ExportResultValuesTable",
                      "ResultTable.WriteAllTables"]),
    ("case_report", ["Study.WriteCaseReport"]),
    ("analysis_group", ["Application.CreateAnalysisGroup", "AnalysisGroup.RunAllCases"]),
    ("batch_jobs", ["Application.CreateJob"]),
    ("jmag_rt_export", ["Study.ExportRttFile"]),
    ("express_motor_template", ["Application.CreateMotorTemplate"]),
]

#: solver/tool files in the installation folder (presence only; the licence decides whether they run)
INSTALL_FILES = [
    ("magnetic_solver", "ExecSolver.exe"),
    ("iron_loss_solver", os.path.join("Tools", "Loss", "mod", "ironloss.exe")),
    ("thermal_solver", "jheat.exe"),
    ("optimization_engines", "OptimizationEngines"),
    ("jmag_rt_maker", "RTMaker.exe"),
    ("express", "Express"),
]


def _designer_module(ctx):
    try:
        ctx.module()
    except Exception as e:  # no JMAG on this machine (offline tests)
        return None, str(e)
    return sys.modules.get("jmag.designer"), None


def _study_type_family(t):
    tl = t.lower()
    for key, fam in (("heat", "thermal"), ("structural", "structural"), ("elec", "electric_field"),
                     ("response", "response"), ("frequency", "magnetic_frequency"), ("transient", "magnetic_transient"),
                     ("static", "magnetic_static")):
        if key in tl:
            return fam
    return "other"


@op(read_only=True)
def capabilities(ctx, model=None):
    """What this JMAG install/licence/API can do: version, study types per model, API feature members."""
    dm, import_error = _designer_module(ctx)
    api = {}
    for feat, members in API_FEATURES:
        have = {}
        for mname in members:
            cls, meth = mname.split(".")
            have[mname] = bool(dm is not None and hasattr(getattr(dm, cls, None), meth))
        api[feat] = {"available": all(have.values()), "members": have}
    home = ctx.home or ""
    out = {"worker_python": sys.version.split()[0], "jmag_home": home, "api": api,
           "install_files": dict((k, os.path.exists(os.path.join(home, rel))) for k, rel in INSTALL_FILES),
           "licence": {"queryable": False,
                       "note": "JMAG 23 exposes no licence query in its Python API: a study type listed here can "
                               "still fail at Run when the licence lacks that solver module (the run report then "
                               "names it). Run jmag_self_test for a known-answer check of the magnetic solver.",
                       "env": dict((k, v) for k, v in os.environ.items()
                                   if k.upper().startswith(("LMX", "JMAG_LIC", "JMAG_LICENSE")))}}
    if import_error:
        out["import_error"] = import_error
    if not ctx.connected():
        out["connected"] = False
        out["hint"] = "connect (jmag_connect) and import a model to list the study types JMAG offers"
        return out
    app = ctx.app
    out["connected"] = True
    out["version"] = app.VersionString(True)
    try:
        out["express_mode"] = bool(app.IsExpressMode())
    except Exception:
        pass
    models = []
    refs = [model] if model else [app.GetModel(i).GetName() for i in range(app.NumModels())]
    for ref in refs:
        m = ctx.model(ref)
        types = list(m.GetStudyTypeNames() or [])
        fams = {}
        for t in types:
            fams.setdefault(_study_type_family(t), []).append(t)
        models.append({"model": m.GetName(), "dimension": m.GetDimension(), "study_types": types, "by_family": fams})
    out["models"] = models
    if not models:
        out["hint"] = "no model yet: study types are listed per model (2D and 3D differ); import geometry first"
    return out


# --------------------------------------------------------------------------- design table (parametric cases)

def _dt(s):
    return need(s.GetDesignTable(), "design table of study %r" % s.GetName())


def _short_param(full):
    """'Motion (RotationMotion2D): AngularVelocity' -> 'Motion: AngularVelocity'."""
    owner, sep, prop = str(full).partition(": ")
    return re.sub(r"\s*\([^)]*\)", "", owner).strip() + sep + prop


def _dt_params(t):
    rows = []
    for i in range(t.NumParameters()):
        rows.append({"index": i, "name": t.ParameterName(i), "type": t.ParameterTypeName(i),
                     "variable": t.GetVariableName(i)})
    return rows


def _param_index(t, name):
    """Index of an existing design-table parameter by full name, short name, equation name or variable name."""
    params = _dt_params(t)
    for key in ("name",):
        for p in params:
            if p[key] == name:
                return p["index"]
    for p in params:
        if p["name"] == "Equation parameters: %s" % name or _short_param(p["name"]) == name or p["variable"] == name:
            return p["index"]
    return None


def _resolve_addable(t, name):
    """Full design-table name for `name` (exact, or the short 'Owner: Prop' form), else OpError with hints."""
    allnames = list(t.AllParameterNames() or [])
    if name in allnames:
        return name
    hits = [n for n in allnames if _short_param(n) == name]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise OpError("parameter name %r is ambiguous" % name, "use one of the full names: %s" % hits)
    shorts = [_short_param(n) for n in allnames]
    raise OpError("%r is not a parameter of this study" % name,
                  "closest: %s. Names look like 'Motion (RotationMotion2D): AngularVelocity' (short form "
                  "'Motion: AngularVelocity' is accepted); list them with jmag_case_table(action='get', "
                  "candidates='Motion'). New equations come from jmag_set_equations and join the table "
                  "automatically." % close_matches(name, shorts + allnames, n=6))


def _case_table_dump(ctx, s, max_cases=200, candidates=None):
    t = _dt(s)
    params = _dt_params(t)
    eqs = []  # equations join the table as 'Equation parameters: <name>' (verified 23.0); look up by name
    for p in params:
        if p["type"] == "Equation" and p["variable"] and t.HasEquation(p["variable"]):
            eq = t.GetEquation(p["variable"])
            if eq is not None and eq.IsValid():
                eqs.append(eq)
    n = t.NumCases()
    cases = []
    for c in range(min(n, int(max_cases))):
        row = {"case": c, "label": t.GetLabel(c), "active": bool(t.IsActive(c)), "has_result": bool(s.CaseHasResult(c)),
               "values": dict((p["name"], jsonable(t.GetValue(c, p["index"]))) for p in params)}
        if eqs:
            row["equation_values"] = dict((e.GetName(), e.GetValue(c)) for e in eqs)
        cases.append(row)
    out = {"study": s.GetName(), "current_case": s.GetCurrentCase(), "num_cases": n, "parameters": params,
           "equations": dict((e.GetName(), {"expression": e.GetExpression(), "type": "value" if e.GetType() == 0 else "expression"})
                             for e in eqs),
           "cases": cases}
    if n > len(cases):
        out["truncated"] = "showing %d of %d cases (max_cases)" % (len(cases), n)
    if candidates is not None:
        allnames = list(t.AllParameterNames() or [])
        q = str(candidates).lower()
        hits = [x for x in allnames if q in x.lower()]
        out["candidates"] = {"filter": candidates, "count": len(hits), "names": hits[:150]}
    return out


@op(read_only=True)
def case_table_get(ctx, study, max_cases=200, candidates=None, model=None):
    """Design table: parameters, cases (values, active, has_result), equations; candidates=<filter> lists addable names."""
    s = ctx.study(study, model)
    return _case_table_dump(ctx, s, max_cases, candidates)


def _set_case_values(t, case, values, report):
    applied = {}
    for pname, val in values.items():
        idx = _param_index(t, pname)
        if idx is None:
            raise OpError("parameter %r is not in the design table" % pname,
                          "add it first with add_parameters=[...]; parameters: %s"
                          % [p["name"] for p in _dt_params(t)])
        t.SetValue(int(case), idx, val)
        got = jsonable(t.GetValue(int(case), idx))
        row = {"parameter": t.ParameterName(idx), "set": val, "read": got}
        if not isinstance(val, str) and not _num_equal(val, got):
            row["mismatch"] = True
        applied[pname] = row
    report.append({"case": int(case), "applied": applied})


@op()
def case_table_set(ctx, study, add_parameters=None, cases=None, set_cases=None, remove_cases=None, active=None,
                   current_case=None, model=None):
    """Edit the design table: add parameters, append cases, change/remove cases, (de)activate, set current case."""
    s = ctx.study(study, model)
    t = _dt(s)
    report = {"added_parameters": [], "new_cases": [], "changed_cases": [], "removed_cases": [], "active": {}}
    # validate everything that can be validated before changing anything
    full = [(_resolve_addable(t, p) if _param_index(t, p) is None else None, p) for p in (add_parameters or [])]
    n0 = t.NumCases()
    for row in set_cases or []:
        c = int(row["case"])
        if c < 0 or c >= n0:
            raise OpError("case %s out of range (0..%d)" % (c, n0 - 1))
    for name, orig in full:
        if name is None:
            report["added_parameters"].append({"name": orig, "state": "already present"})
            continue
        before = t.NumParameters()
        t.AddParameter(name)
        if t.NumParameters() <= before:
            raise OpError("design table did not accept parameter %r" % name, "check jmag_messages", data=report)
        report["added_parameters"].append({"name": name, "state": "added", "index": t.NumParameters() - 1})
    for values in cases or []:
        missing = [k for k in values if _param_index(t, k) is None]
        if missing:
            raise OpError("unknown parameter(s) %s in a new case" % missing,
                          "add them with add_parameters; parameters: %s" % [p["name"] for p in _dt_params(t)],
                          data=report)
    for values in cases or []:
        before = t.NumCases()
        t.AddCases(1)
        if t.NumCases() != before + 1:
            raise OpError("AddCases(1) did not add a case", data=report)
        _set_case_values(t, before, values, report["new_cases"])
    for row in set_cases or []:
        _set_case_values(t, int(row["case"]), row.get("values") or {}, report["changed_cases"])
    for c in sorted(set(int(x) for x in (remove_cases or [])), reverse=True):
        before = t.NumCases()
        if c < 0 or c >= before:
            raise OpError("case %s out of range (0..%d)" % (c, before - 1), data=report)
        t.RemoveCase(c)
        if t.NumCases() != before - 1:
            raise OpError("case %s was not removed (case 0 is the base case and may not be removable)" % c, data=report)
        report["removed_cases"].append(c)
    for k, v in (active or {}).items():
        c = int(k)
        t.SetActive(c, 1 if v else 0)
        report["active"][str(c)] = bool(t.IsActive(c))
    if current_case is not None:
        s.SetCurrentCase(int(current_case))
        report["current_case"] = s.GetCurrentCase()
    out = _case_table_dump(ctx, s)
    out["applied"] = report
    return out


def _calc_folder_of(s):
    try:
        return s.GetReport().GetCalculationFolder()
    except Exception:
        return None


def _write_progress(path, data):
    if not path:
        return
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(jsonable(data)))
    os.replace(tmp, path)


@op()
def run_cases(ctx, study, cases=None, overwrite=True, method="loop", progress_file=None, cancel_file=None, model=None):
    """Solve design-table cases (all or a subset) in the foreground of this worker.

    method='loop' (default): SetCurrentCase(k) + Run() per case; writes progress_file (JSON) before each case and
    stops starting new cases once cancel_file exists. method='all_cases': activates exactly the chosen cases and
    calls Study.RunAllCases() once (activity restored afterwards). method='optimization': Study.RunOptimization()."""
    s = ctx.study(study, model)
    t = _dt(s)
    n = t.NumCases()
    sel = list(range(n)) if cases is None else [int(c) for c in cases]
    bad = [c for c in sel if c < 0 or c >= n]
    if bad:
        raise OpError("case(s) %s out of range; study has %d case(s) (0..%d)" % (bad, n, n - 1))
    if method not in ("loop", "all_cases", "optimization"):
        raise OpError("unknown method %r" % method, "loop | all_cases | optimization")
    if ctx.app.HasRunningProcess():
        raise OpError("JMAG is already running an analysis", "wait or cancel it (jmag_cancel_run)")
    prev = s.GetCurrentCase()
    rows = []
    t_all = time.time()
    state = {"state": "running", "study": s.GetName(), "method": method, "cases": sel, "done": rows,
             "current_case": None, "folder": None, "started": t_all}
    if method == "optimization":
        state["folder"] = _calc_folder_of(s)
        _write_progress(progress_file, state)
        s.RunOptimization()
        n2 = t.NumCases()
        rows.extend({"case": c, "has_result": bool(s.CaseHasResult(c))} for c in range(n2))
        out = {"study": s.GetName(), "method": method, "cases_before": n, "cases_after": n2, "cases": rows,
               "seconds": round(time.time() - t_all, 2)}
        state.update(state="finished")
        _write_progress(progress_file, state)
        return out
    if method == "all_cases":
        old = [bool(t.IsActive(c)) for c in range(n)]
        for c in range(n):
            t.SetActive(c, 1 if c in sel else 0)
        for c in sel:
            if s.CaseHasResult(c):
                if not overwrite:
                    raise OpError("case %s already has results" % c, "pass overwrite=true or choose other cases")
                s.DeleteResultCase(c)
        state["folder"] = _calc_folder_of(s)
        _write_progress(progress_file, state)
        try:
            s.RunAllCases()
        finally:
            for c in range(n):
                t.SetActive(c, 1 if old[c] else 0)
        for c in sel:
            rows.append({"case": c, "state": "finished" if s.CaseHasResult(c) else "failed"})
    else:
        try:
            for k, c in enumerate(sel):
                if cancel_file and os.path.exists(cancel_file):
                    rows.append({"case": c, "state": "cancelled"})
                    continue
                s.SetCurrentCase(c)
                if s.CaseHasResult(c):
                    if not overwrite:
                        rows.append({"case": c, "state": "skipped", "reason": "has results (overwrite=false)"})
                        continue
                    s.DeleteResultCurrentCase()
                folder = _calc_folder_of(s)
                state.update(current_case=c, index=k, folder=folder)
                _write_progress(progress_file, state)
                t0 = time.time()
                s.Run()
                row = {"case": c, "state": "finished" if s.CaseHasResult(c) else "failed",
                       "seconds": round(time.time() - t0, 2), "calc_folder": folder}
                row.update(_report_messages(s))
                rows.append(row)
                _write_progress(progress_file, state)
        finally:
            if s.GetCurrentCase() != prev:
                s.SetCurrentCase(prev)
    state.update(state="finished", current_case=None)
    _write_progress(progress_file, state)
    out = {"study": s.GetName(), "method": method, "cases": rows, "seconds": round(time.time() - t_all, 2),
           "finished": [r["case"] for r in rows if r.get("state") == "finished"],
           "failed": [r["case"] for r in rows if r.get("state") == "failed"]}
    if sel and not out["finished"] and not any(r.get("state") in ("skipped", "cancelled") for r in rows):
        raise OpError("no case produced results", "read the per-case errors below and the solver log", data=out)
    return out


# --------------------------------------------------------------------------- iron-loss study

def _loss_material_report(ctx, s, m, parts):
    """Materials behind the loss parts. Verified on 23.0: GetValue on a library steel's Loss_* properties
    logs 'The property should be a flag or real property' (library materials are protected), so the loss data
    itself is not read; a part whose loss comes out exactly 0 W after the run is the tell-tale of missing data."""
    out = {}
    for p in parts:
        mat = s.GetMaterial(p)
        out[p] = {"material": mat.GetName() if mat is not None and mat.IsValid() else None}
    return out


@op()
def create_loss_study(ctx, study, name, parts=None, frequency_hz=None, reference_steps=None, properties=None,
                      study_properties=None, replace=False, model=None):
    """Iron-loss study from a solved Transient study (Study.CreateLossStudy) + its Ironloss condition."""
    s = ctx.study(study, model)
    m = ctx.model_of(s)
    stype = s.GetScriptTypeName()
    if not (stype.startswith("Transient") or stype.startswith("Frequency")):
        raise OpError("iron-loss studies are derived from a Transient (or Frequency) study, not %s" % stype,
                      "create it from the solved Transient2D/Transient study")
    existing = [m.GetStudy(j).GetName() for j in range(m.NumStudies())]
    if name in existing:
        if not replace:
            raise OpError("study %r already exists" % name, "pass replace=true or choose another name")
        m.DeleteStudy(name)
    ls = need(s.CreateLossStudy(name), "loss study %r" % name)
    cond = None
    for i in range(ls.NumConditions()):
        c = ls.GetCondition(i)
        if c.GetScriptTypeName() == "Ironloss":
            cond = c
            break
    if cond is None:  # 23.0 creates 'untitled 1' (Ironloss) by itself; create one if a later version does not
        _check_type(ls, "Ironloss")
        cond = need(ls.CreateCondition("Ironloss", "IronLoss"), "Ironloss condition")
    vals = {}
    if frequency_hz is not None:
        vals["BasicFrequency"] = float(frequency_hz)
    if reference_steps:
        a, b = int(reference_steps[0]), int(reference_steps[1])
        vals.update({"UseStartReferenceStep": 1, "StartReferenceStep": a, "UseEndReferenceStep": 1, "EndReferenceStep": b})
    vals.update(properties or {})
    applied = set_props(cond, vals) if vals else {}
    sel = select_parts(cond, m, parts) if parts else []
    sp_applied = set_props(ls.GetStudyProperties(), study_properties) if study_properties else {}
    out = {"study": ls.GetName(), "type": ls.GetScriptTypeName(), "source_study": s.GetName(),
           "source_has_result": bool(s.HasResult()), "condition": _cond_summary(ls, cond), "applied": applied,
           "parts": sel, "study_properties_applied": sp_applied,
           "study_properties": get_props(ls.GetStudyProperties()),
           "condition_types": list(ls.GetConditionTypes() or [])}
    out["materials"] = _loss_material_report(ctx, s, m, sel)
    hints = []
    if not sel:
        hints.append("no parts selected: pass parts=[iron cores] (Ironloss condition starts empty)")
    bf = read_prop(cond, "BasicFrequency")[1]
    if (bf is None or _num_equal(bf, 0.0)) and frequency_hz is None:
        hints.append("BasicFrequency is 0: pass frequency_hz (electrical frequency) or the solver aborts with "
                     "'Frequency is 0[Hz]'")
    if not s.HasResult():
        hints.append("the source study has no results yet: run it first, then run this loss study")
    out["next"] = "; ".join(hints) if hints else "run it with jmag_run(study=%r) (seconds), then jmag_loss_study(action='results')" % name
    return out


@op(read_only=True)
def loss_results(ctx, study, case=None, tables=None, include_spectrum=False, model=None):
    """Loss tables (names containing 'Loss') of a solved loss study: total per part and overall."""
    s = ctx.study(study, model)
    _need_result(s, case)
    out = []
    with _CaseScope(ctx, s, case):
        _, rows = _tables(s)
        want = [r for r in rows if "loss" in r["name"].lower()]
        if tables:
            keys = [str(x).lower() for x in tables]
            want = [r for r in rows if r["name"].lower() in keys or r["title"].lower() in keys]
        for r in want:
            res = _read_table(s, r["index"])
            # Verified on 23.0: row 0 ('Step 0', 0 Hz) holds the total; rows 1.. are the frequency components
            # (their sum equals row 0 for Joule loss; hysteresis is only in row 0). Iron = Hysteresis + Joule.
            # Tables without '(Each Component)' carry an extra 'Total' column.
            cols = res["columns"]
            totals = dict((c, float(v[0])) for c, v in cols.items() if v and c != "Total")
            nz = dict((c, v) for c, v in totals.items() if abs(v) > 0)
            total = float(cols["Total"][0]) if "Total" in cols and cols["Total"] else float(sum(totals.values()))
            row = {"table": res["table"], "unit": res["unit"], "x_name": res["x_name"], "x_unit": res["x_unit"],
                   "rows": len(res["x"]), "total": total,
                   "per_part": dict(sorted(nz.items(), key=lambda kv: -abs(kv[1])))}
            spec = dict((c, float(sum(v[1:]))) for c, v in cols.items() if c in nz and len(v) > 1)
            if any(abs(v) > 0 for v in spec.values()):
                row["spectrum_sum_rows_1_on"] = spec
            if include_spectrum:
                row["x"] = res["x"]
                row["columns"] = dict((c, v) for c, v in res["columns"].items() if c in nz)
            out.append(row)
    if not out:
        raise OpError("no loss tables in study %r" % s.GetName(),
                      "tables: %s. Loss tables exist in a solved loss study (jmag_loss_study)" % [r["name"] for r in rows])
    return {"study": s.GetName(), "case": case, "tables": out,
            "note": "total/per_part = row 0 (the total, labelled 0 Hz); rows 1.. are frequency components (x in Hz)"}


# --------------------------------------------------------------------------- efficiency map

#: user key -> (setter, getter or None) on EfficiencyMapDefinition (names from the 23.0 API docs)
EFF_MAP_SETTINGS = {
    "table": ("SetTable", "TableName"),
    "max_voltage": ("SetMaxVoltage", "MaxVoltage"),
    "max_current": ("SetMaxCurrent", "MaxCurrent"),
    "max_field_current": ("SetMaxFieldCurrent", "MaxFieldCurrent"),
    "voltage_limit_type": ("SetVoltageLimitType", "VoltageLimitType"),
    "motor_control_type": ("SetMotorControlType", "MotorControlType"),
    "control": ("SetControlType", "ControlName"),
    "mechanical_loss_factor": ("SetMechancialLossFactor", "MechancialLossFactor"),
    "correction_type": ("SetCorrectionType", "CorrectionType"),
    "motor_mode": ("SetMotorMode", "MotorMode"),
    "torque_type": ("SetTorqueType", "TorqueType"),
    "speed_divisions": ("SetXAxisDivisions", "XAxisDivisions"),
    "torque_divisions": ("SetYAxisDivisions", "YAxisDivisions"),
    "add_pwm_iron_loss": ("SetAddPWMIronLoss", "AddPWMIronLoss"),
    "dc_bus_voltage": ("SetDCBusVoltage", "DCBusVoltage"),
    "carrier_frequency": ("SetCarrierFrequency", "CarrierFrequency"),
    "add_magnet_loss": ("SetAddMagnetLoss", "GetAddMagnetLoss"),
    "add_ac_loss": ("SetAddACLoss", "AddACLoss"),
    "extrapolation": ("SetExtrapolation", "GetExtrapolation"),
    "cluster_points": ("SetClusterPoints", "ClusterPoints"),
    "loss_correction": ("SetLossCorrection", "GetLossCorrection"),
    "coil_temperature_correction": ("SetCoilTemperatureCorrection", None),
    "magnet_temperature_correction": ("SetMagnetTemperatureCorrection", None),
    "magnet_correction_base_temperature": ("SetMagnetCorrectionBaseTemperature", None),
}


def _eff_map_find(s, name):
    for i in range(s.NumEfficiencyMapDefinitions()):
        d = s.GetEfficiencyMapDefinition(i)
        if d is not None and d.IsValid() and d.Name() == name:
            return d
    return None


def _eff_map_state(d):
    out = {}
    for key, (_, getter) in sorted(EFF_MAP_SETTINGS.items()):
        if getter and hasattr(d, getter):
            try:
                out[key] = jsonable(getattr(d, getter)())
            except Exception as e:
                out[key] = "unreadable: %s" % e
    for key, getter in (("magnet_temperature_initial", "GetInitialMagnetTemperature"),
                        ("magnet_temperature_end", "GetEndMagnetTemperature")):
        try:
            out[key] = getattr(d, getter)()
        except Exception:
            pass
    return out


@op()
def efficiency_map_create(ctx, study, name, settings=None, magnet_temperatures=None, model=None):
    """Create (or update) an EfficiencyMapDefinition and apply settings with read-back."""
    s = ctx.study(study, model)
    settings = settings or {}
    unknown = [k for k in settings if k not in EFF_MAP_SETTINGS]
    if unknown:
        raise OpError("unknown efficiency-map setting(s) %s" % unknown,
                      "valid: %s; closest: %s" % (sorted(EFF_MAP_SETTINGS),
                                                 dict((k, close_matches(k, EFF_MAP_SETTINGS)) for k in unknown)))
    d = _eff_map_find(s, name)
    created = d is None
    if d is None:
        d = s.CreateEfficiencyMapDefinition(name)
        need(d, "efficiency map definition %r" % name,
             "JMAG builds efficiency maps in an efficiency-map (response) study; %s is a %s study. Create the map "
             "study in the JMAG GUI or from a Transient study with the Efficiency Map wizard, then pass its name"
             % (s.GetName(), s.GetScriptTypeName()))
    applied = {}
    for key, val in settings.items():
        setter, getter = EFF_MAP_SETTINGS[key]
        if not hasattr(d, setter):
            applied[key] = {"set": val, "error": "this JMAG has no %s" % setter}
            continue
        getattr(d, setter)(val)
        row = {"set": val}
        if getter and hasattr(d, getter):
            got = jsonable(getattr(d, getter)())
            row["read"] = got
            if not _num_equal(val, got) and str(val) != str(got):
                row["mismatch"] = True
        else:
            row["read"] = "no getter in the API"
        applied[key] = row
    if magnet_temperatures:
        mt = magnet_temperatures
        d.SetMagnetCorrection(float(mt["initial"]), float(mt["end"]), int(mt.get("divisions", 1)))
        applied["magnet_temperatures"] = {"set": mt, "read": [d.GetInitialMagnetTemperature(), d.GetEndMagnetTemperature()]}
    return {"study": s.GetName(), "map": d.Name(), "created": created, "applied": applied, "state": _eff_map_state(d),
            "next": "solve the map study, then jmag_efficiency_map(action='results')"}


@op()
def efficiency_map_results(ctx, study, name=None, out_dir=None, result_types=None, points=None, image=False, model=None):
    """Read an efficiency map plot: result types, axis ranges; optional CSV/PNG export and point queries.
    Not read-only: ExportData/SaveMapImage write files."""
    s = ctx.study(study, model)
    n = s.NumEfficiencyMapDefinitions()
    names = [s.GetEfficiencyMapDefinition(i).Name() for i in range(n)]
    if not names:
        raise OpError("study %r has no efficiency map definitions" % s.GetName(),
                      "create one with jmag_efficiency_map(action='create') in an efficiency-map study")
    name = name or names[0]
    if name not in names:
        raise OpError("efficiency map %r not found" % name, "maps: %s" % names)
    plot = need(s.GetEfficiencyMapPlot(name), "efficiency map plot %r" % name, "solve the map study first")
    types = []
    for i in range(plot.NumResults()):
        types.append({"index": i, "type": plot.GetResultType(i), "name": plot.GetResultTypeName(i)})
    out = {"study": s.GetName(), "map": name, "result_types": types,
           "speed_range": [plot.GetResultMinXValue(), plot.GetResultMaxXValue(), plot.GetResultXDivisions()],
           "torque_range": [plot.GetResultMinYValue(), plot.GetResultMaxYValue(), plot.GetResultYDivisions()],
           "definition": _eff_map_state(s.GetEfficiencyMapDefinition(names.index(name)))}
    wanted = result_types or [t["name"] for t in types]
    if out_dir:
        out_dir = os.path.abspath(out_dir)
        os.makedirs(out_dir, exist_ok=True)
        files = []
        for rtype in wanted:
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(rtype)).strip("_")
            p = os.path.join(out_dir, "%s__%s.csv" % (re.sub(r"[^A-Za-z0-9_.-]+", "_", name), safe))
            plot.ExportData(rtype, p)
            row = {"type": rtype, "csv": p, "written": os.path.isfile(p)}
            if image:
                png = p[:-4] + ".png"
                row["png_ok"] = bool(plot.SaveMapImage(rtype, png, 1200, 900))
                row["png"] = png
            files.append(row)
        out["files"] = files
    if points:
        vals = []
        for xy in points:
            x, y = float(xy[0]), float(xy[1])
            row = {"speed": x, "torque": y, "valid": bool(plot.IsValidPoint(x, y))}
            if row["valid"]:
                row["values"] = dict((rt, plot.GetPointResult(rt, x, y)) for rt in wanted)
            vals.append(row)
        out["points"] = vals
    return out


# --------------------------------------------------------------------------- winding

def _coil_direction(c):
    return "up" if direction_flag(c.get("direction", "up")) == 0 else "down"


def _coil_part(c, slot_parts):
    if c.get("part"):
        return c["part"]
    if slot_parts:
        for key in ("%s/%s" % (c.get("slot"), c.get("layer")), str(c.get("slot"))):
            if key in slot_parts:
                return slot_parts[key]
    return None


@op()
def winding_apply(ctx, study, coils, mode="fem_coil", name="Phase", components=None, slot_parts=None,
                  phase_turns=None, slots=None, poles=None, layers=None, winding_type="ThreePhase",
                  reference_slot_parts=None, replace=False, force=False, model=None):
    """Apply a coil list [{slot, phase, direction, layer, turns, part?}] to a study.

    mode='fem_coil' (verified path): one FEMCoil condition per phase linked to circuit component
    components[phase] (default 'Coil<phase>'), sides grouped by direction; Turn = phase_turns[phase] or
    sum(coil turns)/2 (every turn has a go and a return side; assumes all coils of a phase in series).
    mode='jmag_winding': JMAG's own winding definition (WindingRegion + WindingDefinition, manual coil table)."""
    s = ctx.study(study, model)
    if isinstance(coils, dict):  # labels-JSON form {"U": {"up": [parts], "down": [parts]}, ...}
        coils = [{"phase": ph, "direction": d, "part": part}
                 for ph, sides in coils.items() for d in ("up", "down") for part in (sides.get(d) or [])]
    if not coils:
        raise OpError("coils is empty", "pass slot_map (or labels_coils) from jmag_winding_plan")
    pairs_given = all("go_slot" in c and "return_slot" in c for c in coils)
    if pairs_given and mode == "fem_coil":
        raise OpError("coils look like a coil_list (go_slot/return_slot)",
                      "mode='fem_coil' needs per-side entries with parts: pass slot_map or labels_coils")
    phases = []
    groups = {}
    turns = {}
    owner = {}
    for i, c in enumerate(coils):
        if "phase" not in c:
            raise OpError("coil %d has no phase" % i)
        ph = str(c["phase"])
        if ph not in groups:
            phases.append(ph)
            groups[ph] = {"up": [], "down": []}
            turns[ph] = 0.0
        d = "up" if pairs_given else _coil_direction(c)
        if mode == "fem_coil":
            part = _coil_part(c, slot_parts)
            if part is None:
                raise OpError("coil %d (slot %s, layer %s) has no part" % (i, c.get("slot"), c.get("layer")),
                              "give coils[i].part or slot_parts={'1': 'Slot01_U+'} ('1/2' = slot 1, layer 2)")
            tag = ph + ("+" if d == "up" else "-")
            if owner.get(part, tag) != tag:
                raise OpError("part %r is used by %s and %s" % (part, owner[part], tag),
                              "a part carries one phase and direction; draw one part per layer for double-layer windings")
            owner[part] = tag
            if part not in groups[ph][d]:
                groups[ph][d].append(part)
        else:
            groups[ph][d].append(c)
        if c.get("turns") is not None:
            turns[ph] += float(c["turns"])
    if mode == "fem_coil":
        circ = _get_circuit(s, create=False)
        have = _comp_names(circ)
        out = {"mode": mode, "phases": {}}
        for ph in phases:
            comp = (components or {}).get(ph) or "Coil%s" % ph
            if comp not in have:
                raise OpError("circuit has no component %r for phase %s" % (comp, ph),
                              "components: %s. Build the circuit first (jmag_circuit_quick) or pass components={'%s': ...}"
                              % (have, ph))
            sides = [{"parts": groups[ph][d], "direction": d} for d in ("up", "down") if groups[ph][d]]
            n_turns = (phase_turns or {}).get(ph)
            if n_turns is None and turns[ph] > 0:
                n_turns = turns[ph] / 2.0
            res = _fem_group(ctx, study, "FEMCoil", "FEMCoilData", "%s%s" % (name, ph), comp, sides,
                             {"Turn": n_turns} if n_turns else None, replace, model)
            out["phases"][ph] = {"condition": "%s%s" % (name, ph), "component": comp, "turns": n_turns,
                                 "up": groups[ph]["up"], "down": groups[ph]["down"], "summary": res["condition"]}
        return out
    if mode != "jmag_winding":
        raise OpError("unknown mode %r" % mode, "fem_coil | jmag_winding")
    if not force:
        raise OpError("mode='jmag_winding' crashed JMAG-Designer 23.0 in the bridge's live test (winding region + "
                      "manual winding definition on a 2D SPM model); refused by default",
                      "use mode='fem_coil' (verified). Pass force=true only on a saved project you can reopen")
    if not (slots and poles):
        raise OpError("mode='jmag_winding' needs slots and poles")
    m = ctx.model_of(s)
    if replace:
        for i in range(s.NumWindings() - 1, -1, -1):
            if s.GetWinding(i).GetName() == name:
                s.DeleteWinding(i)
    reg = need(s.CreateWindingRegion("%sRegion" % name, 0), "winding region")
    reg.SetSlots(int(slots))
    reg.SetPoles(int(poles))
    for layer, plist in (reference_slot_parts or {}).items():
        for p in plist:
            reg.AddPart(int(layer), resolve_part(m, p))
    wd = need(s.CreateWinding(winding_type, name), "winding definition (%s)" % winding_type)
    wd.SetRegion(reg.GetName())
    wd.SetWindingScheme("Manual")
    if layers:
        wd.SetLayers(int(layers))
    tables = {}
    for k, ph in enumerate(phases):
        if pairs_given:
            pairs = [[int(c["go_slot"]), int(c["return_slot"])] for c in groups[ph]["up"]]
        else:
            ups = sorted(groups[ph]["up"], key=lambda c: int(c.get("slot", 0)))
            downs = sorted(groups[ph]["down"], key=lambda c: int(c.get("slot", 0)))
            if len(ups) != len(downs):
                raise OpError("phase %s has %d go and %d return sides" % (ph, len(ups), len(downs)),
                              "manual winding tables pair each go slot with a return slot; pass coil_list")
            pairs = [[int(a["slot"]), int(b["slot"])] for a, b in zip(ups, downs)]
        wd.SetCoilTable(k, pairs)
        tables[ph] = {"set": pairs, "read": jsonable(wd.GetCoilTable(k))}
    return {"mode": mode, "winding": wd.GetName(), "type": wd.WindingType(), "region": wd.Region(),
            "slots": wd.Slots(), "poles": wd.Poles(), "layers": wd.Layers(), "scheme": wd.WindingScheme(),
            "coil_tables": tables, "num_coils": wd.NumCoils(), "uses_winding": bool(s.HasUseWinding())}


# --------------------------------------------------------------------------- table export / case report

EXPORT_KINDS = ("all_tables", "all_cases", "table", "field", "field_values", "field_minmax")


@op()
def export_table(ctx, study, path, kind="all_tables", table=None, result_type=None, component=None, steps=None,
                 axis="Time", filetype=0, case=None, overwrite=True, force=False, model=None):
    """Export result tables with JMAG's own writers (CSV/TXT/HTM by extension).

    Verified on 23.0: all_tables, all_cases, table. kind='field' returned False with 'There is no selected item.
    (Study::ExportTable)' (it needs a GUI selection the API does not set); the probe sequence ExportTable ->
    ExportResultValuesTable crashed Designer, so field_values/field_minmax are refused unless force=True."""
    s = ctx.study(study, model)
    if kind not in EXPORT_KINDS:
        raise OpError("unknown kind %r" % kind, "valid: %s" % (EXPORT_KINDS,))
    if kind in ("field_values", "field_minmax") and not force:
        raise OpError("kind=%r is suspected of crashing JMAG-Designer 23.0 (seen once in the bridge's live probe)" % kind,
                      "use kind='all_tables'/'table', jmag_export_results, or a probe; force=true on a saved project")
    path = os.path.abspath(path)
    if os.path.splitext(path)[1].lower() not in (".csv", ".txt", ".htm", ".html"):
        raise OpError("path must end with .csv, .txt or .htm", "JMAG picks the format from the extension")
    if os.path.exists(path):
        if not overwrite:
            raise OpError("%s exists" % path, "pass overwrite=true")
        os.remove(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _need_result(s, case)
    ok = None
    with _CaseScope(ctx, s, case):
        if kind == "all_tables":
            s.GetResultTable().WriteAllTables(path, axis)
        elif kind == "all_cases":
            s.GetResultTable().WriteAllCaseTables(path, axis)
        elif kind == "table":
            if table is None:
                raise OpError("kind='table' needs table=<name>", "names: jmag_list_results")
            d, _ = _table_data(s, table)
            d.WriteTable(path, axis)
        else:
            td = need(s.CreateTableDefinition(), "table definition")
            types = list(td.GetResultTypeNames() or [])
            if result_type not in types:
                raise OpError("result_type %r not available" % (result_type,),
                              "valid: %s; closest: %s" % (types, close_matches(result_type, types)))
            td.SetResultType(result_type, "")
            comps = list(td.GetComponentNames() or [])
            if component:
                if comps and component not in comps:
                    raise OpError("component %r invalid for %s" % (component, result_type), "valid: %s" % comps)
                td.SetComponent(component)
            if steps is None or steps == "all":
                td.SetAllSteps()
            else:
                td.SetStepsByString(str(steps))
            if kind == "field":
                ok = s.ExportTable(td, path, filetype)
            elif kind == "field_values":
                ok = s.ExportResultValuesTable(td, path)
            else:
                ok = s.ExportMinMaxTable(td, path)
    if not os.path.isfile(path):
        hint = "check jmag_messages"
        if kind == "field":
            hint = ("field tables need parts selected in the JMAG GUI ('There is no selected item'); use "
                    "jmag_contour_image for field values or kind='all_tables' for result tables")
        raise OpError("JMAG did not write %s (returned %r)" % (path, ok), hint)
    head = []
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for _ in range(4):
            line = f.readline()
            if not line:
                break
            head.append(line.rstrip()[:300])
    return {"path": path, "kind": kind, "bytes": os.path.getsize(path), "returned": ok, "head": head}


@op()
def write_case_report(ctx, study, path, case=None, model=None):
    """Study.WriteCaseReport(path): JMAG's case report (HTML/CSV by extension)."""
    s = ctx.study(study, model)
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = set(os.listdir(os.path.dirname(path)))
    with _CaseScope(ctx, s, case):
        ok = s.WriteCaseReport(path)
    new = sorted(set(os.listdir(os.path.dirname(path))) - before)
    if not os.path.exists(path) and not new:
        raise OpError("no report was written (WriteCaseReport returned %r)" % ok, "check jmag_messages")
    return {"path": path, "returned": ok, "exists": os.path.exists(path), "new_files": new[:50],
            "bytes": os.path.getsize(path) if os.path.isfile(path) else None}


# --------------------------------------------------------------------------- optimisation table

_ITEM_GETTERS = ("GetName", "GetParameterName", "GetItemName", "GetTitle", "GetExpression", "GetType", "GetValue",
                 "GetWeight", "GetMin", "GetMax", "IsExpression")


def _item_dump(obj):
    row = {}
    for g in _ITEM_GETTERS:
        if hasattr(obj, g):
            try:
                row[g[3:] if g.startswith("Get") else g] = jsonable(getattr(obj, g)())
            except Exception:
                pass
    return row


def _opt_dump(ot):
    out = {"parameters": [], "objectives": [], "constraints": []}
    for i in range(ot.NumParameters()):
        out["parameters"].append(_item_dump(ot.GetParametricItem(i)))
    for i in range(ot.NumObjectives()):
        out["objectives"].append(_item_dump(ot.GetObjectiveItem(i)))
    for i in range(ot.NumExpressions()):
        out["constraints"].append(_item_dump(ot.GetExpressionItem(i)))
    for key, getter in (("csv_output_path", "CSVOutputPath"), ("immediate_csv_output", "IsImmediateCSVOutput"),
                        ("stop_on_failed_case", "StopOnFailedCase"), ("num_jobs", "NumJobs"),
                        ("simultaneous_studies", "NumSimultaneousStudies"), ("create_initial_cases", "CreateInitialCases")):
        try:
            out[key] = jsonable(getattr(ot, getter)())
        except Exception:
            pass
    return out


@op(read_only=True)
def optimization_get(ctx, study, model=None):
    """Optimisation table: design-variable ranges, objectives, constraints, CSV output settings."""
    s = ctx.study(study, model)
    ot = need(s.GetOptimizationTable(), "optimization table")
    out = _opt_dump(ot)
    out["study"] = s.GetName()
    return out


@op()
def optimization_set(ctx, study, parameters=None, objectives=None, constraints=None, csv_output_path=None,
                     immediate_csv_output=None, stop_on_failed_case=None, settings=None, model=None):
    """Add design-variable ranges / objectives / constraints and output settings (each read back)."""
    s = ctx.study(study, model)
    ot = need(s.GetOptimizationTable(), "optimization table")
    t = _dt(s)
    applied = {"parameters": [], "objectives": [], "constraints": []}
    for p in parameters or []:
        idx = _param_index(t, p["name"])
        if idx is None:
            raise OpError("%r is not a design-table parameter" % p["name"],
                          "add it with jmag_case_table(add_parameters=[...]) first; parameters: %s"
                          % [x["name"] for x in _dt_params(t)])
        pname = t.ParameterName(idx)
        before = ot.NumParameters()
        ot.AddParametricItem(pname)
        item = need(ot.GetParametricItem(ot.NumParameters() - 1 if ot.NumParameters() > before else pname),
                    "parametric range %r" % pname)
        if "min" in p:
            item.SetMin(float(p["min"]))
        if "max" in p:
            item.SetMax(float(p["max"]))
        applied["parameters"].append(_item_dump(item))
    for kind, adder, getter, counter in (("objectives", "AddObjectiveItem", "GetObjectiveItem", "NumObjectives"),
                                         ("constraints", "AddExpressionItem", "GetExpressionItem", "NumExpressions")):
        for o in (objectives if kind == "objectives" else constraints) or []:
            getattr(ot, adder)(o["name"])
            item = need(getattr(ot, getter)(getattr(ot, counter)() - 1), "%s item %r" % (kind, o["name"]))
            if "title" in o:
                item.SetTitle(o["title"])
            if "expression" in o:
                item.SetExpression(o["expression"])
            if "type" in o:
                item.SetType(o["type"])
            if "value" in o:
                item.SetValue(float(o["value"]))
            if "weight" in o:
                item.SetWeight(float(o["weight"]))
            applied[kind].append(_item_dump(item))
    if csv_output_path:
        ot.SetCSVOutputPath(os.path.abspath(csv_output_path))
    if immediate_csv_output is not None:
        ot.SetImmediateCSVOutput(bool(immediate_csv_output))
    if stop_on_failed_case is not None:
        ot.SetStopOnFailedCase(bool(stop_on_failed_case))
    for k, v in (settings or {}).items():
        ot.SetParameter(k, v)
        applied.setdefault("settings", {})[k] = {"set": v, "read": jsonable(ot.GetParameter(k))}
    out = _opt_dump(ot)
    out["applied"] = applied
    out["study"] = s.GetName()
    out["next"] = "start it with jmag_run_cases(study, method='optimization') (Study.RunOptimization in the runner)"
    return out


# --------------------------------------------------------------------------- experiment setup

@op()
def experiment_setup(ctx, study, zero_current=False, current_peak=None, phase_u_deg=None, frequency=None, rpm=None,
                     steps=None, time_step=None, source=None, motion=None, model=None):
    """One round trip for jmag_experiment: current source amplitude/frequency/phase, rotor speed, step control.

    3-phase current sources: properties Amplitude [A peak], Frequency [Hz], PhaseU [deg] (verified names);
    single current sources get a new sin function. Speed: AngularVelocity [rpm] of the RotationMotion condition."""
    s = ctx.study(study, model)
    rep = {}
    if zero_current or current_peak is not None or frequency is not None or phase_u_deg is not None:
        if not s.HasCircuit():
            raise OpError("study has no circuit", "experiments drive the machine through its circuit current source")
        c = s.GetCircuit()
        comps = [c.GetComponent(i) for i in range(c.NumComponents())]
        if source:
            srcs = [x for x in comps if x.GetName() == source]
            if not srcs:
                raise OpError("component %r not found" % source, "components: %s" % _comp_names(c))
        else:
            # GetType() returns display names on 23.0, e.g. 'Three-phase Current Source' (verified)
            srcs = [x for x in comps if "currentsource" in str(x.GetType()).lower().replace(" ", "")]
        if not srcs:
            vs = [x.GetName() for x in comps if "voltagesource" in str(x.GetType()).lower().replace(" ", "")]
            raise OpError("no current source in the circuit",
                          "voltage-driven models (%s) cannot impose a current; build the experiment copy with "
                          "jmag_circuit_quick(topology='three_phase_current') or pass source=<name>" % vs if vs else
                          "add one with jmag_circuit_quick")
        for src in srcs:
            names = list(src.GetPropertyNames() or [])
            if "Amplitude" in names:
                vals = {}
                if zero_current:
                    vals["Amplitude"] = 0.0
                elif current_peak is not None:
                    vals["Amplitude"] = float(current_peak)
                if frequency is not None and "Frequency" in names:
                    vals["Frequency"] = float(frequency)
                if phase_u_deg is not None and "PhaseU" in names:
                    vals["PhaseU"] = float(phase_u_deg)
                rep[src.GetName()] = set_props(src, vals)
            else:
                amp = 0.0 if zero_current else current_peak
                if amp is None:
                    raise OpError("current source %r has no Amplitude property; pass current_peak" % src.GetName())
                f = {"type": "sin", "amplitude": float(amp), "frequency": float(frequency or 50.0),
                     "phase": float(phase_u_deg or 0.0)}
                src.SetFunction(_function(ctx.need_app(), f))
                rep[src.GetName()] = {"function": f, "read": "functions have no read-back in the API"}
    if rpm is not None:
        conds = [s.GetCondition(i) for i in range(s.NumConditions())]
        mot = [x for x in conds if (x.GetName() == motion if motion else x.GetScriptTypeName().startswith("RotationMotion"))]
        if len(mot) != 1:
            raise OpError("need exactly one rotation-motion condition, found %s" % [x.GetName() for x in mot],
                          "pass motion=<condition name>; conditions: %s" % [x.GetName() for x in conds])
        rep["motion"] = {"condition": mot[0].GetName(), "applied": set_props(mot[0], {"AngularVelocity": float(rpm)})}
    if steps is not None or time_step is not None:
        rep["step"] = set_step(ctx, study, steps=steps, time_step=time_step, model=model)
    return rep


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
