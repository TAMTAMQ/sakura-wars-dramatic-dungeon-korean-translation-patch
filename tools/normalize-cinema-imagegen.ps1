param([Parameter(Mandatory=$true)][string]$Generated,[Parameter(Mandatory=$true)][string]$Original,[Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$src=[System.Drawing.Bitmap]::FromFile($Generated)
$orig=[System.Drawing.Bitmap]::FromFile($Original)
# Generated narrow strips can have white padding. Locate the actual full-width strip.
$top=0; $bottom=$src.Height-1
for($y=0;$y -lt $src.Height;$y++){
  $dark=0
  for($x=0;$x -lt $src.Width;$x+=8){$c=$src.GetPixel($x,$y);if([Math]::Min($c.R,[Math]::Min($c.G,$c.B)) -lt 220){$dark++}}
  if($dark -gt ($src.Width/8)*0.6){$top=$y;break}
}
for($y=$src.Height-1;$y -ge $top;$y--){
  $dark=0
  for($x=0;$x -lt $src.Width;$x+=8){$c=$src.GetPixel($x,$y);if([Math]::Min($c.R,[Math]::Min($c.G,$c.B)) -lt 220){$dark++}}
  if($dark -gt ($src.Width/8)*0.6){$bottom=$y;break}
}
$dst=[System.Drawing.Bitmap]::new($orig.Width,$orig.Height)
$g=[System.Drawing.Graphics]::FromImage($dst)
$g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode=[System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$g.DrawImage($src,[System.Drawing.Rectangle]::new(0,0,$orig.Width,$orig.Height),[System.Drawing.Rectangle]::new(0,$top,$src.Width,$bottom-$top+1),[System.Drawing.GraphicsUnit]::Pixel)
$g.Dispose()
# Restore the original non-text outer rails exactly, avoiding new generated framing.
for($y=0;$y -lt $orig.Height;$y++){
  $keep=if($orig.Height -eq 24){$y -ge 19}else{$y -eq 0 -or $y -ge 14}
  if($keep){for($x=0;$x -lt $orig.Width;$x++){$dst.SetPixel($x,$y,$orig.GetPixel($x,$y))}}
}
$dst.Save($Output,[System.Drawing.Imaging.ImageFormat]::Png)
$dst.Dispose();$src.Dispose();$orig.Dispose()
Write-Output "Saved $Output; crop=$top..$bottom"
