param(
  [Parameter(Mandatory=$true)][string]$Path,
  [ValidateSet('portrait','square')][string]$Frame = 'portrait'
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$full = [System.IO.Path]::GetFullPath($Path)
if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { throw "image not found: $full" }

$targetWidth = if ($Frame -eq 'square') { 1600 } else { 1200 }
$targetHeight = 1600
$source = [System.Drawing.Image]::FromFile($full)
try {
  if ($source.Width -eq $targetWidth -and $source.Height -eq $targetHeight) { return }
  if ($source.Width -ne 1800 -or $source.Height -ne 2400) {
    throw "unexpected Creo raster size $($source.Width)x$($source.Height); expected 1800x2400"
  }

  $left = [int](($source.Width - $targetWidth) / 2)
  $top = [int](($source.Height - $targetHeight) / 2)
  $bitmap = New-Object System.Drawing.Bitmap($targetWidth, $targetHeight, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
  try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
      $graphics.DrawImage(
        $source,
        (New-Object System.Drawing.Rectangle(0, 0, $targetWidth, $targetHeight)),
        (New-Object System.Drawing.Rectangle($left, $top, $targetWidth, $targetHeight)),
        [System.Drawing.GraphicsUnit]::Pixel
      )
    } finally { $graphics.Dispose() }

    $encoder = [System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | Where-Object MimeType -eq 'image/jpeg' | Select-Object -First 1
    $quality = New-Object System.Drawing.Imaging.EncoderParameters(1)
    $quality.Param[0] = New-Object System.Drawing.Imaging.EncoderParameter([System.Drawing.Imaging.Encoder]::Quality, [long]100)
    # Keep the temporary name short because run output directories are deep,
    # and GDI+ may surface a generic Save error when the full temporary path
    # approaches the legacy Windows path limit.
    $temporary = Join-Path (Split-Path -Parent $full) ('.f-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.jpg')
    try {
      $saved = $false
      for ($saveAttempt = 1; $saveAttempt -le 3; $saveAttempt++) {
        try {
          $bitmap.Save($temporary, $encoder, $quality)
          $saved = $true
          break
        }
        catch {
          Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
          if ($saveAttempt -eq 3) { throw }
          Start-Sleep -Milliseconds (100 * $saveAttempt)
        }
      }
      if (-not $saved) { throw "Unable to save fixed-frame JPEG: $full" }
    } finally { $quality.Dispose() }
  } finally { $bitmap.Dispose() }
} finally { $source.Dispose() }

Move-Item -LiteralPath $temporary -Destination $full -Force
