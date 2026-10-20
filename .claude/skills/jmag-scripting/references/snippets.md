# Snippets for `jmag_exec_python`

Contents
- S1 Helpers: need(), find model/study by name without logging errors - VERIFIED
- S2 Safe property read - VERIFIED
- S3 Check the message log yourself - VERIFIED
- S4 Read a result table by internal key - VERIFIED
- S5 Solver report messages and element count - VERIFIED
- S6 Part at a point - VERIFIED
- S7 Study equations - VERIFIED
- S8 Design-table cases - VERIFIED
- S9 Create a loss study - VERIFIED
- S10 Efficiency-map definition - UNVERIFIED
- S11 Point probe of B - UNVERIFIED
- S12 Section graph along the air gap - UNVERIFIED
- S-3D-1 Import a STEP and rename parts by inside point - VERIFIED (3D, 2026-10-19)
- S-3D-2 3D `Current` condition with an inflow face - VERIFIED (3D, 2026-10-19)
- S-3D-3 `SymmetryBoundary` on faces picked by position - VERIFIED (3D, 2026-10-19)
- S-3D-4 3D mesh controls, part sizes and the energy table - VERIFIED (3D, 2026-10-19)
- S-3D-5 Read `CoilFlux` / `CoilCurrent` / `StoredEnergy` - VERIFIED (3D, 2026-10-19)

Rules for this file: every JMAG call is a member listed in the JMAG 23.0 script reference
inventory or already exercised by the bridge's `worker/ops.py`. **VERIFIED** = the same calls
run in `ops.py` against JMAG 23.0. **UNVERIFIED** = documented in the 23.0 reference but never
run through the bridge: use on a copy, read back, report what happened, and mark the result.

**VERIFIED (3D, 2026-10-19)** = run live through `jmag_exec_python` on JMAG-Designer 23.0 in two 3D
Static transformer studies whose inductances matched independent FE within 0.9 % (workflow and
traps: `jmag-simulation` -> `references/static-3d.md`).

Each snippet assumes S1 is pasted above it (the worker does not share state between calls).
Globals available: `app`, `designer`, `ctx`, `OpError`, `result`.

---

## S1 Helpers - VERIFIED

```python
def need(obj, what):
    try:
        ok = obj is not None and bool(obj.IsValid())
    except Exception:
        ok = False
    if not ok:
        raise OpError("%s not found or invalid" % what)
    return obj

def find_model(name):
    for i in range(app.NumModels()):
        m = app.GetModel(i)
        if m.GetName() == name:
            return m
    raise OpError("model %r not found" % name)

def find_study(model, name):
    for i in range(model.NumStudies()):
        s = model.GetStudy(i)
        if s.GetName() == name:
            return s
    raise OpError("study %r not found in %r" % (name, model.GetName()))

m = find_model("SPM")
s = find_study(m, "TR")
result = {"model": m.GetName(), "study": s.GetName(), "type": s.GetScriptTypeName()}
```

## S2 Safe property read - VERIFIED

`GetValue` on Integer / Vector / Table properties logs an error; gate on the type.

```python
def read_prop(obj, name):
    t = (obj.GetPropertyType(name) or "").lower()
    if t in ("flag", "double", "real", "bool", "boolean"):
        return obj.GetValue(name)
    if t == "string":
        return obj.GetStringValue(name)
    return None   # not readable as a scalar

props = need(s.GetStudyProperties(), "study properties")
result = {n: read_prop(props, n) for n in props.GetPropertyNames() if n in ("ModelThickness", "NonlinearMaxIteration")}
```

## S3 Check the message log yourself - VERIFIED

```python
n0 = len(app.GetAllMessages() or [])
# ... calls that might log ...
new = (app.GetAllMessages() or [])[n0:]
if app.HasError():
    app.ClearError()            # only if you handle the error here
    raise OpError("JMAG logged: %s" % list(new)[-3:])
result = {"new_messages": [str(x) for x in new]}
```

## S4 Read a result table by internal key - VERIFIED

