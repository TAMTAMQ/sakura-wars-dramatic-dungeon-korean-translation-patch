param([string]$Manifest)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$project = Split-Path $PSScriptRoot -Parent
$records = Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json
$destination = Join-Path $project 'assets/visual/edits/nitrofs/icat'
$qa = Join-Path $project 'work/visual-qa/icat-v2'
New-Item -ItemType Directory -Force -Path $qa | Out-Null
$sheet = New-Object System.Drawing.Bitmap 768,672
$sg = [System.Drawing.Graphics]::FromImage($sheet)
$sg.Clear([System.Drawing.Color]::White)
$font = New-Object System.Drawing.Font 'Arial',9
$i = 0
foreach($record in $records) {
    $name = 'eyecatch' + $record.id + '.png'
    Copy-Item -LiteralPath $record.path -Destination (Join-Path $qa ('generated-' + $name)) -Force
    $original = [System.Drawing.Bitmap]::FromFile((Join-Path $project "work/visual-qa/icat-before/$name"))
    $generated = [System.Drawing.Bitmap]::FromFile($record.path)
    $small = New-Object System.Drawing.Bitmap 256,192
    $g = [System.Drawing.Graphics]::FromImage($small)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($generated, [System.Drawing.Rectangle]::new(0,0,256,192), [System.Drawing.Rectangle]::new(0,0,$generated.Width,$generated.Height),[System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    $out = New-Object System.Drawing.Bitmap 256,192
    $g = [System.Drawing.Graphics]::FromImage($out)
    $g.DrawImage($original,[System.Drawing.Rectangle]::new(0,0,256,192),[System.Drawing.Rectangle]::new(0,0,256,192),[System.Drawing.GraphicsUnit]::Pixel)
    $g.Dispose()
    # Adopt only the generated title region. Feather at original boundary rows
    # and the right edge, retaining the scene and existing logo pixel-for-pixel.
    for($y=12; $y -lt 34; $y++) {
        for($x=0; $x -lt 130; $x++) {
            $a = [Math]::Min(1.0,[Math]::Min(($y-11)/3.0,(34-$y)/3.0))
            $a = [Math]::Min($a,[Math]::Min(1.0,(130-$x)/10.0))
            $old = $original.GetPixel($x,$y)
            $new = $small.GetPixel($x,$y)
            $r = [int][Math]::Round($old.R*(1-$a)+$new.R*$a)
            $gg = [int][Math]::Round($old.G*(1-$a)+$new.G*$a)
            $b = [int][Math]::Round($old.B*(1-$a)+$new.B*$a)
            $out.SetPixel($x,$y,[System.Drawing.Color]::FromArgb($r,$gg,$b))
        }
    }
    $out.Save((Join-Path $destination $name),[System.Drawing.Imaging.ImageFormat]::Png)
    $out.Save((Join-Path $qa $name),[System.Drawing.Imaging.ImageFormat]::Png)
    $outside = 0
    for($vy=0; $vy -lt 192; $vy++) {
        for($vx=0; $vx -lt 256; $vx++) {
            if(($vx -ge 130 -or $vy -lt 12 -or $vy -ge 34) -and $out.GetPixel($vx,$vy).ToArgb() -ne $original.GetPixel($vx,$vy).ToArgb()) { $outside++ }
        }
    }
    if($outside -ne 0) { throw "$name changed $outside pixels outside the title" }
    $sx = ($i % 3)*256
    $sy = [Math]::Floor($i/3)*134
    $sg.DrawString($name,$font,[System.Drawing.Brushes]::Black,$sx,$sy)
    $sg.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
    $sg.DrawImage($out,[System.Drawing.Rectangle]::new($sx,$sy+20,256,64),[System.Drawing.Rectangle]::new(0,6,128,32),[System.Drawing.GraphicsUnit]::Pixel)
    $original.Dispose(); $generated.Dispose(); $small.Dispose(); $out.Dispose()
    $i++
}
$sg.Dispose(); $font.Dispose()
$sheet.Save((Join-Path $qa 'titles.png'),[System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()
Write-Output "Applied $i title repairs"
