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

$requiredClasses = @('AutoCadDiscovery.class', 'NativeArrowWorker.class')
foreach ($className in $requiredClasses) {
  $classPath = Join-Path $projectRoot (Join-Path 'creo_java\build' $className)
  if (-not (Test-Path -LiteralPath $classPath -PathType Leaf)) {
    throw "J-Link compilation did not produce $className."
  }
}

& $PythonCommand -m PyInstaller --noconfirm --clean `
  --distpath ([System.IO.Path]::GetFullPath($DistPath)) `
  --workpath ([System.IO.Path]::GetFullPath($WorkPath)) `
  (Join-Path $PSScriptRoot 'CreoSopAgent.spec')
exit $LASTEXITCODE
