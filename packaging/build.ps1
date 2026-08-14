param(
  [string]$RuntimeConfig = '',
  [string]$PythonCommand = 'python',
  [string]$DistPath = '',
  [string]$WorkPath = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $DistPath) { $DistPath = Join-Path $projectRoot 'dist' }
if (-not $WorkPath) { $WorkPath = Join-Path $projectRoot 'build\pyinstaller' }

& (Join-Path $projectRoot 'creo_java\build.ps1') -RuntimeConfig $RuntimeConfig
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$discoveryClass = Join-Path $projectRoot 'creo_java\build\AutoCadDiscovery.class'
if (-not (Test-Path -LiteralPath $discoveryClass -PathType Leaf)) {
  throw 'J-Link compilation did not produce AutoCadDiscovery.class.'
}

& $PythonCommand -m PyInstaller --noconfirm --clean `
  --distpath ([System.IO.Path]::GetFullPath($DistPath)) `
  --workpath ([System.IO.Path]::GetFullPath($WorkPath)) `
  (Join-Path $PSScriptRoot 'QwenCreoSopAgent.spec')
exit $LASTEXITCODE
