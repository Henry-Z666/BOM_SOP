param([string]$RuntimeConfig = '')

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig
$output = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$classpath = (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' + (Join-Path $runtime.CommonFiles 'text\java\otk.jar')
& $runtime.JavacCommand --release 17 -cp $classpath -d $output (Join-Path $PSScriptRoot 'src\AutoCadDiscovery.java') (Join-Path $PSScriptRoot 'src\EmbeddedFirstImage.java') (Join-Path $PSScriptRoot 'src\ArrowProjection.java') (Join-Path $PSScriptRoot 'src\RenderAssemblyImage.java') (Join-Path $PSScriptRoot 'src\PixelArrowBaseBatchV3.java') (Join-Path $PSScriptRoot 'src\CameraBasisDiscovery.java')
