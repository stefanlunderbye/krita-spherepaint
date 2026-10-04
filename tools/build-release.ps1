# Builds a Krita-installable zip with the plugin and a bundled NumPy for Windows.
param(
    [Parameter(Mandatory)] [string] $Version,
    [string] $PythonVersion = '3.13',
    [string] $Python = 'python'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$stage = Join-Path $root "build\stage"
$wheels = Join-Path $root "build\wheels"
$dist = Join-Path $root 'dist'

Remove-Item (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage, $wheels, $dist -Force | Out-Null

Copy-Item (Join-Path $root 'spherepaint.desktop') $stage
Copy-Item (Join-Path $root 'spherepaint') $stage -Recurse
Remove-Item (Join-Path $stage 'spherepaint\_vendor') -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $stage 'spherepaint\LICENSE')

& $Python -m pip download numpy --only-binary=:all: --python-version $PythonVersion `
    --platform win_amd64 --implementation cp --no-deps -d $wheels --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'pip download failed' }
$wheel = Get-ChildItem $wheels -Filter 'numpy-*.whl' | Select-Object -First 1
$vendor = Join-Path $stage 'spherepaint\_vendor'
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, $vendor)
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

$zip = Join-Path $dist "krita-spherepaint-$Version-windows.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip)
Write-Host "Built $zip ($($wheel.Name))"