```python
rt = s.GetResultTable()
d = rt.GetDataFromName("FEMCoilFlux")       # internal keys only: LineCurrent, TerminalVoltage, FEMCoilFlux ...
if d is None or not d.IsValid() or d.GetRows() == 0:
    raise OpError("empty table: wrong key or no results")
cols = [d.GetColName(j) for j in range(d.GetCols())]
n = d.GetRows()
result = {"cols": cols, "t": [d.GetTime(k) for k in range(n)],
          "first_col": [d.GetValue(k, cols[0]) for k in range(n)]}
```

## S5 Solver report messages and element count - VERIFIED

```python
r = s.GetReport()
result = {"errors": [r.GetErrorMessage(i).GetText() for i in range(r.NumErrorMessages())],
          "warnings": [r.GetWarningMessage(i).GetText() for i in range(r.NumWarningMessages())],
          "elements": r.NumElements(), "has_result": bool(s.HasResult())}
```

## S6 Part at a point - VERIFIED

```python
p = m.GetPartByPosition(app.CreatePoint(38.0, 0.0, 0.0))
name = p.GetName() if p is not None and p.IsValid() else None
if name is None:
    app.ClearError()            # a miss logs an error; we handled it
result = {"part": name}
```

## S7 Study equations - VERIFIED

(Prefer `jmag_set_equations` / `jmag_get_equations`.)

```python
t = need(s.GetDesignTable(), "design table")
if not t.HasEquation("I"):
    t.AddEquation("I")
eq = need(t.GetEquation("I"), "equation I")
eq.SetType(0)                   # 0 value, 1 expression
eq.SetExpression("10")
result = {"I": eq.GetValue()}
```

## S8 Design-table cases - VERIFIED

(Prefer `jmag_case_table`; this is the pattern it uses.) Assumes equation `I` exists (S7):
equations join the design table by themselves as `Equation parameters: I`.

```python
t = need(s.GetDesignTable(), "design table")
params = [(i, t.ParameterName(i)) for i in range(t.NumParameters())]
k = [i for i, n in params if n == "Equation parameters: I"]
if not k:
    raise OpError("equation I is not a design-table parameter", "parameters: %s" % [n for _, n in params])
k = k[0]
added = []
for v in [5.0, 10.0, 15.0]:
    before = t.NumCases()
    t.AddCases(1)
    if t.NumCases() != before + 1:
        raise OpError("AddCases(1) did not add a case")
    t.SetValue(before, k, v)
    added.append({"case": before, "I": t.GetValue(before, k)})   # read back
result = {"param_index": k, "added": added, "num_cases": t.NumCases()}
```
Cases are 0-based and case 0 is the base case. Other parameters need `t.AddParameter(<full
name from t.AllParameterNames()>)` first (e.g. `'Motion (RotationMotion2D): AngularVelocity'`).

## S9 Create a loss study - VERIFIED

(Prefer `jmag_loss_study`, which also sets frequency, reference steps and parts.)

```python
loss = need(s.CreateLossStudy("Loss_" + s.GetName()), "loss study")
conds = [loss.GetCondition(i) for i in range(loss.NumConditions())]
iron = [c.GetName() for c in conds if c.GetScriptTypeName() == "Ironloss"]
result = {"loss_study": loss.GetName(), "type": loss.GetScriptTypeName(), "ironloss_conditions": iron}
```
On 23.0 the new loss study already holds one `Ironloss` condition. Its `BasicFrequency` must be
set (0 aborts the solve with "Frequency is 0[Hz]").

## S10 Efficiency-map definition - UNVERIFIED

(Prefer `jmag_efficiency_map`, which uses these members but is itself unverified on 23.0.)

```python
em = s.CreateEfficiencyMapDefinition("Map1")
if em is None or not em.IsValid():
    app.ClearError()
    raise OpError("no efficiency-map definition on this study",
                  "maps live in an efficiency-map (response) study made with JMAG's wizard")
em.SetTorqueType("shaft")                    # "shaft" or "electromagnetic"
result = {"control": em.ControlName(),      # "mtpa", "mtpa+fw", "unitypf", "id=0"
          "carrier_hz": em.CarrierFrequency(), "correction": em.CorrectionType()}
```

