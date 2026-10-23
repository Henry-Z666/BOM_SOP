param([string]$RuntimeConfig = '')

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig
$output = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$classpath = (Join-Path $runtime.CommonFiles 'text\java\pfcasync.jar') + ';' + (Join-Path $runtime.CommonFiles 'text\java\otk.jar')
$sources = @(
  'src\AutoCadDiscovery.java',
  'src\NativeCadIdentityProbe.java',
  'src\NativeModelBindings.java',
  'src\ArrowProjection.java',
  'src\TorqueLabelLayout.java',
  'src\UnicodeVectorText.java',
  'src\NativeTorqueAnnotations.java',
  'src\NativeReceiverAppearance.java',
  'src\RenderAssemblyImage.java',
  'src\NativeArrowBatch.java',
  'src\NativeArrowWorker.java'
) | ForEach-Object { Join-Path $PSScriptRoot $_ }
& $runtime.JavacCommand --release 17 -cp $classpath -d $output @sources
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
