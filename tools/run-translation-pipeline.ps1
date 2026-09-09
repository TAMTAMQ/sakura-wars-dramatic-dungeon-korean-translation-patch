[CmdletBinding()]
param(
    [ValidateSet('Prepare', 'ExportText', 'ImportText', 'ExportVisual', 'ExportPng', 'ExportComparePng', 'InitVisualEdit', 'ImportPng', 'ExportCpk', 'ImportCpkPng', 'Validate', 'Build', 'All')]
    [string]$Mode = 'Build',

    [string]$Source = '',
    [string]$Base = '',
    [string]$TranslationTsv = '',
    [string]$VisualExportRoot = '',
    [string]$VisualPngRoot = '',
    [string]$Output = '',
    [string]$Python = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($Source)) { $Source = Join-Path $projectRoot 'Dramatic Dungeon Sakura Taisen Kimi Arugatame.nds' }
if ([string]::IsNullOrWhiteSpace($Base)) { $Base = Join-Path $projectRoot 'work\chinese-patched.nds' }
if ([string]::IsNullOrWhiteSpace($TranslationTsv)) { $TranslationTsv = Join-Path $projectRoot 'work\translations.tsv' }
if ([string]::IsNullOrWhiteSpace($VisualExportRoot)) { $VisualExportRoot = Join-Path $projectRoot 'work\visual-source' }
if ([string]::IsNullOrWhiteSpace($VisualPngRoot)) { $VisualPngRoot = Join-Path $projectRoot 'work\visual-png' }
if ([string]::IsNullOrWhiteSpace($Output)) { $Output = Join-Path $projectRoot 'out\dramatic-dungeon-kr-dev.nds' }
$sourceInventory = Join-Path $projectRoot 'work\nitrofs-inventory.json'
$baseInventory = Join-Path $projectRoot 'work\nitrofs-inventory-cn.json'
$outputInventory = Join-Path $projectRoot 'work\nitrofs-inventory-output.json'
$translationIndex = Join-Path $projectRoot 'assets\translation\index.json'
$visualIndex = Join-Path $projectRoot 'assets\visual\index.json'
$fontConfig = Join-Path $projectRoot 'config\font_layout.json'
$bannerConfig = Join-Path $projectRoot 'config\banner.json'
$lipsSpec = Join-Path $projectRoot 'assets\visual\lips-translation.json'
$visualEditRoot = Join-Path $projectRoot 'assets\visual\edits\nitrofs'
$cpkEditRoot = Join-Path $projectRoot 'assets\visual\edits\cpk'
$report = [IO.Path]::ChangeExtension($Output, '.report.json')
$buildStage = Join-Path $projectRoot 'work\build-stage.nds'
$buildStageReport = Join-Path $projectRoot 'work\build-stage.report.json'
$relocationReport = Join-Path $projectRoot 'work\relocate-overflow.report.json'
$relocationVerifyReport = Join-Path $projectRoot 'work\relocation-verify.json'

if ([string]::IsNullOrWhiteSpace($Python)) {
    $projectPython = Join-Path $projectRoot '..\..\hanpatch\venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $projectPython) { $projectPython } else { 'python' }
}

function Invoke-Python([string[]]$Arguments) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-Prepare {
    & (Join-Path $PSScriptRoot 'survey-nitrofs.ps1') -Source $Source -OutputPath $sourceInventory
    & (Join-Path $PSScriptRoot 'survey-nitrofs.ps1') -Source $Base -OutputPath $baseInventory -AllowUnregisteredSurvey
}

function Invoke-Validate {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'verify-translation-assets.py'),
        '--source', $Source, '--inventory', $sourceInventory, '--index', $translationIndex
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'manage-visual-assets.py'), 'validate',
        '--source', $Source, '--inventory', $sourceInventory, '--index', $visualIndex
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'verify-lips-consistency.py'),
        '--spec', $lipsSpec, '--translation-index', $translationIndex
    )
}

