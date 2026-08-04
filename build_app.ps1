[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Push-Location $root
try {
    python -m pip install -r (Join-Path $root 'requirements.txt') pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }

    python -m PyInstaller --noconfirm --clean (Join-Path $root 'SU2CAD.spec')
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

    $exe = Join-Path $root 'dist\SU2CAD\SU2CAD.exe'
    if (-not (Test-Path -LiteralPath $exe)) { throw "Build output not found: $exe" }
    Get-Item -LiteralPath $exe | Select-Object FullName, Length, LastWriteTime | ConvertTo-Json
}
finally {
    Pop-Location
}
