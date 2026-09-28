<#
  uninstall_windows.ps1 - removes the two shortcuts install_windows.ps1 added (the
  desktop "Ask All" icon and the Startup "Ask All server" entry). Deletes nothing else:
  the repo, your profile and your saved runs stay where they are. A server that is
  already running keeps running until you log off or end pythonw.exe.

    powershell -ExecutionPolicy Bypass -File scripts\uninstall_windows.ps1
#>
# Step: remove each shortcut if it exists, and say what happened either way
foreach ($p in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Ask All.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'Ask All server.lnk'))) {
    if (Test-Path $p) { Remove-Item $p -Confirm:$false; Write-Host "removed: $p" }
    else { Write-Host "not there: $p" }
}
