# List visible top-level windows of the running pet (python pet.py) and print their union rect.
# Usage:  pwsh -File tools/find_windows.ps1
$ErrorActionPreference = 'Stop'
Add-Type @"
using System;using System.Runtime.InteropServices;using System.Text;
public class PetWinEnum {
  public delegate bool Proc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(Proc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*pet.py*' }
if (-not $procs) { Write-Host 'NOT_RUNNING'; exit 2 }
$pids = @($procs.ProcessId)
$rows = New-Object System.Collections.ArrayList
$cb = [PetWinEnum+Proc] {
    param($h, $l)
    $owner = 0
    [void][PetWinEnum]::GetWindowThreadProcessId($h, [ref]$owner)
    if (($pids -contains $owner) -and [PetWinEnum]::IsWindowVisible($h)) {
        $r = New-Object PetWinEnum+RECT
        if ([PetWinEnum]::GetWindowRect($h, [ref]$r)) {
            [void]$rows.Add([pscustomobject]@{
                X = $r.Left; Y = $r.Top; W = $r.Right - $r.Left; H = $r.Bottom - $r.Top })
        }
    }
    return $true
}
[void][PetWinEnum]::EnumWindows($cb, [IntPtr]::Zero)
$rows | Format-Table -AutoSize | Out-String | Write-Host
if ($rows.Count) {
    $minX = ($rows | Measure-Object X -Minimum).Minimum
    $minY = ($rows | Measure-Object Y -Minimum).Minimum
    $maxX = ($rows | ForEach-Object { $_.X + $_.W } | Measure-Object -Maximum).Maximum
    $maxY = ($rows | ForEach-Object { $_.Y + $_.H } | Measure-Object -Maximum).Maximum
    Write-Host "UNION $minX $minY $($maxX - $minX) $($maxY - $minY)"
}
