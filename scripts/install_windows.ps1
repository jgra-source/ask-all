<#
  install_windows.ps1 - optional one-click setup for Windows. Adds two shortcuts:
    Desktop  "Ask All"         starts the page server if needed and opens the page.
    Startup  "Ask All server"  starts the server hidden at login, so a bookmark to
                               http://127.0.0.1:8770 works right after a restart.
  Both run the server with pythonw.exe (Python without a console window).
  Nothing else is changed. Undo with scripts\uninstall_windows.ps1.

  Run from the ask-all folder:
    powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_windows.ps1 -NoStartup   (desktop icon only)
#>
param([switch]$NoStartup)
$ErrorActionPreference = 'Stop'

# Step: find a real Python 3.11+ (the "python" on PATH can be the Microsoft Store stub,
# which does nothing; the py launcher, when installed, knows where the real one is)
$python = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $python = (& py -3 -c "import sys; print(sys.executable)" 2>$null)
}
if (-not $python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike '*WindowsApps*') { $python = $cmd.Source }
}
if (-not $python) { throw "Python 3.11+ not found. Install it from python.org (tick 'Add to PATH'), then run this again." }
$pythonw = Join-Path (Split-Path $python) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { throw "pythonw.exe not found next to $python." }
& $python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "$python is older than 3.11. Install Python 3.11 or newer." }

# Step: the server script, relative to this file (works wherever the repo was cloned)
$root = Split-Path -Parent $PSScriptRoot
$server = Join-Path $root 'web\server.py'
if (-not (Test-Path $server)) { throw "Can't find $server. Run this from inside the ask-all folder." }

# Step: write one shortcut and read it back, so a silent failure can't pass as success
function New-Shortcut($path, $arguments, $description) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($path)
    $s.TargetPath = $pythonw
    $s.Arguments = $arguments
    $s.WorkingDirectory = Join-Path $root 'web'
    $s.Description = $description
    $s.Save()
    $check = $ws.CreateShortcut($path)
    if ($check.TargetPath -ne $pythonw) { throw "Shortcut $path did not save correctly." }
    Write-Host "created: $path"
}

# Step: desktop icon (opens the page) and, unless -NoStartup, the hidden login starter
New-Shortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ask All.lnk') "`"$server`" --open" `
    'Ask All: paste once, several AIs answer side by side'
if (-not $NoStartup) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath('Startup')) 'Ask All server.lnk') "`"$server`"" `
        'Starts the Ask All page server hidden at login (127.0.0.1:8770). Delete to stop.'
}
Write-Host "Done. Double-click 'Ask All' on the desktop, or bookmark http://127.0.0.1:8770"
