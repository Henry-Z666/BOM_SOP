param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [Parameter(Mandatory=$true)][string]$PreparedModelsRoot,
  [Parameter(Mandatory=$true)][string]$PreparedAssembly,
  [Parameter(Mandatory=$true)][string]$Manifest
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
. (Join-Path $here 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $ProjectRoot
Set-CreoRuntimeEnvironment -Runtime $runtime
$classpath = (Join-Path $here 'build') + ';' + `
  (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' + `
  (Join-Path $runtime.CommonFiles 'text\java\otk.jar')

Push-Location $PreparedModelsRoot
try {
  & $runtime.JavaCommand '--enable-native-access=ALL-UNNAMED' `
    ('-Djava.library.path=' + $runtime.NativeLibrary) `
    '-cp' $classpath 'NativeArrowBatch' $runtime.CreoCommand `
    $PreparedAssembly $Manifest
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
