param(
    [string]$Config = "Release"
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildDir = Join-Path $root "build"

if (!(Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "cmake not found in PATH."
}

cmake -S $root -B $buildDir
cmake --build $buildDir --config $Config

$dllSrc = Join-Path $buildDir "$Config\\native_mixer.dll"
if (!(Test-Path $dllSrc)) {
    $dllSrc = Join-Path $buildDir "native_mixer.dll"
}

if (!(Test-Path $dllSrc)) {
    throw "native_mixer.dll was not produced."
}

$dllDst = Join-Path $root "native_mixer.dll"
Copy-Item $dllSrc $dllDst -Force

Write-Host "Built:" $dllDst
