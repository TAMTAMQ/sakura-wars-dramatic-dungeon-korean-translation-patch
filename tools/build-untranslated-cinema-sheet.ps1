param(
    [Parameter(Mandatory=$true)][string]$InputDir,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$files = Get-ChildItem -LiteralPath $InputDir -File -Filter '*.png' | Sort-Object Name
$columns = 4
$cellWidth = 470
$cellHeight = 130
$rows = [Math]::Ceiling($files.Count / $columns)
$sheet = New-Object System.Drawing.Bitmap ($columns * $cellWidth),($rows * $cellHeight)
$graphics = [System.Drawing.Graphics]::FromImage($sheet)
$graphics.Clear([System.Drawing.Color]::White)
$graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::NearestNeighbor
$font = New-Object System.Drawing.Font 'Arial',10
for($index = 0; $index -lt $files.Count; $index++) {
    $image = [System.Drawing.Image]::FromFile($files[$index].FullName)
    $x = ($index % $columns) * $cellWidth
    $y = [Math]::Floor($index / $columns) * $cellHeight
    $graphics.DrawString($files[$index].Name,$font,[System.Drawing.Brushes]::Black,$x + 4,$y + 3)
    $scale = 4
    $graphics.DrawImage($image,[System.Drawing.Rectangle]::new($x,$y + 23,$image.Width*$scale,$image.Height*$scale),[System.Drawing.Rectangle]::new(0,0,$image.Width,$image.Height),[System.Drawing.GraphicsUnit]::Pixel)
    $image.Dispose()
}
$font.Dispose()
$graphics.Dispose()
$sheet.Save($Output,[System.Drawing.Imaging.ImageFormat]::Png)
$sheet.Dispose()
Write-Output "Built contact sheet for $($files.Count) images"
