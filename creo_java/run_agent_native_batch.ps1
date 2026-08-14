param(
  [Parameter(Mandatory=$true)][string]$ModelsRoot,
  [Parameter(Mandatory=$true)][string]$RenderPlanJson,
  [Parameter(Mandatory=$true)][string]$OutputFolder,
  [int]$StartIndex = 0,
  [int]$Count = 1,
  [string]$PreparedModelsRoot = '',
  [ValidateRange(10, 3600)][int]$TimeoutSeconds = 600,
  [ValidateRange(1, 30)][int]$CompletionGraceSeconds = 4
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot

$sourceRoot = (Resolve-Path -LiteralPath $ModelsRoot).Path
$planPath = (Resolve-Path -LiteralPath $RenderPlanJson).Path
$plan = Get-Content -Raw -LiteralPath $planPath | ConvertFrom-Json
if ([string]$plan.schema_version -ne 'render-plan/v2') { throw 'Agent native batch requires render-plan/v2.' }
$tasks = @($plan.tasks)
if ($StartIndex -lt 0 -or $StartIndex -ge $tasks.Count) { throw 'StartIndex is outside the render plan.' }
if ($Count -lt 1) { throw 'Count must be positive.' }
$stop = [Math]::Min($StartIndex + $Count, $tasks.Count)

$output = [IO.Path]::GetFullPath($OutputFolder)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$runRoot = Split-Path -Parent $output
$internalRoot = Join-Path $runRoot 'internal'
New-Item -ItemType Directory -Force -Path $internalRoot | Out-Null

if ($PreparedModelsRoot) {
  $prepared = [IO.Path]::GetFullPath($PreparedModelsRoot)
  if (-not $prepared.StartsWith($runRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'PreparedModelsRoot must stay inside the current Agent run workspace.'
  }
  if (-not (Test-Path -LiteralPath (Join-Path $prepared '.agent-copy-complete.json') -PathType Leaf)) {
    throw 'PreparedModelsRoot is incomplete or not owned by the Agent.'
  }
}
else {
  $prepared = Join-Path $internalRoot 'prepared-models'
  $marker = Join-Path $prepared '.agent-copy-complete.json'
  if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
    if (Test-Path -LiteralPath $prepared) { throw 'Prepared model copy exists without an atomic completion marker.' }
    $temporary = Join-Path $internalRoot ('.prepared-models-' + [guid]::NewGuid().ToString('N'))
    try {
      Copy-Item -LiteralPath $sourceRoot -Destination $temporary -Recurse
      Copy-Item -LiteralPath (Join-Path $here 'isolated_config.pro') -Destination (Join-Path $temporary 'config.pro') -Force
      [IO.File]::WriteAllText(
        (Join-Path $temporary '.agent-copy-complete.json'),
        '{"schema_version":"prepared-model-copy/v1","complete":true}',
        [Text.UTF8Encoding]::new($false)
      )
      Move-Item -LiteralPath $temporary -Destination $prepared
    }
    finally {
      if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Recurse -Force }
    }
  }
}
Write-Output ("[AGENT_RENDER] prepared_models {0}" -f $prepared)

$firstPayload = $tasks[$StartIndex].payload
$assemblyName = [string]$firstPayload.authoritative_assembly.assembly_file
if (-not $assemblyName -or [IO.Path]::IsPathRooted($assemblyName) -or $assemblyName.Contains('..')) {
  throw 'Authoritative assembly must be a safe relative filename.'
}
$sourceAssembly = [IO.Path]::GetFullPath((Join-Path $sourceRoot $assemblyName))
if (-not $sourceAssembly.StartsWith($sourceRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Authoritative assembly escapes ModelsRoot.'
}
if (-not (Test-Path -LiteralPath $sourceAssembly -PathType Leaf)) { throw 'Authoritative assembly is missing.' }
$expectedHash = ([string]$firstPayload.authoritative_assembly.sha256) -replace '^sha256:', ''
$actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceAssembly).Hash.ToLowerInvariant()
if (-not $expectedHash -or $actualHash -ne $expectedHash.ToLowerInvariant()) {
  throw 'Authoritative assembly SHA-256 does not match the locked plan.'
}
$preparedAssembly = Join-Path $prepared $assemblyName
if (-not (Test-Path -LiteralPath $preparedAssembly -PathType Leaf)) { throw 'Prepared authoritative assembly is missing.' }

