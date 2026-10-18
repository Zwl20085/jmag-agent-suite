<#
.SYNOPSIS
  Install jmag-agent-suite for Claude Code: venv + package, MCP registration, skills, then a doctor run.
.EXAMPLE
  .\scripts\install.ps1                       # user-wide MCP server + skills
  .\scripts\install.ps1 -JmagHome "E:\JMAG-Designer23.1"
  .\scripts\install.ps1 -Python "C:\Python312\python.exe"   # pick the interpreter for the venv
  .\scripts\install.ps1 -NoMcp                # venv + skills only (no Claude Code CLI needed)
  .\scripts\install.ps1 -Uninstall            # remove MCP registration and the skills (keeps .venv)
#>
param(
  [string]$JmagHome = "",
  [ValidateSet("user", "project", "local")][string]$Scope = "user",
  [switch]$NoSkills,
  [switch]$NoMcp,
  [switch]$Uninstall,
  [string]$Python = ""
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$claudeUrl = "https://docs.anthropic.com/en/docs/claude-code/setup"
$pythonUrl = "https://www.python.org/downloads/windows/"

function Fail([string]$msg) {
  Write-Host ""
  Write-Host "install.ps1: $msg" -ForegroundColor Red
  exit 1
}

# Run a native command without PowerShell 5.1 turning its stderr into a terminating error.
# Returns @{ code = <exit code>; out = <stdout+stderr text> }.
function Invoke-Native([string]$exe, [string[]]$argv) {
  $old = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    $out = & $exe @argv 2>&1 | ForEach-Object { "$_" }
    return @{ code = $LASTEXITCODE; out = ($out -join "`n") }
  } catch {
    return @{ code = -1; out = "$_" }
  } finally { $ErrorActionPreference = $old }
}

# Probe an interpreter: prints "<major> <minor> <sys.executable>" or nothing if it does not run.
function Test-Python([string]$exe, [string[]]$pre) {
  $probe = "import sys; print(sys.version_info[0], sys.version_info[1], sys.executable)"
  $r = Invoke-Native $exe ($pre + @("-c", $probe))
  if ($r.code -ne 0 -or -not $r.out) { return $null }
  $line = ($r.out -split "`n" | Where-Object { $_ -match '^\d+ \d+ ' } | Select-Object -Last 1)
  if (-not $line) { return $null }
  $parts = $line -split " ", 3
  return @{ major = [int]$parts[0]; minor = [int]$parts[1]; path = $parts[2].Trim() }
}

function Find-HostPython {
  $candidates = @()
  if ($Python) { $candidates += , @($Python, @()) }
  else {
    if (Get-Command py -ErrorAction SilentlyContinue) {
      foreach ($v in "3.14", "3.13", "3.12", "3.11", "3.10") { $candidates += , @("py", @("-$v")) }
    }
    foreach ($n in "python", "python3") {
      $c = Get-Command $n -ErrorAction SilentlyContinue
      if ($c) { $candidates += , @($c.Source, @()) }
    }
  }
  $rejected = @()
  foreach ($c in $candidates) {
    $label = (@($c[0]) + $c[1]) -join " "
    $info = Test-Python $c[0] $c[1]
    if (-not $info) {
      if ("$($c[0])" -match "WindowsApps") { $rejected += "$label (Microsoft Store stub: does not run)" }
      elseif ($Python -or $c[0] -ne "py") { $rejected += "$label (does not run)" }
      continue
    }
    if ($info.path -match "WindowsApps") { $rejected += "$label -> $($info.path) (Microsoft Store Python: sandboxed AppData)"; continue }
    if ($info.major -lt 3 -or ($info.major -eq 3 -and $info.minor -lt 10)) {
      $rejected += "$label -> Python $($info.major).$($info.minor) (< 3.10)"; continue
    }
    return $info.path
  }
  $why = if ($rejected) { "Rejected: " + ($rejected -join "; ") + "." } else { "No python/py found on PATH." }
  Fail "no usable Python >= 3.10 for the server venv. $why Install Python 3.10+ from $pythonUrl (tick 'Add to PATH'), or pass -Python <path to python.exe>."
}

