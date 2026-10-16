# Design table, analysis group and optimisation table: 23.0 API members

Contents: 1 Verified usage - 2 DesignTable - 3 Running cases - 4 AnalysisGroup -
5 OptimizationTable - 6 Exports

Signatures are copied from the JMAG-Designer 23.0 script reference. "Verified" = used by the
bridge's worker (`ops.py`) on a real JMAG 23.0. `jmag_case_table` (equation and condition
parameters, cases) and `jmag_run_cases(method='loop')` are verified tools built on these members;
the raw members are listed for `jmag_exec_python` work. Members not used by a verified tool are
**unverified on 23.0**.

## 1. Verified usage (equations)

`Study.GetDesignTable()` returns the design table; the bridge's equation tools use
`HasEquation(name)`, `AddEquation(name)`, `GetEquation(name | index)`, and on the equation
`SetType(0 value | 1 expression)`, `SetExpression(str)`, `SetDescription`, `GetExpression`,
`GetValue`, `GetName`. `GetEquation(i)` past the last index logs an error (the bridge clears
it). Use `jmag_set_equations` / `jmag_get_equations` rather than raw calls.

## 2. DesignTable (cases)

- `DesignTable * Study::GetDesignTable()`
- `void AddParameter(String & parameterName)` - adds parameters as design variables
- `void AddEquation(String & parameterName)` - adds a parametric equation (verified)
- `void AddCadParameter(String & parameterName)` - CAD-model parameters as design variables
- `void AddMeasurementVariable(String & parameterName)`
- `void AddCases(int numCases)`
- `void AddCaseGroup(String & name, String & indices)`
- `void SetValue(int caseIndex, int parameterIndex, Variant & value)`
- `void SetValues(int caseIndex, String array & parameterIndices, String array & values)`
- `void SetActive(int caseIndex, int active)` - the case check box in the Design Table dialog
- `void RemoveCase(int caseIndex)`
- `int NumCases()`, `int NumParameters()`
- `void Import(String & filename)` / `void Export(String & filename)` - case lists (*.csv, *.txt;
  export also *.html)
- `void ImportEquation(String & filename)` / `void ExportEquation(String & filename,
  StringList & parameterNameList)` - equations as *.xml

Pattern (unverified): add a parameter for an existing equation/property, `AddCases(n)`, then
`SetValue(case, param_index, value)` per cell, `NumCases()` to read back, `Export` to a CSV
that the report can cite. Case 0 is the base case (as `jmag_case_table` documents).

## 3. Running cases

- `void Study::Run()` runs the **current case** in the foreground (verified via `jmag_run`).
- `bool Study::CaseHasResult(int caseIndex)` - whether a case has results.
- `void Study::ExportCaseValueData(String & filename)` - the response value table (*.htm, *.txt,
  *.csv).
- `Job * Application::CreateJob(String array & studies)`, `Study::SubmitLocal(bool queued)`,
  `Study::SubmitRemote(...)` - batch/remote execution (unverified).
- `jmag_run_cases(method='loop')` selects each case (`SetCurrentCase`) and runs it - verified;
  use it rather than raw calls.

## 4. AnalysisGroup

- `AnalysisGroup * Application::CreateAnalysisGroup(String & typeName, String & name)`
- `void RunAllCases()`, `void RunSequentialAllCases()`, `void RunCoupledAllCases()`
- `void SetCurrentCase(int caseIndex)`
- `DesignTable * GetDesignTable()`, `OptimizationTable * GetOptimizationTable()`
- `ParametricGraphDefinition * CreateParametricGraphDefinition()` - response graph
- `void ExportAnalysisParameters(String & filename, bool exportCADParameter)`

`RunAllCases` runs in the foreground and blocks Designer for the whole batch; the bridge's
runner pattern (one case per `jmag_run_cases` item) keeps status and cancel responsive.

## 5. OptimizationTable

- `OptimizationTable * Study::GetOptimizationTable()` (also on AnalysisGroup)
- `void AddObjectiveItem(String & parameterName)` - objective function
- `void AddExpressionItem(String & parameterName)` - constraint
- `void AddParametricItem(String & parameterName)` - variable range
- `void SetCheckOn(Variant & value, bool checkOn)` - use this variable range or not
- `void AddInitialCase(int caseIndex)`, `int CreateInitialCases()`, `void SetCreateInitialCases(int create)`
- `void SetCSVOutputPath(String & filePath)`, `void SetImmediateCSVOutput(bool immediateOutput)` -
  monitoring without the GUI
- `void SetCpuGroup(String & group)`
- `void ExportOptimizationRange(String & csvFile)` / `void ImportOptimizationRange(String & csvFile)`
- Magnet-direction optimisation: `OptimizeMagnetDirection()`, `SetMagnetDirectionType(index, exp)`,
  `SetMagnetDirectionMin/Max(index, value)`, `SetMagnetDirectionGroup(index, exp)`,
  `SetMagnetDirectionCoordinate(exp)`

Not found in the 23.0 reference: `SetNumSimultaneousStudies`; the algorithm choice and
population settings are not in the extracted member list (set them in the GUI or look them up
with `jmag_api_lookup("OptimizationTable")`).

## 6. Exports

- `void ResultTable::WriteAllCaseTables(String & filename, String & type)` - all cases' tables.
- `void GraphModel::WriteAllCaseTable(String & filename)`;
  `GraphModel * DataManager::CreateAllCasesGraphModel(DataSet * data, String & title)`.
- `bool Report::WriteParametricReport(String & filename)`, `bool Report::WriteCaseReport(String & filename)`.
