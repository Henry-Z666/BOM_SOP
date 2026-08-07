param([Parameter(Mandatory=$true)][string]$ModelsRoot, [Parameter(Mandatory=$true)][string]$AssemblyFile, [Parameter(Mandatory=$true)][string]$OutputJpeg, [string]$ExplodeComponentIds = '', [string]$ExplodeOccurrencePaths = '', [double[]]$Translation = @(0,0,0), [string]$VisibleComponentIds = '', [string]$VisibleOccurrencePaths = '', [string]$ExpectedAssemblySha256 = '', [string]$CameraRotate = '', [string]$SecondOutputJpeg = '', [string]$SecondCameraRotate = '', [ValidateSet('portrait','square')][string]$Frame = 'portrait', [string]$CameraSpec = '', [switch]$DrawInstallArrows, [string]$ArrowAuditJson = '', [string]$PreparedModelsRoot = '')
$here = $PSScriptRoot; $projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot
$ptc = $runtime.CreoLoadpoint; $common = $runtime.CommonFiles; $nativeLib = $runtime.NativeLibrary
$renderClass = Join-Path $here 'build\RenderAssemblyImage.class'; $renderSource = Join-Path $here 'src\RenderAssemblyImage.java'; $arrowSource = Join-Path $here 'src\ArrowProjection.java'
if (-not (Test-Path $renderClass) -or (Get-Item $renderSource).LastWriteTimeUtc -gt (Get-Item $renderClass).LastWriteTimeUtc -or (Get-Item $arrowSource).LastWriteTimeUtc -gt (Get-Item $renderClass).LastWriteTimeUtc) { & (Join-Path $here 'build.ps1'); if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$sourceModels = (Resolve-Path -LiteralPath $ModelsRoot).Path; $requested = Resolve-Path -LiteralPath $AssemblyFile
if (-not $requested.Path.StartsWith($sourceModels, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'AssemblyFile 必须位于指定的 ModelsRoot 目录。' }
if ($ExplodeComponentIds -and $ExplodeOccurrencePaths) { throw '不能同时使用旧版特征号和 occurrence 路径。' }
if ($VisibleComponentIds -and $VisibleOccurrencePaths) { throw '不能同时使用旧版特征号和 occurrence 路径。' }
if (-not $ExplodeOccurrencePaths) { $ExplodeOccurrencePaths = $ExplodeComponentIds }
if (-not $VisibleOccurrencePaths) { $VisibleOccurrencePaths = $VisibleComponentIds }
if ($DrawInstallArrows -and (-not $ExplodeOccurrencePaths -or -not $CameraSpec)) { throw '正式箭头要求 occurrence 路径和绝对 CameraSpec。' }
if ($ExpectedAssemblySha256) {
  $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $requested.Path).Hash.ToLowerInvariant()
  if ($actualHash -ne $ExpectedAssemblySha256.ToLowerInvariant()) { throw ("权威总装哈希不一致: expected=" + $ExpectedAssemblySha256 + " actual=" + $actualHash) }
}
$effectiveCamera = $CameraSpec
if ($CameraRotate) {
  if ($CameraSpec) { throw 'CameraSpec 与旧版 CameraRotate 不能同时提供。' }
  Write-Warning 'legacy_relative_camera: CameraRotate 仅用于旧任务；新任务必须使用 ABS/UP CameraSpec。'
  $effectiveCamera = $CameraRotate
}
$licenseFile = $runtime.LicenseFile
& (Join-Path $here 'test_license_binding.ps1') -LicenseFile $licenseFile -CreoLoadpoint $runtime.CreoLoadpoint
$runRoot = Join-Path $projectRoot ('data\runs\render-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
if ($PreparedModelsRoot) {
  $stagedModels = [System.IO.Path]::GetFullPath($PreparedModelsRoot)
  $allowedPreparedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'data\runs'))
  if (-not $stagedModels.StartsWith($allowedPreparedRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'PreparedModelsRoot 必须位于项目 data\runs 的批次隔离目录内。'
  }
  if (-not (Test-Path -LiteralPath $stagedModels -PathType Container)) { throw 'PreparedModelsRoot 不存在。' }
  $runRoot = Split-Path -Parent $stagedModels
}
else {
  $stagedModels = Join-Path $runRoot 'models'
  New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
  Copy-Item -LiteralPath $sourceModels -Destination $stagedModels -Recurse
  Copy-Item -LiteralPath (Join-Path $here 'isolated_config.pro') -Destination (Join-Path $stagedModels 'config.pro') -Force
}
$relative = $requested.Path.Substring($sourceModels.Length).TrimStart('\'); $stagedAssembly = Join-Path $stagedModels $relative
$outputFull = [System.IO.Path]::GetFullPath($OutputJpeg); New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputFull) | Out-Null
$arrowAuditFull = ''
if ($DrawInstallArrows) {
  if (-not $ArrowAuditJson) { $ArrowAuditJson = [System.IO.Path]::ChangeExtension($outputFull, '.arrow.json') }
  $arrowAuditFull = [System.IO.Path]::GetFullPath($ArrowAuditJson); New-Item -ItemType Directory -Force -Path (Split-Path -Parent $arrowAuditFull) | Out-Null
}
$secondOutputFull = ''
if ($SecondOutputJpeg) {
  if (-not $SecondCameraRotate) { throw 'SecondCameraRotate 不能为空。' }
  if ($ExplodeOccurrencePaths -or $VisibleOccurrencePaths -or $effectiveCamera) { throw '双视图预览不能与爆炸/可见集参数混用。' }
  $secondOutputFull = [System.IO.Path]::GetFullPath($SecondOutputJpeg); New-Item -ItemType Directory -Force -Path (Split-Path -Parent $secondOutputFull) | Out-Null
}
Set-CreoRuntimeEnvironment -Runtime $runtime
$classpath = (Join-Path $here 'build') + ';' + (Join-Path $common 'text\java\pfcasync.jar') + ';' + (Join-Path $common 'text\java\otk.jar')
$java = $runtime.JavaCommand; $creoCommand = $runtime.CreoCommand
Push-Location $stagedModels
try {
  $renderArgs = @('--enable-native-access=ALL-UNNAMED', ('-Djava.library.path=' + $nativeLib), '-cp', $classpath, 'RenderAssemblyImage', $creoCommand, $stagedAssembly, $outputFull)
  if ($ExplodeOccurrencePaths) {
    if ($Translation.Count -ne 3) { throw 'Translation 必须包含 dx, dy, dz 三个数值。' }
    $renderArgs += @($ExplodeOccurrencePaths, $Translation[0], $Translation[1], $Translation[2])
  }
  if ($VisibleOccurrencePaths) { $renderArgs += @($VisibleOccurrencePaths) }
  if ($effectiveCamera) { $renderArgs += @($effectiveCamera) }
  if ($DrawInstallArrows) { $renderArgs += @($arrowAuditFull) }
  if ($secondOutputFull) { $renderArgs += @($secondOutputFull, $SecondCameraRotate) }
  $javaOutput = @(& $java $renderArgs 2>&1)
  $renderExitCode = $LASTEXITCODE
  $javaOutput | Set-Content -LiteralPath (Join-Path $runRoot 'jlink-render.log') -Encoding UTF8
  $javaOutput | Write-Output
}
finally { Pop-Location }
# Preserve a fixed output frame without any dynamic crop or resampling.  The
# native Creo view is refit first; this one-time crop only removes a fixed outer
# margin and uses JPEG quality 100.
foreach ($imageOutput in @($outputFull, $secondOutputFull)) {
  if ($imageOutput -and (Test-Path -LiteralPath $imageOutput)) {
    & (Join-Path $projectRoot 'scripts\fit_creo_image.ps1') -Path $imageOutput -Frame $Frame
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
}
exit $renderExitCode