## S11 Point probe of B - UNVERIFIED

Not run in 2D or 3D. In 3D a point is `CreatePoint(x, y, z)`; the component naming is unknown.

```python
pr = need(s.CreateProbe("gapB"), "probe")
pr.AddPoint(app.CreatePoint(30.5, 0.0, 0.0), "mid_gap")
pr.SetResultType("MagneticFluxDensity", "")  # same result-type name the contour tool uses
ds = need(pr.GetDataSet(), "probe data set")
result = {"rows": ds.GetRows(), "cols": ds.GetCols(),
          "first_row": [ds.GetValue(0, j) for j in range(ds.GetCols())]}
```

## S12 Section graph along the air gap - UNVERIFIED

Not run in 2D or 3D.

```python
sg = need(s.CreateSectionGraph("gap_arc"), "section graph")
o = app.CreatePoint(0.0, 0.0, 0.0)
axis = app.CreatePoint(0.0, 0.0, 1.0)
start = app.CreatePoint(30.5, 0.0, 0.0)      # mid-gap radius of the 12s4p example
sg.SetArc(o, axis, start, 360.0)
sg.SetResultType("MagneticFluxDensity", "")
ds = need(sg.GetDataSet(), "section data set")
result = {"rows": ds.GetRows(), "cols": [ds.GetColumnName(j) for j in range(ds.GetCols())]}
```
Gap B vs angle gives the flux-density distribution for B_g / B_1 checks (`jmag-machine-design`).
Which component (radial / tangential / absolute) is returned and in which coordinate system is
unverified - check against the contour at the same step.

---

## S-3D-1 Import a STEP and rename parts by inside point - VERIFIED (3D, 2026-10-19)

Coordinates in S-3D-1 to S-3D-4 are examples (mm, a half model y >= 0): take yours from the labels
JSON of your geometry script (`jmag-geometry`).

(Prefer `jmag_import_cad` + `jmag_rename_parts` with `[x, y, z]` points; this is what they call.)
Check the STEP's product names first with `python -m jmag_bridge.cad3d model.step`.

```python
path = r"<folder>\e_core_half.step"
m = app.LoadCadDataWithOption(path, "e_core", True, True, "mm")   # healing, remove sheets, unit
if m is None or not m.IsValid():
    m = app.GetModel(app.NumModels() - 1)
app.SetCurrentModel(app.NumModels() - 1)
before = [m.GetPartByIndex(i).GetName() for i in range(m.NumParts())]   # e.g. "E_core_pair/Core_Top"
renames = [((0.0, 5.25, 23.0), "Core_Top"), ((0.0, 13.0, 0.0), "Coil_Primary")]
done = []
for (x, y, z), new in renames:
    p = m.GetPartByPosition(app.CreatePoint(x, y, z))
    if p is None or not p.IsValid():
        app.ClearError()
        raise OpError("no part contains %s" % ((x, y, z),), "use a point strictly inside the solid")
    done.append((p.GetName(), new))
    p.SetName(new)
result = {"before": before, "renamed": done,
          "volumes": dict((m.GetPartByIndex(i).GetName(), m.GetPartByIndex(i).Volume()) for i in range(m.NumParts()))}
```
Compare every volume with the CAD volume (they matched to < 2e-5 on 2026-10-19).

## S-3D-2 3D `Current` condition with an inflow face - VERIFIED (3D, 2026-10-19)

Half model, winding cut into a U by the symmetry plane; the point lies on one end face of the U on
that plane. `DirectionType` 0 = InflowFace; 1 = Vector gives zero current in a closed winding (no
error). The outflow face is found by the solver on the symmetry boundary; selecting it is optional.