Push-Location $repo
try {
  $hasClaude = [bool](Get-Command claude -ErrorAction SilentlyContinue)
  $skillNames = @(Get-ChildItem ".claude\skills" -Directory | ForEach-Object { $_.Name })
  $skillDest = Join-Path $env:USERPROFILE ".claude\skills"

  # ------------------------------------------------------------------ uninstall
  if ($Uninstall) {
    if ($hasClaude) {
      $r = Invoke-Native "claude" @("mcp", "remove", "jmag", "--scope", $Scope)
      Write-Host "claude mcp remove jmag --scope $Scope -> $(if ($r.code -eq 0) { 'removed' } else { 'not registered' })"
    } else { Write-Host "claude CLI not on PATH: skipped MCP removal" }
    foreach ($s in $skillNames) {
      $target = Join-Path $skillDest $s
      if (Test-Path $target) { Remove-Item -Recurse -Force $target; Write-Host "Removed skill $target" }
    }
    Write-Host "Uninstalled (the .venv folder in $repo was left in place; delete it by hand if you want)."
    exit 0
  }

  # ------------------------------------------------------------------ preflight
  if (-not $NoMcp -and -not $hasClaude) {
    Fail "the Claude Code CLI ('claude') is not on PATH. Install it from $claudeUrl, open a new terminal and rerun; or pass -NoMcp to install without registering the MCP server."
  }
  if ($JmagHome) {
    if (-not (Test-Path (Join-Path $JmagHome "designer.exe"))) {
      Fail "-JmagHome '$JmagHome' has no designer.exe. Pass the JMAG-Designer folder that contains designer.exe and python3.8\."
    }
    if (-not (Test-Path (Join-Path $JmagHome "python3.8\python.exe"))) {
      Write-Warning "'$JmagHome' has no python3.8\python.exe: the worker will need JMAG_PYTHON (the doctor will report it)."
    }
  }

  # ------------------------------------------------------------------ venv + package
  $py = Join-Path $repo ".venv\Scripts\python.exe"
  if (Test-Path ".venv") {
    $info = Test-Python $py @()
    if (-not $info -or $info.major -lt 3 -or ($info.major -eq 3 -and $info.minor -lt 10)) {
      Fail "the existing .venv does not run or is older than Python 3.10 (its base Python was removed or upgraded?). Delete the .venv folder in $repo and rerun."
    }
  } else {
    $hostPy = Find-HostPython
    Write-Host "Creating .venv with $hostPy"
    $r = Invoke-Native $hostPy @("-m", "venv", ".venv")
    if ($r.code -ne 0) { Fail "python -m venv failed:`n$($r.out)" }
  }
  $r = Invoke-Native $py @("-m", "pip", "install", "--quiet", "--upgrade", "pip")
  if ($r.code -ne 0) { Write-Warning "pip self-upgrade failed (continuing):`n$($r.out)" }
  $r = Invoke-Native $py @("-m", "pip", "install", "--quiet", "-e", ".[plots]")
  if ($r.code -ne 0) { Fail "pip install -e .[plots] failed (network/proxy?):`n$($r.out)" }
  Write-Host "Installed jmag-agent-suite (+plots) into $py"

  # ------------------------------------------------------------------ MCP registration
  if (-not $NoMcp) {
    $envArgs = @()
    if ($JmagHome) { $envArgs = @("-e", "JMAG_HOME=$JmagHome") }
    Invoke-Native "claude" @("mcp", "remove", "jmag", "--scope", $Scope) | Out-Null
    $r = Invoke-Native "claude" (@("mcp", "add", "jmag", "--scope", $Scope) + $envArgs + @("--", $py, "-m", "jmag_bridge"))
    if ($r.code -ne 0) { Fail "claude mcp add failed:`n$($r.out)" }
    Write-Host "Registered MCP server 'jmag' ($Scope scope) -> $py -m jmag_bridge"
  } else { Write-Host "-NoMcp: MCP server not registered" }

  # ------------------------------------------------------------------ skills
  if (-not $NoSkills) {
    New-Item -ItemType Directory -Force -Path $skillDest | Out-Null
    foreach ($s in Get-ChildItem ".claude\skills" -Directory) {
      $target = Join-Path $skillDest $s.Name
      # replace, don't merge: Copy-Item into an existing folder nests it (skill\skill) and keeps stale files
      if (Test-Path $target) { Remove-Item -Recurse -Force $target }
      Copy-Item $s.FullName $target -Recurse -Force
      Write-Host "Installed skill $($s.Name) -> $skillDest"
    }
  }

  # ------------------------------------------------------------------ doctor
  Write-Host ""
  # the registered server gets JMAG_HOME via `claude mcp add -e`; give the doctor the same view
  $oldJmagHome = $env:JMAG_HOME
  if ($JmagHome) { $env:JMAG_HOME = $JmagHome }
  try {
    $old = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $py -m jmag_bridge --doctor
    $doctorCode = $LASTEXITCODE
    $ErrorActionPreference = $old
  } finally { $env:JMAG_HOME = $oldJmagHome }
  Write-Host ""
  Write-Host "To update: git pull; .\scripts\install.ps1"
  if ($doctorCode -ne 0) {
    Write-Host "Installed, but the doctor reported FAIL rows above: apply their 'fix:' lines and rerun '$py -m jmag_bridge --doctor'." -ForegroundColor Yellow
    exit 1
  }
  Write-Host "Done. Restart Claude Code, then ask: 'run the JMAG self-test'."
} finally { Pop-Location }
