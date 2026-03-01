param(
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"

& $PythonExe -m PyInstaller `
    --onefile `
    --name VoiceQoSSetup `
    --uac-admin `
    --clean `
    tools/qos_policy_installer.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

Write-Host ""
Write-Host "Built installer:"
Write-Host "  dist\\VoiceQoSSetup.exe"