$culture = [Globalization.CultureInfo]::InvariantCulture
$formatVector = { param($values) ((@($values) | ForEach-Object { ([double]$_).ToString('G17', $culture) }) -join ':') }
$manifestRows = New-Object Collections.Generic.List[string]
$renderedFiles = New-Object Collections.Generic.List[string]
$auditFiles = New-Object Collections.Generic.List[string]
for ($index = $StartIndex; $index -lt $stop; $index++) {
  $task = $tasks[$index]
  $payload = $task.payload
  if ([string]$payload.schema_version -ne 'creo-render-task/v1') { throw "Invalid task schema at index $index." }
  if ([string]$payload.execution_mode -ne 'formal') { throw "Task $($task.step_id) is not eligible for formal Creo rendering." }
  if ([string]$payload.arrow_renderer -ne 'creo_display_list/v1') { throw "Task $($task.step_id) does not use Creo-native arrows." }
  if ([string]$payload.authoritative_assembly.assembly_file -ne $assemblyName) { throw 'A batch cannot mix authoritative assemblies.' }
  if (([string]$payload.authoritative_assembly.sha256) -ne ([string]$firstPayload.authoritative_assembly.sha256)) { throw 'A batch cannot mix assembly hashes.' }
  $taskId = [string]$task.task_id
  if ($taskId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe task ID: $taskId" }
  $moving = @($payload.moving_occurrences)
  $visible = @($payload.visible_occurrences)
  $receivers = @($payload.receiver_occurrences)
  if ($moving.Count -lt 1 -or $visible.Count -lt 1 -or $receivers.Count -lt 1) { throw "Task $taskId has incomplete occurrence sets." }
  foreach ($required in @($moving + $receivers)) {
    if ($visible -notcontains $required) { throw "Task $taskId omits a moving or receiver occurrence from visibility." }
  }
  $translation = @($payload.translation_vector_root)
  if ($translation.Count -ne 3) { throw "Task $taskId has no pure translation vector." }
  $camera = $payload.camera
  if ([string]$camera.id -notin @('fixed_123', 'fixed_456')) { throw "Task $taskId has an invalid fixed camera." }
  $cameraSpec = 'ABS:' + (& $formatVector $camera.position_direction_root)
  $cameraSpec += ',UP:' + (& $formatVector $camera.up_reference_root)
  $cameraSpec += ',ZOOM:' + ([double]$camera.zoom).ToString('G17', $culture) + ',CENTER'
  $pan = @($camera.pan)
  if ($pan.Count -eq 2) {
    $cameraSpec += ',PAN:' + ([double]$pan[0]).ToString('G17', $culture) + ':' + ([double]$pan[1]).ToString('G17', $culture)
  }
  $image = Join-Path $output ($taskId + '.jpg')
  $audit = Join-Path $output ($taskId + '.arrow.json')
  Remove-Item -LiteralPath $image,$audit -Force -ErrorAction SilentlyContinue
  $manifestRows.Add((@(
    $image,
    ($moving -join ';'),
    ([double]$translation[0]).ToString('G17', $culture),
    ([double]$translation[1]).ToString('G17', $culture),
    ([double]$translation[2]).ToString('G17', $culture),
    ($visible -join ';'),
    $cameraSpec,
    $audit
  ) -join "`t"))
  $renderedFiles.Add($image)
  $auditFiles.Add($audit)
}

& (Join-Path $here 'test_license_binding.ps1') -LicenseFile $runtime.LicenseFile -CreoLoadpoint $runtime.CreoLoadpoint
$nativeClass = Join-Path $here 'build\NativeArrowBatch.class'
$nativeSource = Join-Path $here 'src\NativeArrowBatch.java'
if (-not (Test-Path -LiteralPath $nativeClass) -or (Get-Item $nativeSource).LastWriteTimeUtc -gt (Get-Item $nativeClass).LastWriteTimeUtc) {
  & (Join-Path $here 'build.ps1')
  if ($LASTEXITCODE -ne 0) { throw 'J-Link build failed.' }
}
$manifest = Join-Path $internalRoot ('native-arrow-' + [guid]::NewGuid().ToString('N') + '.tsv')
[IO.File]::WriteAllLines($manifest, $manifestRows, [Text.UTF8Encoding]::new($false))
$log = Join-Path $output 'native-arrow-jlink.log'
$errorLog = $log + '.err'
$launcher = Join-Path $here 'invoke_agent_native_jlink.ps1'
$launcherArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
$launcherArguments += ' -ProjectRoot "' + $projectRoot + '"'
$launcherArguments += ' -PreparedModelsRoot "' + $prepared + '"'
$launcherArguments += ' -PreparedAssembly "' + $preparedAssembly + '"'
$launcherArguments += ' -Manifest "' + $manifest + '"'
$hostExecutable = (Get-Process -Id $PID).Path
$process = Start-Process -FilePath $hostExecutable -ArgumentList $launcherArguments -PassThru `
  -RedirectStandardOutput $log -RedirectStandardError $errorLog -WindowStyle Hidden

function Test-NativeArtifactsReady {
  for ($artifactIndex = 0; $artifactIndex -lt $renderedFiles.Count; $artifactIndex++) {
    $imagePath = $renderedFiles[$artifactIndex]
    $auditPath = $auditFiles[$artifactIndex]
    if (-not (Test-Path -LiteralPath $imagePath -PathType Leaf)) { return $false }
    if (-not (Test-Path -LiteralPath $auditPath -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $imagePath).Length -lt 10000) { return $false }
    if ((Get-Item -LiteralPath $auditPath).Length -lt 100) { return $false }
  }
  return $true
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$ready = $false
while ((Get-Date) -lt $deadline) {
  if (Test-NativeArtifactsReady) {
    Start-Sleep -Seconds $CompletionGraceSeconds
    if (Test-NativeArtifactsReady) { $ready = $true; break }
  }
  $process.Refresh()
  if ($process.HasExited) { break }
  Start-Sleep -Milliseconds 250
}
$process.Refresh()
if (-not $process.HasExited) {
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  $process.WaitForExit()
}
if (-not $ready) {
  $details = @()
  if (Test-Path -LiteralPath $log) { $details += Get-Content -Raw -LiteralPath $log }
  if (Test-Path -LiteralPath $errorLog) { $details += Get-Content -Raw -LiteralPath $errorLog }
  throw ("NativeArrowBatch did not produce complete JPEG and arrow audit artifacts.`n" + ($details -join "`n"))
}

foreach ($image in $renderedFiles) {
  if (-not (Test-Path -LiteralPath $image -PathType Leaf)) { throw "Creo did not produce $image" }
  & (Join-Path $projectRoot 'scripts\fit_creo_image.ps1') -Path $image -Frame square
  Add-Type -AssemblyName System.Drawing
  $bitmap = [System.Drawing.Bitmap]::new($image)
  try {
    if ($bitmap.Width -ne 1600 -or $bitmap.Height -ne 1600) {
      throw "Fixed-frame processing failed for $image"
    }
  }
  finally { $bitmap.Dispose() }
}
Write-Output ("[AGENT_RENDER] complete {0} tasks" -f $manifestRows.Count)
