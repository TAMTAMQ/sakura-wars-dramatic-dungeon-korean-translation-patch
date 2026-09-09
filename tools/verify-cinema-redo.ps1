# Validate the generated-lettering composites, not the rejected font experiment.
param([string]$InputDir='work/visual-qa/cinema-redo')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$root=Split-Path $PSScriptRoot -Parent
$dir=Join-Path $root $InputDir
$records=(Get-Content -Raw (Join-Path $root 'assets/visual/cinema-untranslated-translation.json') | ConvertFrom-Json).records
$files=Get-ChildItem -LiteralPath $dir -Filter *.png -File
if($files.Count -ne $records.Count){throw "Expected $($records.Count) images; found $($files.Count)"}
$checked=0
foreach($r in $records){
 $p=Join-Path $dir $r.file
 $img=[System.Drawing.Bitmap]::FromFile($p)
 $screen=$r.file.StartsWith('top_screen')
 $refPath=if($screen){Join-Path $root ('work/visual-qa/cinema-untranslated-before/'+$r.file)}else{Join-Path $root 'assets/visual/edits/nitrofs/cinema/_templates/top_place_background.png'}
 $ref=[System.Drawing.Bitmap]::FromFile($refPath)
 if($img.Width -ne $ref.Width -or $img.Height -ne $ref.Height){throw "Dimension mismatch: $($r.file)"}
 for($y=0;$y -lt $img.Height;$y++){
  $protected=if($screen){$y -eq 0 -or $y -ge 14}else{$y -ge 19}
  if($protected){for($x=0;$x -lt $img.Width;$x++){if($img.GetPixel($x,$y).ToArgb() -ne $ref.GetPixel($x,$y).ToArgb()){throw "Background/frame mismatch: $($r.file) ($x,$y)"}}}
 }
 $img.Dispose();$ref.Dispose();$checked++
}
Write-Output "PASS: $checked filenames, native dimensions, and protected background/frame pixels"
