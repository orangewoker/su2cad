[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$PayloadRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$SketchUpSource = Join-Path $PayloadRoot 'sketchup'
$CadSource = Join-Path $PayloadRoot 'autocad\SU2CAD.bundle'

if (-not (Test-Path -LiteralPath (Join-Path $SketchUpSource 'su2cad_bridge.rb'))) {
    throw 'SketchUp plugin payload is missing.'
}
if (-not (Test-Path -LiteralPath (Join-Path $CadSource 'PackageContents.xml'))) {
    throw 'AutoCAD plugin payload is missing.'
}

$versions = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$profileRoot = Join-Path $env:APPDATA 'SketchUp'
if (Test-Path -LiteralPath $profileRoot) {
    Get-ChildItem -LiteralPath $profileRoot -Directory -Filter 'SketchUp *' | ForEach-Object {
        [void]$versions.Add(($_.Name -replace '^SketchUp\s+', ''))
    }
}

$programRoots = @($env:ProgramW6432, $env:ProgramFiles, ${env:ProgramFiles(x86)}) |
    Where-Object { $_ } | Select-Object -Unique
foreach ($root in $programRoots) {
    $sketchupRoot = Join-Path $root 'SketchUp'
    if (-not (Test-Path -LiteralPath $sketchupRoot)) { continue }
    Get-ChildItem -LiteralPath $sketchupRoot -Directory -Filter 'SketchUp *' | ForEach-Object {
        $directExe = Join-Path $_.FullName 'SketchUp.exe'
        $nestedExe = Join-Path $_.FullName 'SketchUp\SketchUp.exe'
        if ((Test-Path -LiteralPath $directExe) -or (Test-Path -LiteralPath $nestedExe)) {
            [void]$versions.Add(($_.Name -replace '^SketchUp\s+', ''))
        }
    }
}

$installedVersions = @()
foreach ($version in ($versions | Sort-Object -Descending)) {
    $pluginDirectory = Join-Path $profileRoot "SketchUp $version\SketchUp\Plugins"
    New-Item -ItemType Directory -Path $pluginDirectory -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $SketchUpSource 'su2cad_bridge.rb') -Destination $pluginDirectory -Force
    $targetFolder = Join-Path $pluginDirectory 'su2cad_bridge'
    if (Test-Path -LiteralPath $targetFolder) {
        Remove-Item -LiteralPath $targetFolder -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $SketchUpSource 'su2cad_bridge') -Destination $targetFolder -Recurse -Force
    $installedVersions += $version
}

$cadPluginRoot = Join-Path $env:APPDATA 'Autodesk\ApplicationPlugins'
$cadTarget = Join-Path $cadPluginRoot 'SU2CAD.bundle'
New-Item -ItemType Directory -Path $cadPluginRoot -Force | Out-Null
if (Test-Path -LiteralPath $cadTarget) {
    Remove-Item -LiteralPath $cadTarget -Recurse -Force
}
Copy-Item -LiteralPath $CadSource -Destination $cadTarget -Recurse -Force

$summary = [ordered]@{
    ok = $true
    sketchUpVersions = $installedVersions
    autoCADBundle = $cadTarget
    restartRequired = $true
}
$summary | ConvertTo-Json -Depth 4
Write-Host ''
Write-Host 'SU2CAD plugins installed. Restart SketchUp and AutoCAD.' -ForegroundColor Green
Read-Host 'Press Enter to close'
