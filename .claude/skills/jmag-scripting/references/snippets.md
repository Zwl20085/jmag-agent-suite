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

Rules for this file: every JMAG call is a member listed in the JMAG 23.0 script reference
inventory or already exercised by the bridge's `worker/ops.py`. **VERIFIED** = the same calls
run in `ops.py` against JMAG 23.0. **UNVERIFIED** = documented in the 23.0 reference but never
run through the bridge: use on a copy, read back, report what happened, and mark the result.

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

```python
pr = need(s.CreateProbe("gapB"), "probe")
pr.AddPoint(app.CreatePoint(30.5, 0.0, 0.0), "mid_gap")
pr.SetResultType("MagneticFluxDensity", "")  # same result-type name the contour tool uses
ds = need(pr.GetDataSet(), "probe data set")
result = {"rows": ds.GetRows(), "cols": ds.GetCols(),
          "first_row": [ds.GetValue(0, j) for j in range(ds.GetCols())]}
```

## S12 Section graph along the air gap - UNVERIFIED

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
