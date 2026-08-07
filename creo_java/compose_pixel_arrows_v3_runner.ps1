param([Parameter(Mandatory=$true)][string]$BaseJpeg,[Parameter(Mandatory=$true)][string]$CalibrationJpeg,[Parameter(Mandatory=$true)][string]$OutputJpeg,[Parameter(Mandatory=$true)][int]$ExpectedArrowCount)
$ErrorActionPreference='Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot
& $runtime.PythonCommand (Join-Path $PSScriptRoot 'compose_pixel_arrows_v3.py') --base $BaseJpeg --calibration $CalibrationJpeg --output $OutputJpeg --expected $ExpectedArrowCount
if($LASTEXITCODE -ne 0){throw 'Pixel V3 compositor failed'}
