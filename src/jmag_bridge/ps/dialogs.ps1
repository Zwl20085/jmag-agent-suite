# Lists modal dialogs owned by JMAG processes (UI Automation) and optionally clicks a button.
# Output: one JSON object per dialog.
param([string]$Click = "", [string]$TitleFilter = "", [string]$ProcNames = "designer|modeller")
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$UIA = [System.Windows.Automation.AutomationElement]
$CT = [System.Windows.Automation.ControlType]
$pids = @(Get-Process | Where-Object { $_.ProcessName -match "^($ProcNames)$" } | ForEach-Object { $_.Id })
$isCt = { param($t) New-Object System.Windows.Automation.PropertyCondition($UIA::ControlTypeProperty, $t) }
$tops = $UIA::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($w in $tops) {
  if ($pids -notcontains $w.Current.ProcessId) { continue }
  $cands = @($w) + @($w.FindAll([System.Windows.Automation.TreeScope]::Children, (& $isCt $CT::Window)))
  foreach ($d in $cands) {
    $title = $d.Current.Name
    if ($title -match '\[Window|Geometry Editor - ') { continue }   # main windows
    $btns = @($d.FindAll([System.Windows.Automation.TreeScope]::Descendants, (& $isCt $CT::Button)))
    $names = @($btns | ForEach-Object { $_.Current.Name } | Where-Object { $_ })
    if (($names -contains 'Close') -and ($names -contains 'Float')) { continue }  # docked panes
    if ($names.Count -gt 10) { continue }                                         # main windows / toolbars
    $modal = $false
    try { $modal = $d.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Current.IsModal } catch {}
    $kind = if ($title -match 'Run Analysis') { 'progress' } elseif ($modal) { 'modal' } else { 'window' }
    $texts = @($d.FindAll([System.Windows.Automation.TreeScope]::Descendants, (& $isCt $CT::Text)) | ForEach-Object { $_.Current.Name } | Where-Object { $_ })
    if ($names.Count -eq 0 -and $texts.Count -eq 0) { continue }
    if ($TitleFilter -and ($title -notmatch $TitleFilter)) { continue }
    $clicked = $null
    if ($Click) {
      $b = $btns | Where-Object { $_.Current.Name -eq $Click } | Select-Object -First 1
      if ($b) { $b.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke(); $clicked = $Click }
    }
    [pscustomobject]@{ pid = $d.Current.ProcessId; kind = $kind; title = $title; text = ($texts -join "`n"); buttons = $names; clicked = $clicked } | ConvertTo-Json -Compress
  }
}