if ($Mode -in @('Prepare', 'All')) { Invoke-Prepare }
if ($Mode -in @('ExportText', 'All')) {
    Invoke-Python @((Join-Path $PSScriptRoot 'manage-translations.py'), 'export', '--index', $translationIndex, '--output', $TranslationTsv)
}
if ($Mode -eq 'ImportText') {
    Invoke-Python @((Join-Path $PSScriptRoot 'manage-translations.py'), 'import', '--index', $translationIndex, '--input', $TranslationTsv)
}
if ($Mode -in @('ExportVisual', 'All')) {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'manage-visual-assets.py'), 'extract',
        '--source', $Source, '--inventory', $sourceInventory, '--index', $visualIndex,
        '--output-root', $VisualExportRoot
    )
}
if ($Mode -in @('ExportPng', 'All')) {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'convert-visual-assets.py'),
        '--index', $visualIndex, '--source-root', $VisualExportRoot,
        '--output-root', $VisualPngRoot
    )
}
if ($Mode -in @('ExportComparePng', 'All')) {
    $priorVisualRoot = Join-Path $projectRoot 'work\visual-prior-patch'
    $priorPngRoot = Join-Path $projectRoot 'work\visual-png-prior-patch'
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'manage-visual-assets.py'), 'extract',
        '--source', $Base, '--inventory', $baseInventory, '--index', $visualIndex,
        '--output-root', $priorVisualRoot, '--variant', 'prior-patch'
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'convert-visual-assets.py'),
        '--index', $visualIndex, '--source-root', $priorVisualRoot,
        '--output-root', $priorPngRoot
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'build-visual-comparison-gallery.py'),
        '--index', $visualIndex, '--source-png-root', $VisualPngRoot,
        '--prior-png-root', $priorPngRoot,
        '--output', (Join-Path $projectRoot 'work\visual-compare\index.html')
    )
}
if ($Mode -in @('ExportCpk', 'All')) {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'extract-cpk-itoc.py'),
        '--cpk', (Join-Path $VisualExportRoot 'cpk\faCpkData.cpk'),
        '--compare-rom', $Base, '--compare-inventory', $baseInventory,
        '--output-root', (Join-Path $projectRoot 'work\cpk-extracted'),
        '--manifest', (Join-Path $projectRoot 'work\cpk-extracted\manifest.json')
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'extract-cpk-visuals.py'),
        '--cpk-manifest', (Join-Path $projectRoot 'work\cpk-extracted\manifest.json'),
        '--extracted-root', (Join-Path $projectRoot 'work\cpk-extracted'),
        '--output-root', (Join-Path $projectRoot 'work\cpk-visual-png')
    )
}
if ($Mode -eq 'InitVisualEdit') {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'initialize-visual-edits.py'),
        '--visual-manifest', (Join-Path $VisualPngRoot 'preview-manifest.json'),
        '--visual-source-root', $VisualPngRoot,
        '--visual-edit-root', $visualEditRoot,
        '--cpk-manifest', (Join-Path $projectRoot 'work\cpk-visual-png\manifest.json'),
        '--cpk-source-root', (Join-Path $projectRoot 'work\cpk-visual-png\source'),
        '--cpk-edit-root', $cpkEditRoot
    )
}
if ($Mode -eq 'ImportPng') {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'rebuild-visual-assets.py'),
        '--index', $visualIndex, '--source-root', $VisualExportRoot,
        '--png-root', $visualEditRoot,
        '--replacement-root', (Join-Path $projectRoot 'assets\visual\replacements'),
        '--report', (Join-Path $projectRoot 'work\visual-import.report.json'),
        '--update-index'
    )
}
if ($Mode -eq 'ImportCpkPng') {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'rebuild-cpk-visuals.py'),
        '--cpk', (Join-Path $VisualExportRoot 'cpk\faCpkData.cpk'),
        '--cpk-manifest', (Join-Path $projectRoot 'work\cpk-extracted\manifest.json'),
        '--visual-manifest', (Join-Path $projectRoot 'work\cpk-visual-png\manifest.json'),
        '--visual-root', (Join-Path $projectRoot 'work\cpk-visual-png'),
        '--edit-root', $cpkEditRoot,
        '--visual-index', $visualIndex,
        '--output', (Join-Path $projectRoot 'assets\visual\replacements\cpk\faCpkData.cpk'),
        '--report', (Join-Path $projectRoot 'work\cpk-visual-import.report.json'),
        '--update-index'
    )
}
if ($Mode -in @('Validate', 'Build', 'All')) { Invoke-Validate }
if ($Mode -in @('Build', 'All')) {
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'build-translation.py'),
        '--source', $Source, '--base', $Base,
        '--source-inventory', $sourceInventory, '--base-inventory', $baseInventory,
        '--translation-index', $translationIndex, '--visual-index', $visualIndex,
        '--font-config', $fontConfig, '--banner-config', $bannerConfig,
        '--output', $buildStage, '--report', $buildStageReport
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'relocate-overflow-strings.py'),
        '--source', $Source, '--rom', $buildStage, '--inventory', $sourceInventory,
        '--index', $translationIndex, '--build-report', $buildStageReport,
        '--output', $Output, '--report', $relocationReport
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'verify-relocation.py'),
        '--before', $buildStage, '--after', $Output, '--index', $translationIndex,
        '--build-report', $buildStageReport, '--relocation-report', $relocationReport,
        '--report', $relocationVerifyReport
    )
    Invoke-Python @(
        (Join-Path $PSScriptRoot 'verify-lips-consistency.py'),
        '--spec', $lipsSpec, '--translation-index', $translationIndex,
        '--visual-index', $visualIndex, '--rom', $Output, '--build-report', $buildStageReport
    )

    Invoke-Python @(
        (Join-Path $PSScriptRoot 'finalize-build-report.py'),
        '--build-report', $buildStageReport, '--relocation-report', $relocationReport,
        '--relocation-verify-report', $relocationVerifyReport,
        '--rom', $Output, '--output', $report
    )

    & (Join-Path $PSScriptRoot 'survey-nitrofs.ps1') -Source $Output -OutputPath $outputInventory -AllowUnregisteredSurvey
}
