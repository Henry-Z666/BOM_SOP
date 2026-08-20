param(
  [Parameter(Mandatory=$true)][string]$ModelsDirectory,
  [Parameter(Mandatory=$true)][string]$AssemblyRelativePath,
  [Parameter(Mandatory=$true)][string]$RunWorkspace,
  [string]$RuntimeConfig = ''
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')

$sourceModels = [System.IO.Path]::GetFullPath($ModelsDirectory)
if (-not (Test-Path -LiteralPath $sourceModels -PathType Container)) {
  throw "CAD models directory does not exist: $sourceModels"
}
if ([System.IO.Path]::IsPathRooted($AssemblyRelativePath)) {
  throw 'AssemblyRelativePath must be relative to the CAD models directory.'
}
$requested = [System.IO.Path]::GetFullPath((Join-Path $sourceModels $AssemblyRelativePath))
$sourcePrefix = $sourceModels.TrimEnd('\') + '\'
if (-not $requested.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'Final assembly escapes the CAD models directory.'
}
if (-not (Test-Path -LiteralPath $requested -PathType Leaf)) {
  throw "Final assembly does not exist: $AssemblyRelativePath"
}

$runRoot = [System.IO.Path]::GetFullPath($RunWorkspace)
$runPrefix = $runRoot.TrimEnd('\') + '\'
if ($runRoot.Equals($sourceModels, [System.StringComparison]::OrdinalIgnoreCase) -or
    $runRoot.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    $sourceModels.StartsWith($runPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw 'Discovery workspace must be isolated from the source CAD directory.'
}
if (Test-Path -LiteralPath $runRoot) {
  throw "Discovery workspace already exists and cannot be reused: $runRoot"
}
New-Item -ItemType Directory -Path $runRoot | Out-Null
$stagedModels = Join-Path $runRoot 'models'
Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
$relative = $requested.Substring($sourcePrefix.Length)
$stagedAssembly = Join-Path $stagedModels $relative
$outputFull = Join-Path $runRoot 'cad-discovery.json'
$traceFull = Join-Path $runRoot 'discovery.log'
$completeFull = Join-Path $runRoot 'discovery.complete'

$runtime = Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig
$javaBuild = Join-Path $here 'build'
$discoveryClass = Join-Path $javaBuild 'AutoCadDiscovery.class'
if (-not (Test-Path -LiteralPath $discoveryClass)) {
  New-Item -ItemType Directory -Force -Path $javaBuild | Out-Null
  $compileClasspath = (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' +
    (Join-Path $runtime.CommonFiles 'text\java\otk.jar')
  & $runtime.JavacCommand --release 17 -cp $compileClasspath -d $javaBuild `
    (Join-Path $here 'src\AutoCadDiscovery.java')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Set-CreoRuntimeEnvironment -Runtime $runtime
$null = Start-CreoNameService -Runtime $runtime
$classpath = $javaBuild + ';' +
  (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' +
  (Join-Path $runtime.CommonFiles 'text\java\otk.jar')

Push-Location $stagedModels
$javaExitCode = 1
try {
  & $runtime.JavaCommand '--enable-native-access=ALL-UNNAMED' `
    ('-Djava.library.path=' + $runtime.NativeLibrary) -cp $classpath `
    AutoCadDiscovery $runtime.CreoCommand $stagedAssembly $outputFull $completeFull `
    *> $traceFull
  $javaExitCode = $LASTEXITCODE
}
finally { Pop-Location }
if ($javaExitCode -ne 0) { exit $javaExitCode }
if (-not (Test-Path -LiteralPath $outputFull -PathType Leaf)) {
  throw 'Creo discovery did not produce an output file.'
}
Write-Output ("[DISCOVERY] output " + $outputFull)
