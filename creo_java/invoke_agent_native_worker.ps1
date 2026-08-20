param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [Parameter(Mandatory=$true)][string]$PreparedModelsRoot,
  [Parameter(Mandatory=$true)][string]$PreparedAssembly,
  [Parameter(Mandatory=$true)][string]$WorkerGenerationRoot,
  [string]$RuntimeConfig = '',
  [ValidateRange(1, 100)][int]$MaxCommands = 100,
  [ValidateRange(10, 3600)][int]$IdleSeconds = 300
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. (Join-Path $here 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $ProjectRoot -ConfigPath $RuntimeConfig
Set-CreoRuntimeEnvironment -Runtime $runtime
$null = Start-CreoNameService -Runtime $runtime
$classpath = (Join-Path $here 'build') + ';' + `
  (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' + `
  (Join-Path $runtime.CommonFiles 'text\java\otk.jar')

Push-Location $PreparedModelsRoot
try {
  & $runtime.JavaCommand '--enable-native-access=ALL-UNNAMED' `
    '-Dfile.encoding=UTF-8' ('-Djava.library.path=' + $runtime.NativeLibrary) `
    '-cp' $classpath 'NativeArrowWorker' $runtime.CreoCommand `
    $PreparedAssembly $WorkerGenerationRoot $MaxCommands $IdleSeconds
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
