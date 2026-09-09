param([Parameter(Mandatory=$true)][string]$Layer,[Parameter(Mandatory=$true)][string]$Background,[Parameter(Mandatory=$true)][string]$Output,[int]$TextWidth=94,[int]$TextHeight=15,[int]$Top=2,[switch]$SeparatePair)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$src=[System.Drawing.Bitmap]::FromFile($Layer)
$base=[System.Drawing.Bitmap]::FromFile($Background)
if($src.GetPixel(0,0).A -ne 0){throw 'Lettering layer must have a transparent background'}
$left=$src.Width;$right=0;$up=$src.Height;$down=0
for($y=0;$y -lt $src.Height;$y++){for($x=0;$x -lt $src.Width;$x++){if($src.GetPixel($x,$y).A -gt 16){$left=[Math]::Min($left,$x);$right=[Math]::Max($right,$x);$up=[Math]::Min($up,$y);$down=[Math]::Max($down,$y)}}}
if($right -le $left){throw 'Empty text layer'}
$dst=[System.Drawing.Bitmap]::new($base.Width,$base.Height)
$g=[System.Drawing.Graphics]::FromImage($dst)
$g.DrawImage($base,[System.Drawing.Rectangle]::new(0,0,$base.Width,$base.Height),[System.Drawing.Rectangle]::new(0,0,$base.Width,$base.Height),[System.Drawing.GraphicsUnit]::Pixel)
$g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g.PixelOffsetMode=[System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
$tx=[int][Math]::Floor(($base.Width-$TextWidth)/2)
if($SeparatePair){
  $mid=[int](($left+$right)/2)
  foreach($part in 0,1){
    $a=if($part -eq 0){$left}else{$mid};$b=if($part -eq 0){$mid}else{$right}
    $pl=$b;$pr=$a
    for($xx=$a;$xx -le $b;$xx++){for($yy=$up;$yy -le $down;$yy++){if($src.GetPixel($xx,$yy).A -gt 16){$pl=[Math]::Min($pl,$xx);$pr=[Math]::Max($pr,$xx);break}}}
    $px=if($part -eq 0){23}else{75}
    $g.DrawImage($src,[System.Drawing.Rectangle]::new($px,$Top,17,$TextHeight),[System.Drawing.Rectangle]::new($pl,$up,$pr-$pl+1,$down-$up+1),[System.Drawing.GraphicsUnit]::Pixel)
  }
}else{
  $g.DrawImage($src,[System.Drawing.Rectangle]::new($tx,$Top,$TextWidth,$TextHeight),[System.Drawing.Rectangle]::new($left,$up,$right-$left+1,$down-$up+1),[System.Drawing.GraphicsUnit]::Pixel)
}
$g.Dispose()
$dst.Save($Output,[System.Drawing.Imaging.ImageFormat]::Png)
$dst.Dispose();$src.Dispose();$base.Dispose()
Write-Output $Output
