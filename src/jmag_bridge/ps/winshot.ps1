# Captures every visible top-level window of JMAG processes (main windows and dialogs) to PNG,
# even when they are off-screen or behind other windows (PrintWindow).
param([Parameter(Mandatory=$true)][string]$OutDir, [string]$ProcNames = "designer|modeller")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices; using System.Collections.Generic;
public class JmagWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> All() { var l = new List<IntPtr>(); EnumWindows((h, p) => { if (IsWindowVisible(h)) l.Add(h); return true; }, IntPtr.Zero); return l; }
}
"@
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$pids = @{}
Get-Process | Where-Object { $_.ProcessName -match "^($ProcNames)$" } | ForEach-Object { $pids[[uint32]$_.Id] = $_.ProcessName }
$i = 0
foreach ($h in [JmagWin]::All()) {
  $wpid = [uint32]0; [void][JmagWin]::GetWindowThreadProcessId($h, [ref]$wpid)
  if (-not $pids.ContainsKey($wpid)) { continue }
  $sb = New-Object System.Text.StringBuilder 512; [void][JmagWin]::GetWindowText($h, $sb, 512)
  $r = New-Object JmagWin+RECT; [void][JmagWin]::GetWindowRect($h, [ref]$r)
  $w = $r.R - $r.L; $hh = $r.B - $r.T
  if ($w -lt 80 -or $hh -lt 40) { continue }
  $bmp = New-Object System.Drawing.Bitmap $w, $hh
  $g = [System.Drawing.Graphics]::FromImage($bmp); $hdc = $g.GetHdc()
  [void][JmagWin]::PrintWindow($h, $hdc, 2); $g.ReleaseHdc($hdc); $g.Dispose()
  $f = Join-Path $OutDir ("jmag_window_{0}_{1}.png" -f $i, $pids[$wpid]); $bmp.Save($f); $bmp.Dispose(); $i++
  [pscustomobject]@{ process = $pids[$wpid]; pid = $wpid; title = $sb.ToString(); width = $w; height = $hh; path = $f } | ConvertTo-Json -Compress
}
