[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path ([Environment]::GetFolderPath('Desktop')) 'SketchUp-CAD'),
    [switch]$NoOpen,
    [switch]$DisableOcclusion,
    [switch]$IncludeHiddenSectionEdges,
    [switch]$NoDimensions,
    [switch]$NoMaterials,
    [ValidateRange(0, 1000000)]
    [int]$MaxBlockLines = 2500,
    [ValidateSet('Light', 'Balanced', 'Precise')]
    [string]$Quality = 'Balanced',
    [ValidateSet('AUTO', 'A0', 'A1', 'A2', 'A3', 'A4')]
    [string]$PaperSize = 'AUTO'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$rubyExporter = Join-Path $scriptDirectory 'export_current_view.rb'
$dxfBuilder = Join-Path $scriptDirectory 'build_dxf.py'
$bridgeMain = Join-Path $env:APPDATA 'SketchUp\SketchUp 2026\SketchUp\Plugins\codex_sketchup_bridge\main.rb'

if (-not (Test-Path -LiteralPath $bridgeMain)) {
    throw "Codex SketchUp Bridge was not found: $bridgeMain"
}

$tokenMatch = Select-String -LiteralPath $bridgeMain -Pattern "TOKEN\s*=\s*'([^']+)'" | Select-Object -First 1
if (-not $tokenMatch) {
    throw 'Could not read the local SketchUp bridge token.'
}
$token = $tokenMatch.Matches[0].Groups[1].Value
$headers = @{ 'X-Codex-SketchUp-Token' = $token }
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/health' -Method Get
if (-not $health.ok -or -not $health.running) {
    throw 'SketchUp bridge is not running. Open SketchUp and enable Codex SketchUp Bridge.'
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$safeTitle = [IO.Path]::GetFileNameWithoutExtension([string]$health.title) -replace '[^A-Za-z0-9_-]', '_'
if ([string]::IsNullOrWhiteSpace($safeTitle)) { $safeTitle = 'sketchup-view' }
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$jsonPath = Join-Path $OutputDirectory "${safeTitle}_current_view_${timestamp}.json"
$dxfPath = Join-Path $OutputDirectory "${safeTitle}_current_view_${timestamp}.dxf"

function Convert-ToRubyLiteral([string]$Value) {
    return $Value.Replace('\', '/').Replace("'", "\\'")
}

$rubyPathLiteral = Convert-ToRubyLiteral $rubyExporter
$jsonPathLiteral = Convert-ToRubyLiteral $jsonPath
$occlusionLiteral = if ($DisableOcclusion) { 'false' } else { 'true' }
$strictSectionLiteral = if ($IncludeHiddenSectionEdges) { 'false' } else { 'true' }
$materialsLiteral = if ($NoMaterials) { 'false' } else { 'true' }
$qualityLiteral = $Quality.ToLowerInvariant()
$rubyCode = "load('$rubyPathLiteral'); result = SketchupCurrentViewCad.export('$jsonPathLiteral', occlusion: $occlusionLiteral, strict_section_occlusion: $strictSectionLiteral, materials: $materialsLiteral, quality: '$qualityLiteral'); puts JSON.generate(result); result"
$requestBody = @{
    command = 'run_ruby'
    args = @{ code = $rubyCode; file = $rubyExporter }
    timeout_ms = 600000
} | ConvertTo-Json -Depth 8

$response = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/command' -Method Post -Headers $headers -ContentType 'application/json' -Body $requestBody -TimeoutSec 620
if (-not $response.ok) {
    throw "SketchUp extraction failed: $($response.error)"
}
if (-not (Test-Path -LiteralPath $jsonPath)) {
    throw "SketchUp extraction did not create: $jsonPath"
}
$extractResult = $response.stdout -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Select-Object -Last 1 | ConvertFrom-Json

$pythonCommand = Get-Command python -ErrorAction Stop
$arguments = @($dxfBuilder, $jsonPath, $dxfPath)
if ($NoDimensions) { $arguments += '--no-dimensions' }
$arguments += @('--max-block-lines', $MaxBlockLines, '--paper', $PaperSize)
$builderOutput = & $pythonCommand.Source @arguments
if ($LASTEXITCODE -ne 0) {
    throw "DXF builder failed with exit code $LASTEXITCODE"
}
$builderResult = $builderOutput | Select-Object -Last 1 | ConvertFrom-Json
$orientationCode = if ($builderResult.paperOrientation -eq 'Landscape') { 'L' } else { 'P' }
$finalDxfPath = Join-Path $OutputDirectory "${safeTitle}_current_view_$($builderResult.paperSize)-${orientationCode}_${timestamp}.dxf"
Move-Item -LiteralPath $dxfPath -Destination $finalDxfPath
$dxfPath = $finalDxfPath

if (-not $NoOpen) {
    $autodeskLauncher = 'C:\Program Files\Common Files\Autodesk Shared\AcShellEx\AcLauncher.exe'
    $acadPath = 'C:\Program Files\Autodesk\AutoCAD 2025\acad.exe'
    if (Test-Path -LiteralPath $autodeskLauncher) {
        Start-Process -FilePath $autodeskLauncher -ArgumentList @('/O', "`"$dxfPath`"")
    } elseif (-not (Test-Path -LiteralPath $acadPath)) {
        $acadPath = Get-ChildItem -LiteralPath 'C:\Program Files\Autodesk' -Filter 'acad.exe' -Recurse -File -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
        if ($acadPath) {
            Start-Process -FilePath $acadPath -ArgumentList @("`"$dxfPath`"")
        } else {
            throw 'AutoCAD executable was not found.'
        }
    } else {
        Start-Process -FilePath $acadPath -ArgumentList @("`"$dxfPath`"")
    }
}

[ordered]@{
    ok = $true
    sketchupVersion = $health.sketchup_version
    modelPath = $health.model_path
    lineworkJson = $jsonPath
    dxf = $dxfPath
    linesBeforeMerge = $builderResult.linesBeforeMerge
    linesAfterMerge = $builderResult.linesAfterMerge
    curves = $builderResult.curves
    blocks = $builderResult.blocks
    blockReferences = $builderResult.blockReferences
    blockLinesBeforeOptimization = $builderResult.blockLinesBeforeOptimization
    blockLinesAfterOptimization = $builderResult.blockLinesAfterOptimization
    simplifiedBlocks = $builderResult.simplifiedBlocks
    maxBlockLines = $builderResult.maxBlockLines
    quality = $qualityLiteral
    plantBlockReferences = $builderResult.plantBlockReferences
    materialFaces = $builderResult.materialFaces
    materialHatches = $builderResult.materialHatches
    materialCount = $builderResult.materialCount
    occluderFaces = $builderResult.occluderFaces
    sectionLines = $extractResult.sectionLines
    silhouetteEdges = $extractResult.silhouetteEdges
    skippedOccluded = $extractResult.skippedOccluded
    scale = $builderResult.scale
    paperSize = $builderResult.paperSize
    paperOrientation = $builderResult.paperOrientation
    paperDimensionsMm = $builderResult.paperDimensionsMm
    layout = $builderResult.layout
    sourcePerspective = $builderResult.sourcePerspective
    auditErrors = $builderResult.auditErrors
    openedInCad = -not $NoOpen
} | ConvertTo-Json -Depth 6
