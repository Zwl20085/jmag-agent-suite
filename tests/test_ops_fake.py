"""worker/ops.py logic against a minimal fake JMAG object model (no JMAG): validation happens before any
mutation, case switching is restored, out-of-range cases never reach JMAG."""
import pytest

from jmag_bridge.worker import ops

MUTATORS = {"SetCurrentCase", "SetActive", "DeleteResultCase", "DeleteResultCurrentCase", "RunAllCases", "Run",
            "AddParameter", "AddCases", "SetValue", "RemoveCase", "CreateCondition", "DeleteCondition", "SetLink"}


class Log(list):
    def mutations(self):
        return [c for c in self if c[0] in MUTATORS]


class FakeDesignTable:
    def __init__(self, log, n_cases=3, params=("Motion (RotationMotion2D): AngularVelocity",),
                 addable=("Source (3PhaseCurrentSource): Amplitude",)):
        self.log = log
        self.n = n_cases
        self.params = list(params)
        self.addable = list(params) + list(addable)
        self.active = [1] * n_cases
        self.values = {}

    def _rec(self, name, *a):
        self.log.append((name,) + a)

    def NumCases(self):
        return self.n

    def IsActive(self, c):
        return self.active[c]

    def SetActive(self, c, v):
        self._rec("SetActive", c, v)
        self.active[c] = v

    def NumParameters(self):
        return len(self.params)

    def ParameterName(self, i):
        return self.params[i]

    def ParameterTypeName(self, i):
        return "Condition"

    def GetVariableName(self, i):
        return ""

    def AllParameterNames(self):
        return list(self.addable)

    def AddParameter(self, name):
        self._rec("AddParameter", name)
        self.params.append(name)

    def AddCases(self, k):
        self._rec("AddCases", k)
        self.n += k
        self.active += [1] * k

    def SetValue(self, c, i, v):
        self._rec("SetValue", c, i, v)
        self.values[(c, i)] = v

    def GetValue(self, c, i):
        return self.values.get((c, i), 0.0)

    def RemoveCase(self, c):
        self._rec("RemoveCase", c)
        self.n -= 1

    def HasEquation(self, name):
        return False

    def GetLabel(self, c):
        return "Case %d" % (c + 1)

    def IsValid(self):
        return True


class FakeComponent:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class FakeCircuit:
    def __init__(self, names):
        self.comps = [FakeComponent(n) for n in names]

    def NumComponents(self):
        return len(self.comps)

    def GetComponent(self, i):
        return self.comps[i]

    def IsValid(self):
        return True


class FakeCondition:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class FakeStudy:
    def __init__(self, log, n_cases=3, results=(), current=0, components=("CoilU", "CoilV", "CoilW"), conditions=()):
        self.log = log
        self.dt = FakeDesignTable(log, n_cases)
        self.results = set(results)
        self.current = current
        self.circuit = FakeCircuit(components) if components is not None else None
        self.conditions = [FakeCondition(c) for c in conditions]

    def GetName(self):
        return "S"

    def GetDesignTable(self):
        return self.dt

    def CaseHasResult(self, c):
        self.log.append(("CaseHasResult", c))
        if not 0 <= c < self.dt.n:
            raise AssertionError("CaseHasResult(%s) out of range would log a JMAG error" % c)
        return c in self.results

    def HasResult(self):
        return self.current in self.results

    def GetCurrentCase(self):
        return self.current

    def SetCurrentCase(self, c):
        self.log.append(("SetCurrentCase", c))
        self.current = c

    def DeleteResultCase(self, c):
        self.log.append(("DeleteResultCase", c))
        self.results.discard(c)

    def RunAllCases(self):
        self.log.append(("RunAllCases",))
        self.results.update(c for c in range(self.dt.n) if self.dt.active[c])

    def GetReport(self):
        raise RuntimeError("no report in the fake")

    def GetConditionTypes(self):
        return ["FEMCoil", "Current"]

    def GetScriptTypeName(self):
        return "Transient2D"

    def HasCircuit(self):
        return self.circuit is not None

    def GetCircuit(self):
        return self.circuit

    def NumConditions(self):
        return len(self.conditions)

    def GetCondition(self, i):
        return self.conditions[i]

    def CreateCondition(self, ctype, name):
        self.log.append(("CreateCondition", ctype, name))
        raise AssertionError("must not be reached in these tests")


class FakeApp:
    def __init__(self, running=False):
        self.running = running

    def HasRunningProcess(self):
        return self.running


class FakeCtx:
    def __init__(self, study, running=False):
        self.s = study
        self.app = FakeApp(running)

    def study(self, ref=None, model=None):
        return self.s

    def model_of(self, s):
        return None


def _ctx(**kw):
    log = Log()
    s = FakeStudy(log, **kw)
    return FakeCtx(s), s, log


# --------------------------------------------------------------------------- _CaseScope / _need_result

def test_case_scope_restores_previous_case_on_exception():
    ctx, s, log = _ctx(current=0, results=(0, 2))
    with pytest.raises(ValueError):
        with ops._CaseScope(ctx, s, 2):
            assert s.current == 2
            raise ValueError("boom")
    assert s.current == 0
    assert [c for c in log if c[0] == "SetCurrentCase"] == [("SetCurrentCase", 2), ("SetCurrentCase", 0)]


def test_case_scope_rejects_out_of_range_before_any_jmag_call():
    ctx, s, log = _ctx(n_cases=3)
    for bad in (3, -1, 99):
        with pytest.raises(ops.OpError, match="out of range"):
            with ops._CaseScope(ctx, s, bad):
                pass
    assert log == []


