param(
  [Parameter(Mandatory=$true)][string]$ProductConfig,
  [string]$AssemblyFile = '',
  [Parameter(Mandatory=$true)][string]$OutputJson
)
$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')
. (Join-Path $here 'ProductConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot
$product = Get-AssemblySopProduct -ProjectRoot $projectRoot -ProductConfig $ProductConfig
$common = $runtime.CommonFiles
$nativeLib = $runtime.NativeLibrary
$java = $runtime.JavaCommand
if (-not (Test-Path (Join-Path $here 'build\AutoCadDiscovery.class'))) { & (Join-Path $here 'build.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$sourceModels = $product.ModelsRoot
if (-not $AssemblyFile) { $AssemblyFile = $product.FinalAssemblyPath }
$requested = Resolve-Path -LiteralPath $AssemblyFile
if (-not $requested.Path.StartsWith($sourceModels, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'AssemblyFile 必须位于产品 models_dir 目录。' }
$runRoot = Join-Path $projectRoot ('data\runs\discovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$stagedModels = Join-Path $runRoot 'models'
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
$relative = $requested.Path.Substring($sourceModels.Length).TrimStart('\')
$stagedAssembly = Join-Path $stagedModels $relative
$outputFull = [System.IO.Path]::GetFullPath($OutputJson)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull) | Out-Null
Set-CreoRuntimeEnvironment -Runtime $runtime
$classpath = (Join-Path $here 'build') + ';' + (Join-Path $common 'text\java\pfcasync.jar') + ';' + (Join-Path $common 'text\java\otk.jar')
# J-Link's official contract expects the full startup command/batch file, not a
# direct executable plus PSF arguments.  Models are retrieved through the API.
$creoCommand = $runtime.CreoCommand
Push-Location $stagedModels
try { & $java '--enable-native-access=ALL-UNNAMED' ('-Djava.library.path=' + $nativeLib) -cp $classpath AutoCadDiscovery $creoCommand $stagedAssembly $outputFull }
finally { Pop-Location }
exit $LASTEXITCODE
