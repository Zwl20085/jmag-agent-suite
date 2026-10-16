<#
.SYNOPSIS
  Install jmag-agent-suite for Claude Code: venv + package, MCP registration, skills.
.EXAMPLE
  .\scripts\install.ps1                       # user-wide MCP server + skills
  .\scripts\install.ps1 -JmagHome "E:\JMAG-Designer23.1"
#>
param(
  [string]$JmagHome = "",
  [ValidateSet("user", "project", "local")][string]$Scope = "user",
  [switch]$NoSkills
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
  if (-not (Test-Path ".venv")) { python -m venv .venv }
  & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
  & .\.venv\Scripts\python.exe -m pip install --quiet -e .
  $py = (Resolve-Path ".venv\Scripts\python.exe").Path

  $envArgs = @()
  if ($JmagHome) { $envArgs = @("-e", "JMAG_HOME=$JmagHome") }
  claude mcp remove jmag --scope $Scope 2>$null | Out-Null
  claude mcp add jmag --scope $Scope @envArgs -- $py -m jmag_bridge
  Write-Host "Registered MCP server 'jmag' ($Scope scope) -> $py -m jmag_bridge"

  if (-not $NoSkills) {
    $dest = Join-Path $env:USERPROFILE ".claude\skills"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    foreach ($s in Get-ChildItem ".claude\skills" -Directory) {
      $target = Join-Path $dest $s.Name
      # replace, don't merge: Copy-Item into an existing folder nests it (skill\skill) and keeps stale files
      if (Test-Path $target) { Remove-Item -Recurse -Force $target }
      Copy-Item $s.FullName $target -Recurse -Force
      Write-Host "Installed skill $($s.Name) -> $dest"
    }
  }
  & $py -c "from jmag_bridge.config import find_jmag; i=find_jmag(); print('JMAG found:', i.as_dict() if i else 'NOT FOUND - set JMAG_HOME')"
  Write-Host "Done. Restart Claude Code, then ask: 'run the JMAG self-test'."
} finally { Pop-Location }
