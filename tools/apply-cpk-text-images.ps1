param(
    [Parameter(Mandatory=$true)][string]$AssetDir,
    [Parameter(Mandatory=$true)][string]$Manifest,
    [Parameter(Mandatory=$true)][string]$BackupDir,
    [Parameter(Mandatory=$true)][string]$QaSheet
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$records = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

foreach($record in $records) {
    $target = Join-Path $AssetDir $record.file
    if(Test-Path -LiteralPath $target) {
        $backup = Join-Path $BackupDir $record.file
        if(-not (Test-Path -LiteralPath $backup)) { Copy-Item -LiteralPath $target -Destination $backup }
    }
    if($record.copy) {
        Copy-Item -LiteralPath (Join-Path $AssetDir $record.copy) -Destination $target -Force
        continue
    }
    $source = [System.Drawing.Image]::FromFile($record.generated)
    $bitmap = New-Object System.Drawing.Bitmap 256,192
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.DrawImage($source,[System.Drawing.Rectangle]::new(0,0,256,192),[System.Drawing.Rectangle]::new(0,0,$source.Width,$source.Height),[System.Drawing.GraphicsUnit]::Pixel)
    $graphics.Dispose()
    $source.Dispose()
    $bitmap.Save($target,[System.Drawing.Imaging.ImageFormat]::Png)
    $bitmap.Dispose()
}

$previewNames = @(
    'episode-1-orleans-no-shoujo.png',
    'episode-2-matenrou-no-majin.png',
    'episode-3-louvre-no-hihou.png',
    'episode-final-shiroki-hyouga-no-hate-ni.png',
    'shuugeki-the-end.png',
    'logo-sakura-taisen.png'
)
$sheet = New-Object System.Drawing.Bitmap 768,424
$g = [System.Drawing.Graphics]::FromImage($sheet)
$g.Clear([System.Drawing.Color]::White)
$font = New-Object System.Drawing.Font 'Arial',9
for($i=0; $i -lt $previewNames.Count; $i++) {
    $x = ($i % 3) * 256
    $y = [Math]::Floor($i / 3) * 212
    $image = [System.Drawing.Image]::FromFile((Join-Path $AssetDir $previewNames[$i]))
    $g.DrawString($previewNames[$i],$font,[System.Drawing.Brushes]::Black,$x,$y)
    $g.DrawImageUnscaled($image,$x,$y+20)
    $image.Dispose()
}
$font.Dispose(); $g.Dispose()
$sheet.Save($QaSheet,[System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()
Write-Output 'Applied CPK text-image translations'
