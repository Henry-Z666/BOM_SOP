param(
  [Parameter(Mandatory=$true)][string]$ProductConfig,
  [string]$AssemblyFile = '',
  [Parameter(Mandatory=$true)][string]$OutputJson
)
$here = $PSScriptRoot; $projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')
. (Join-Path $here 'ProductConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot
$product = Get-AssemblySopProduct -ProjectRoot $projectRoot -ProductConfig $ProductConfig
$common = $runtime.CommonFiles; $nativeLib = $runtime.NativeLibrary; $sourceModels = $product.ModelsRoot
if (-not $AssemblyFile) { $AssemblyFile = $product.FinalAssemblyPath }
$requested = Resolve-Path -LiteralPath $AssemblyFile
if (-not $requested.Path.StartsWith($sourceModels, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'AssemblyFile 必须位于产品 models_dir 目录。' }
$basisClass = Join-Path $here 'build\CameraBasisDiscovery.class'; $basisSource = Join-Path $here 'src\CameraBasisDiscovery.java'
if (-not (Test-Path $basisClass) -or (Get-Item $basisSource).LastWriteTimeUtc -gt (Get-Item $basisClass).LastWriteTimeUtc) { & (Join-Path $here 'build.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$runRoot = Join-Path $projectRoot ('data\runs\camera-calibration-' + (Get-Date -Format 'yyyyMMdd-HHmmss')); $stagedModels = Join-Path $runRoot 'models'
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null; Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
Copy-Item -LiteralPath (Join-Path $here 'isolated_config.pro') -Destination (Join-Path $stagedModels 'config.pro') -Force
$relative = $requested.Path.Substring($sourceModels.Length).TrimStart('\'); $stagedAssembly = Join-Path $stagedModels $relative
$outputFull = [System.IO.Path]::GetFullPath($OutputJson); New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull) | Out-Null
Set-CreoRuntimeEnvironment -Runtime $runtime
$classpath = (Join-Path $here 'build') + ';' + (Join-Path $common 'text\java\pfcasync.jar') + ';' + (Join-Path $common 'text\java\otk.jar')
$java = $runtime.JavaCommand; $creoCommand = $runtime.CreoCommand
Push-Location $stagedModels
try {
  & $java '--enable-native-access=ALL-UNNAMED' ('-Djava.library.path=' + $nativeLib) '-cp' $classpath 'CameraBasisDiscovery' $creoCommand $stagedAssembly $outputFull
  $exitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $exitCode
