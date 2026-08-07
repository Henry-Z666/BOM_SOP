$ptcRoot = 'C:\Program Files\PTC\Creo 13.4.0.0\Common Files'
$output = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$classpath = (Join-Path $ptcRoot 'text\java\pfcasync.jar') + ';' + (Join-Path $ptcRoot 'text\java\otk.jar')
javac --release 17 -cp $classpath -d $output (Join-Path $PSScriptRoot 'src\AutoCadDiscovery.java') (Join-Path $PSScriptRoot 'src\EmbeddedFirstImage.java') (Join-Path $PSScriptRoot 'src\ArrowProjection.java') (Join-Path $PSScriptRoot 'src\RenderAssemblyImage.java') (Join-Path $PSScriptRoot 'src\PixelArrowBaseBatchV3.java') (Join-Path $PSScriptRoot 'src\CameraBasisDiscovery.java')
