param(
    [string]$ShortcutName = "Amazon 选品助手 Dev"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $AppDir
$Launcher = Join-Path $RepoRoot "launcher.py"
$BatchLauncher = Join-Path $RepoRoot "launch_desktop.bat"
$Pythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Icon = Join-Path $AppDir "web\app-icon.ico"

if (-not (Test-Path $Launcher)) {
    throw "Cannot find launcher.py at $Launcher"
}

$Desktop = [Environment]::GetFolderPath("Desktop")
$ShortcutPath = Join-Path $Desktop ($ShortcutName + ".lnk")

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut($ShortcutPath)

if (Test-Path $Pythonw) {
    $Shortcut.TargetPath = $Pythonw
    $Shortcut.Arguments = '"' + $Launcher + '"'
} elseif (Test-Path $Python) {
    $Shortcut.TargetPath = $Python
    $Shortcut.Arguments = '"' + $Launcher + '"'
} elseif (Test-Path $BatchLauncher) {
    $Shortcut.TargetPath = $BatchLauncher
    $Shortcut.Arguments = ""
} else {
    throw "Cannot find Python or launch_desktop.bat"
}

$Shortcut.WorkingDirectory = $RepoRoot
$Shortcut.Description = "启动 Amazon 选品助手桌面壳（开发环境）"
if (Test-Path $Icon) {
    $Shortcut.IconLocation = $Icon
}
$Shortcut.Save()

Write-Host "Created desktop shortcut:"
Write-Host "  $ShortcutPath"
