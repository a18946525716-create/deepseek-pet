# Create Desktop + Start Menu shortcuts for the desktop pet.
#   powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root '启动桌宠.bat'
$icon = Join-Path $root 'assets\icon.ico'

if (-not (Test-Path $bat)) { throw "launcher not found: $bat" }

$targets = @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) '大肥鱼桌宠.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) '大肥鱼桌宠.lnk')
)

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $targets) {
    $sc = $shell.CreateShortcut($lnk)
    $sc.TargetPath = $bat
    $sc.WorkingDirectory = $root
    $sc.Description = 'DeepSeek 蓝色大肥鱼桌宠'
    if (Test-Path $icon) { $sc.IconLocation = $icon }
    $sc.Save()
    Write-Host "created $lnk"
}
