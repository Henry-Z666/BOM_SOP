param(
  [Parameter(Mandatory=$true)][string]$AssemblyFile,
  [Parameter(Mandatory=$true)][string]$OutputJson
)
$ptc = 'C:\Program Files\PTC\Creo 13.4.0.0'; $common = Join-Path $ptc 'Common Files'; $nativeLib = Join-Path $common 'x86e_win64\lib'
$here = $PSScriptRoot; $projectRoot = Split-Path -Parent $here; $sourceModels = Join-Path $projectRoot '零件图'
$requested = Resolve-Path -LiteralPath $AssemblyFile
if (-not $requested.Path.StartsWith($sourceModels, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'AssemblyFile 必须位于项目的零件图目录。' }
$basisClass = Join-Path $here 'build\CameraBasisDiscovery.class'; $basisSource = Join-Path $here 'src\CameraBasisDiscovery.java'
if (-not (Test-Path $basisClass) -or (Get-Item $basisSource).LastWriteTimeUtc -gt (Get-Item $basisClass).LastWriteTimeUtc) { & (Join-Path $here 'build.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$runRoot = Join-Path $projectRoot ('data\runs\camera-calibration-' + (Get-Date -Format 'yyyyMMdd-HHmmss')); $stagedModels = Join-Path $runRoot 'models'
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null; Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
Copy-Item -LiteralPath (Join-Path $here 'isolated_config.pro') -Destination (Join-Path $stagedModels 'config.pro') -Force
$relative = $requested.Path.Substring($sourceModels.Length).TrimStart('\'); $stagedAssembly = Join-Path $stagedModels $relative
$outputFull = [System.IO.Path]::GetFullPath($OutputJson); New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull) | Out-Null
$env:PATH = $nativeLib + ';' + (Join-Path $ptc 'Parametric\bin') + ';' + $env:PATH; $env:PRO_DIRECTORY = Join-Path $ptc 'Parametric'; $env:CREO_COMMON_FILES = $common
$env:PRO_COMM_MSG_EXE = Join-Path $common 'x86e_win64\obj\pro_comm_msg.exe'; $env:PTC_D_LICENSE_FILE = 'C:\ProgramData\PTC\Licensing\BK130602EDUNIVERSITYED_license.dat'
$env:CREO_APP = 'PMA'; $env:CREOPMA_FEATURE_NAME = 'CREOPMA_StudentP6 ()'
$classpath = (Join-Path $here 'build') + ';' + (Join-Path $common 'text\java\pfcasync.jar') + ';' + (Join-Path $common 'text\java\otk.jar')
$java = (Get-Command java -ErrorAction Stop).Source; $creoCommand = '"' + (Join-Path $ptc 'Parametric\bin\parametric.bat') + '"'
Push-Location $stagedModels
try {
  & $java '--enable-native-access=ALL-UNNAMED' ('-Djava.library.path=' + $nativeLib) '-cp' $classpath 'CameraBasisDiscovery' $creoCommand $stagedAssembly $outputFull
  $exitCode = $LASTEXITCODE
}
finally { Pop-Location }
exit $exitCode
