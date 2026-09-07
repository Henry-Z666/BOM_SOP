param([string]$RuntimeConfig = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $projectRoot 'creo_java/RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig
Set-CreoRuntimeEnvironment -Runtime $runtime
$output = Join-Path $projectRoot 'work/java-auxiliary-tests'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$classpath = (Join-Path $runtime.CommonFiles 'text/java/pfcasync.jar') + ';' + (Join-Path $runtime.CommonFiles 'text/java/otk.jar')
$sources = @('creo_java/src/ArrowProjection.java', 'creo_java/src/RenderAssemblyImage.java', 'tests/java/AuxiliaryStageCleanupTest.java') | ForEach-Object { Join-Path $projectRoot $_ }
& $runtime.JavacCommand --release 17 -encoding UTF-8 -cp $classpath -d $output @sources
if ($LASTEXITCODE -ne 0) { throw 'Auxiliary cleanup regression test compilation failed' }
& $runtime.JavaCommand --enable-native-access=ALL-UNNAMED ('-Djava.library.path=' + $runtime.NativeLibrary) -cp ($output + ';' + $classpath) AuxiliaryStageCleanupTest
if ($LASTEXITCODE -ne 0) { throw 'Auxiliary cleanup regression test failed' }
