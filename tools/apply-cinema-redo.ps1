$ErrorActionPreference='Stop'
$root=Split-Path $PSScriptRoot -Parent
& (Join-Path $PSScriptRoot 'verify-cinema-redo.ps1') -InputDir 'work/visual-qa/cinema-redo'
$source=Join-Path $root 'work/visual-qa/cinema-redo'
$target=Join-Path $root 'assets/visual/edits/nitrofs/cinema/미번역'
$backup=Join-Path $root 'work/visual-qa/cinema-rejected-before-redo-20260909'
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$records=(Get-Content -Raw (Join-Path $root 'assets/visual/cinema-untranslated-translation.json') | ConvertFrom-Json).records
foreach($r in $records){
 $old=Join-Path $target $r.file
 $save=Join-Path $backup $r.file
 if(!(Test-Path -LiteralPath $save)){Copy-Item -LiteralPath $old -Destination $save}
}
foreach($r in $records){Copy-Item -LiteralPath (Join-Path $source $r.file) -Destination (Join-Path $target $r.file) -Force}
& (Join-Path $PSScriptRoot 'verify-cinema-redo.ps1') -InputDir 'assets/visual/edits/nitrofs/cinema/미번역'
Write-Output "Applied $($records.Count) images; previous edits backed up at $backup"