@pytest.mark.parametrize("fn,args", [
    (ops.get_result, {"table": "Torque"}),
    (ops.list_results, {}),
    (ops.loss_results, {}),
])
def test_case_reads_validate_range_before_case_has_result(fn, args):
    ctx, s, log = _ctx(n_cases=3, results=(0, 1, 2))
    with pytest.raises(ops.OpError, match="out of range"):
        fn(ctx, "S", case=7, **args)
    assert log == []  # no CaseHasResult(7) probe, no SetCurrentCase


def test_export_table_validates_case_before_touching_the_file(tmp_path):
    ctx, s, log = _ctx(n_cases=2, results=(0, 1))
    out = tmp_path / "x.csv"
    out.write_text("keep me")
    with pytest.raises(ops.OpError, match="out of range"):
        ops.export_table(ctx, "S", str(out), case=5)
    assert out.read_text() == "keep me" and log == []


def test_case_scope_refused_while_solving():
    log = Log()
    s = FakeStudy(log)
    ctx = FakeCtx(s, running=True)
    with pytest.raises(ops.OpError, match="running a solve"):
        with ops._CaseScope(ctx, s, 1):
            pass
    assert log.mutations() == []


# --------------------------------------------------------------------------- run_cases all_cases

def test_run_cases_all_cases_no_overwrite_raises_before_any_mutation():
    ctx, s, log = _ctx(n_cases=3, results=(1,))
    with pytest.raises(ops.OpError, match="already have results"):
        ops.run_cases(ctx, "S", cases=[0, 1, 2], overwrite=False, method="all_cases")
    assert log.mutations() == []
    assert s.dt.active == [1, 1, 1] and s.results == {1}


def test_run_cases_all_cases_restores_activity():
    ctx, s, log = _ctx(n_cases=3, results=(1,))
    s.dt.active = [1, 0, 1]
    out = ops.run_cases(ctx, "S", cases=[1], overwrite=True, method="all_cases")
    assert out["finished"] == [1]
    assert s.dt.active == [1, 0, 1]
    assert ("DeleteResultCase", 1) in log


def test_run_cases_all_cases_restores_activity_when_run_fails():
    ctx, s, log = _ctx(n_cases=3)

    def boom():
        raise RuntimeError("solver died")
    s.RunAllCases = boom
    with pytest.raises(RuntimeError):
        ops.run_cases(ctx, "S", cases=[2], method="all_cases")
    assert s.dt.active == [1, 1, 1]


# --------------------------------------------------------------------------- case_table_set

def test_case_table_set_rejects_unknown_set_cases_names_before_mutation():
    ctx, s, log = _ctx(n_cases=3)
    with pytest.raises(ops.OpError, match="unknown parameter"):
        ops.case_table_set(ctx, "S", add_parameters=["Source: Amplitude"],
                           set_cases=[{"case": 1, "values": {"Nope: Speed": 3}}])
    assert log.mutations() == []


def test_case_table_set_accepts_names_added_in_the_same_call():
    ctx, s, log = _ctx(n_cases=3)
    out = ops.case_table_set(ctx, "S", add_parameters=["Source: Amplitude"],
                             set_cases=[{"case": 1, "values": {"Source: Amplitude": 5.0}}])
    assert out["applied"]["changed_cases"][0]["applied"]["Source: Amplitude"]["read"] == 5.0


@pytest.mark.parametrize("kw", [
    {"remove_cases": [3]},
    {"active": {"5": False}},
    {"current_case": 3},
    {"cases": [{"Unknown": 1}]},
    {"remove_cases": [2], "current_case": 2},       # case 2 is gone by the time current_case applies
])
def test_case_table_set_validates_indices_before_mutation(kw):
    ctx, s, log = _ctx(n_cases=3)
    with pytest.raises(ops.OpError):
        ops.case_table_set(ctx, "S", add_parameters=["Source: Amplitude"], **kw)
    assert log.mutations() == []


# --------------------------------------------------------------------------- winding_apply fem_coil

COILS = {"U": {"up": ["U1"], "down": ["U2"]}, "V": {"up": ["V1"], "down": ["V2"]}, "W": {"up": ["W1"], "down": ["W2"]}}


def test_winding_fem_coil_validates_all_phases_before_creating():
    ctx, s, log = _ctx(components=("CoilU", "CoilV"))  # CoilW missing: phase W is validated last
    with pytest.raises(ops.OpError, match="CoilW"):
        ops.winding_apply(ctx, "S", COILS)
    assert log.mutations() == []


def test_winding_fem_coil_rejects_existing_condition_and_bad_turns_first():
    ctx, s, log = _ctx(conditions=("PhaseW",))
    with pytest.raises(ops.OpError, match="already exists"):
        ops.winding_apply(ctx, "S", COILS)
    ctx, s, log2 = _ctx()
    with pytest.raises(ops.OpError, match="positive"):
        ops.winding_apply(ctx, "S", COILS, phase_turns={"U": 80, "W": 0})
    with pytest.raises(ops.OpError, match="unknown phase"):
        ops.winding_apply(ctx, "S", COILS, phase_turns={"X": 80})
    assert log.mutations() == [] and log2.mutations() == []


# --------------------------------------------------------------------------- create_loss_study

def test_create_loss_study_validates_reference_steps_before_replace():
    class M:
        def __init__(self, log):
            self.log = log

        def NumStudies(self):
            return 2

        def GetStudy(self, j):
            return FakeCondition(["S", "Loss"][j])

        def DeleteStudy(self, name):
            self.log.append(("DeleteStudy", name))

    ctx, s, log = _ctx()
    ctx.model_of = lambda st: M(log)
    for bad in ([10], [5, "x"], [9, 3], 7):
        with pytest.raises(ops.OpError, match="reference_steps"):
            ops.create_loss_study(ctx, "S", "Loss", reference_steps=bad, replace=True)
    assert log == []
