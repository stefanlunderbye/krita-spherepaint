# Builds a Krita-installable zip with the plugin and bundled NumPy for one platform.
#
# The Python bundled with Krita differs between platforms, so the zip holds one
# NumPy per Python version under spherepaint/_vendor/cpXY-<platform>-<machine>;
# the plugin picks the matching one at startup (see spherepaint/vendor.py).
param(
    [Parameter(Mandatory)] [string] $Version,
    [ValidateSet('windows', 'linux', 'macos')] [string] $Platform = 'windows',
    [string[]] $PythonVersions,
    [string] $Python = 'python'
)
$ErrorActionPreference = 'Stop'

# folder suffix -> pip platform tags
$targets = @{
    windows = [ordered]@{ 'win-x86_64' = @('win_amd64') }
    linux   = [ordered]@{ 'linux-x86_64' = @('manylinux_2_28_x86_64', 'manylinux2014_x86_64') }
    # Oldest macOS first, so pip prefers the widely compatible wheels over macOS 14-only builds.
    macos   = [ordered]@{ 'macos-arm64' = @('macosx_11_0_arm64', 'macosx_14_0_arm64')
                          'macos-x86_64' = @('macosx_10_13_x86_64', 'macosx_10_9_x86_64') }
}
if (-not $PythonVersions) {
    # Krita 5.3 on Windows ships Python 3.13; other platforms vary, so cover several.
    $PythonVersions = if ($Platform -eq 'windows') { @('3.13') } else { @('3.10', '3.11', '3.12', '3.13') }
}

$root = Split-Path $PSScriptRoot -Parent
$build = Join-Path $root 'build'
$stage = Join-Path $build 'stage'
$wheels = Join-Path $build 'wheels'
$dist = Join-Path $root 'dist'

Remove-Item $build -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage, $wheels, $dist -Force | Out-Null

Copy-Item (Join-Path $root 'spherepaint.desktop') $stage
Copy-Item (Join-Path $root 'spherepaint.action') $stage  # Krita's importer installs it into resources/actions
Copy-Item (Join-Path $root 'spherepaint') $stage -Recurse
Remove-Item (Join-Path $stage 'spherepaint\_vendor') -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $stage 'spherepaint\LICENSE')

Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($suffix in $targets[$Platform].Keys) {
    foreach ($py in $PythonVersions) {
        $tag = "cp$($py -replace '\.', '')-$suffix"
        $dl = Join-Path $wheels $tag
        $platformArgs = $targets[$Platform][$suffix] | ForEach-Object { '--platform', $_ }
        & $Python -m pip download numpy --only-binary=:all: --python-version $py --implementation cp `
            @platformArgs --no-deps -d $dl --quiet --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw "pip download failed for $tag" }
        $wheel = Get-ChildItem $dl -Filter 'numpy-*.whl' | Select-Object -First 1
        [IO.Compression.ZipFile]::ExtractToDirectory($wheel.FullName, (Join-Path $stage "spherepaint\_vendor\$tag"))
        Write-Host "  $tag <- $($wheel.Name)"
    }
}
Get-ChildItem $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

$zip = Join-Path $dist "krita-spherepaint-$Version-$Platform.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip)
Write-Host "Built $zip"
