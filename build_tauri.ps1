[CmdletBinding()]
param(
    [switch]$SkipDependencies
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$desktop = Join-Path $root 'desktop'
$binaryDirectory = Join-Path $desktop 'src-tauri\binaries'
$sidecarSource = Join-Path $root 'dist\su2cad-core.exe'
$sidecarTarget = Join-Path $binaryDirectory 'su2cad-core-x86_64-pc-windows-msvc.exe'

Push-Location $root
try {
    if (-not $SkipDependencies) {
        python -m pip install -r (Join-Path $root 'requirements.txt') pyinstaller
        if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
        Push-Location $desktop
        try {
            npm install
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        finally { Pop-Location }
    }

    python -m PyInstaller --noconfirm --clean (Join-Path $root 'SU2CADCore.spec')
    if ($LASTEXITCODE -ne 0) { throw 'Python sidecar build failed.' }
    if (-not (Test-Path -LiteralPath $sidecarSource)) { throw "Sidecar not found: $sidecarSource" }
    New-Item -ItemType Directory -Force -Path $binaryDirectory | Out-Null
    Copy-Item -LiteralPath $sidecarSource -Destination $sidecarTarget -Force

    Push-Location $desktop
    try {
        npm run tauri build
        if ($LASTEXITCODE -ne 0) { throw 'Tauri build failed.' }
    }
    finally { Pop-Location }

    $installer = Get-ChildItem -LiteralPath (Join-Path $desktop 'src-tauri\target\release\bundle\nsis') -Filter '*.exe' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $installer) { throw 'NSIS installer was not generated.' }
    [pscustomobject]@{
        Sidecar = $sidecarTarget
        Application = (Join-Path $desktop 'src-tauri\target\release\su2cad.exe')
        Installer = $installer.FullName
    } | ConvertTo-Json
}
finally {
    Pop-Location
}