```python
s = find_study(app.GetCurrentModel(), "Ip")
c = need(s.CreateCondition("Current", "primary"), "Current condition")
c.SetValue("Turn", 20.0)
c.SetValue("Current", 1.0)
c.SetValue("DirectionType", 0)        # InflowFace
c.SetValue("UniformCurrent", 1)
sel = c.GetSelection(); sel.Clear(); sel.SelectPart("Coil_Primary"); c.AddSelected(sel)
sel2 = c.GetSecondarySelection(); sel2.Clear()
sel2.SelectFaceByPosition(2.5, 0.0, 0.0)      # inflow end face, on the cut plane y = 0
c.AddSecondarySelected(sel2)
result = {"Turn": c.GetValue("Turn"), "Current": c.GetValue("Current"),
          "DirectionType": c.GetValue("DirectionType"), "parts": c.GetSelection().NumParts(),
          "secondary_faces": c.GetSecondarySelection().NumFaces()}   # expect 1
```
Run fails with "Cannot find flow-out face ... not set on Symmetry boundary (3D)" when the coil ends
are inside the air. Current enters through the selected face: flip the face to flip the sign.

## S-3D-3 `SymmetryBoundary` on faces picked by position - VERIFIED (3D, 2026-10-19)

```python
s = find_study(app.GetCurrentModel(), "Ip")
c = need(s.CreateCondition("SymmetryBoundary", "cut_plane"), "SymmetryBoundary")
pts = [(0.0, 0.0, 23.0), (0.0, 0.0, -23.0), (2.5, 0.0, 0.0), (40.0, 0.0, 40.0)]   # one point per face
for (x, y, z) in pts:
    sel = c.GetSelection(); sel.Clear(); sel.SelectFaceByPosition(x, y, z); c.AddSelected(sel)
n = c.GetSelection().NumFaces()
if n != len(pts):
    raise OpError("selected %d faces for %d points" % (n, len(pts)),
                  "a face shared by two solids selects both; pick points on external faces")
result = {"faces": n}
```
The boundary type was left at JMAG's default, which acted flux-parallel (B tangential) in both
transformers. The property that sets the type explicitly is UNVERIFIED: look it up with
`jmag_get_properties(study='Ip', kind='condition', name='cut_plane', with_help=true)` and read it back.

## S-3D-4 3D mesh controls, part sizes and the energy table - VERIFIED (3D, 2026-10-19)

```python
s = find_study(app.GetCurrentModel(), "Ip")
sp = s.GetStudyProperties()
sp.SetValue("Energy", 1)                       # StoredEnergy table: one column per part + Total
mc = s.GetMeshControl()
for k, v in (("AirRegion", 0), ("AutoMeshSize", 0), ("MeshSize", 8.0)):   # STEP carries its own air
    mc.SetValue(k, v)
for part, size in (("Core_Top", 2.0), ("Coil_Primary", 2.0), ("Gap", 0.5)):
    mcond = need(mc.CreateCondition("Part", "size_" + part), "mesh part condition")
    mcond.SetValue("Size", size)
    sel = mcond.GetSelection(); sel.Clear(); sel.SelectPart(part); mcond.AddSelected(sel)
result = {"Energy": sp.GetValue("Energy"),
          "mesh": dict((k, mc.GetValue(k)) for k in ("AirRegion", "AutoMeshSize", "MeshSize"))}
```

## S-3D-5 Read `CoilFlux` / `CoilCurrent` / `StoredEnergy` - VERIFIED (3D, 2026-10-19)

```python
s = find_study(app.GetCurrentModel(), "Ip")
rt = s.GetResultTable()
out = {}
for key in ("CoilFlux", "CoilCurrent", "StoredEnergy"):
    d = rt.GetDataFromName(key)
    if d is None or not d.IsValid():
        app.ClearError()
        out[key] = None
        continue
    cols = [d.GetColName(j) for j in range(d.GetCols())]
    out[key] = dict((cn, [d.GetValue(r, cn) for r in range(d.GetRows())]) for cn in cols)
rep = s.GetReport()
result = {"tables": out, "elements": rep.NumElements(), "nodes": rep.NumVertices()}
```
`CoilFlux` has one column per `Current` condition, turns included, and is the model value (half
model: multiply by 2 for the device; no JMAG conversion factor was used). A 0 A condition still
reports its flux, so one study per excited winding gives a full L matrix.
