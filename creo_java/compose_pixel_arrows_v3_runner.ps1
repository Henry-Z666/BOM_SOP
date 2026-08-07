param([Parameter(Mandatory=$true)][string]$BaseJpeg,[Parameter(Mandatory=$true)][string]$CalibrationJpeg,[Parameter(Mandatory=$true)][string]$OutputJpeg,[Parameter(Mandatory=$true)][int]$ExpectedArrowCount)
$ErrorActionPreference='Stop'
$python='C:\Users\10602\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python (Join-Path $PSScriptRoot 'compose_pixel_arrows_v3.py') --base $BaseJpeg --calibration $CalibrationJpeg --output $OutputJpeg --expected $ExpectedArrowCount
if($LASTEXITCODE -ne 0){throw 'Pixel V3 compositor failed'}
