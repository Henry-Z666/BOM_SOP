param([Parameter(Mandatory=$true)][string]$AssemblyFile, [Parameter(Mandatory=$true)][string]$OutputJson)
$ptc = 'C:\Program Files\PTC\Creo 13.4.0.0'
$common = Join-Path $ptc 'Common Files'
$nativeLib = Join-Path $common 'x86e_win64\lib'
$java = (Get-Command java -ErrorAction Stop).Source
$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
if (-not (Test-Path (Join-Path $here 'build\AutoCadDiscovery.class'))) { & (Join-Path $here 'build.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$sourceModels = Join-Path $projectRoot '零件图'
$requested = Resolve-Path -LiteralPath $AssemblyFile
if (-not $requested.Path.StartsWith($sourceModels, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'AssemblyFile 必须位于项目的零件图目录。' }
$runRoot = Join-Path $projectRoot ('data\runs\discovery-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$stagedModels = Join-Path $runRoot 'models'
New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
$relative = $requested.Path.Substring($sourceModels.Length).TrimStart('\')
$stagedAssembly = Join-Path $stagedModels $relative
$outputFull = [System.IO.Path]::GetFullPath($OutputJson)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull) | Out-Null
$env:PATH = $nativeLib + ';' + (Join-Path $ptc 'Parametric\bin') + ';' + $env:PATH
$env:PRO_DIRECTORY = Join-Path $ptc 'Parametric'
$env:CREO_COMMON_FILES = $common
$env:PRO_COMM_MSG_EXE = Join-Path $common 'x86e_win64\obj\pro_comm_msg.exe'
$env:PTC_D_LICENSE_FILE = 'C:\ProgramData\PTC\Licensing\BK130602EDUNIVERSITYED_license.dat'
$env:CREO_APP = 'PMA'
$env:CREOPMA_FEATURE_NAME = 'CREOPMA_StudentP6 ()'
$classpath = (Join-Path $here 'build') + ';' + (Join-Path $common 'text\java\pfcasync.jar') + ';' + (Join-Path $common 'text\java\otk.jar')
# J-Link's official contract expects the full startup command/batch file, not a
# direct executable plus PSF arguments.  Models are retrieved through the API.
$creoCommand = '"' + (Join-Path $ptc 'Parametric\bin\parametric.bat') + '"'
Push-Location $stagedModels
try { & $java '--enable-native-access=ALL-UNNAMED' ('-Djava.library.path=' + $nativeLib) -cp $classpath AutoCadDiscovery $creoCommand $stagedAssembly $outputFull }
finally { Pop-Location }
exit $LASTEXITCODE
