[CmdletBinding()]
param(
    [string]$Version = '0.8.1'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$release = Join-Path $root 'release'
$staging = Join-Path $env:TEMP ("su2cad-plugins-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $release -Force | Out-Null
New-Item -ItemType Directory -Path $staging -Force | Out-Null

try {
    $rbzRoot = Join-Path $staging 'rbz'
    New-Item -ItemType Directory -Path $rbzRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'plugins\sketchup\su2cad_bridge.rb') -Destination $rbzRoot
    Copy-Item -LiteralPath (Join-Path $root 'plugins\sketchup\su2cad_bridge') -Destination $rbzRoot -Recurse
    $rbzZip = Join-Path $staging 'sketchup.zip'
    Compress-Archive -Path (Join-Path $rbzRoot '*') -DestinationPath $rbzZip -CompressionLevel Optimal
    $rbz = Join-Path $release "SU2CAD-SketchUp-Bridge-$Version.rbz"
    Copy-Item -LiteralPath $rbzZip -Destination $rbz -Force

    $cad = Join-Path $release "SU2CAD-AutoCAD-Helper-$Version.zip"
    if (Test-Path -LiteralPath $cad) { Remove-Item -LiteralPath $cad -Force }
    Compress-Archive -Path (Join-Path $root 'plugins\autocad\SU2CAD.bundle') -DestinationPath $cad -CompressionLevel Optimal

    $combinedRoot = Join-Path $staging "SU2CAD-Plugins-$Version"
    New-Item -ItemType Directory -Path $combinedRoot | Out-Null
    Copy-Item -LiteralPath (Join-Path $root 'plugins\sketchup') -Destination $combinedRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $root 'plugins\autocad') -Destination $combinedRoot -Recurse
    Copy-Item -LiteralPath (Join-Path $root 'plugins\Install-SU2CAD-Plugins.ps1') -Destination $combinedRoot
    Copy-Item -LiteralPath (Join-Path $root 'plugins\Install-SU2CAD-Plugins.cmd') -Destination $combinedRoot
    Copy-Item -LiteralPath (Join-Path $root 'plugins\README-安装说明.md') -Destination $combinedRoot
    $combined = Join-Path $release "SU2CAD-Plugins-$Version.zip"
    if (Test-Path -LiteralPath $combined) { Remove-Item -LiteralPath $combined -Force }
    Compress-Archive -Path $combinedRoot -DestinationPath $combined -CompressionLevel Optimal

    @($rbz, $cad, $combined) | ForEach-Object {
        $item = Get-Item -LiteralPath $_
        [pscustomobject]@{
            File = $item.FullName
            Bytes = $item.Length
            SHA256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
        }
    } | ConvertTo-Json
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
