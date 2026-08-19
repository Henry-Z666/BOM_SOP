param(
  [Parameter(Mandatory=$true)][string]$ModelsRoot,
  [Parameter(Mandatory=$true)][string]$RenderPlanJson,
  [Parameter(Mandatory=$true)][string]$OutputFolder,
  [int]$StartIndex = 0,
  [int]$Count = 1,
  [ValidateRange(0, 3)][int]$VariantIndex = 0,
  [string]$PreparedModelsRoot = '',
  [string]$RunWorkspaceRoot = '',
  [string]$WorkerRoot = '',
  [string]$RuntimeConfig = '',
  # This is an internal render-command failsafe.  The Python scheduler owns
  # the product contract and restarts the Worker after 20 formal tasks; one
  # formal task may legitimately consume many probe/correction commands.
  [ValidateRange(1, 100)][int]$MaxWorkerCommands = 100,
  [ValidateRange(10, 3600)][int]$WorkerIdleSeconds = 300,
  [ValidateRange(10, 3600)][int]$TimeoutSeconds = 600,
  [ValidateRange(1, 30)][int]$CompletionGraceSeconds = 4
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$projectRoot = Split-Path -Parent $here
. (Join-Path $here 'RuntimeConfig.ps1')
$runtime = Get-CreoRuntime -ProjectRoot $projectRoot -ConfigPath $RuntimeConfig

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
$runRoot = if ($RunWorkspaceRoot) {
  [IO.Path]::GetFullPath($RunWorkspaceRoot)
}
else {
  Split-Path -Parent $output
}
if (-not $output.StartsWith($runRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'OutputFolder must stay inside the current Agent run workspace.'
}
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
  if ([string]$payload.execution_mode -notin @('formal','diagnostic_preview')) { throw "Task $($task.step_id) is not eligible for Creo rendering." }
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
  $presentation = $payload.presentation
  if ([string]$presentation.schema_version -ne 'fixed-frame-presentation/v1') { throw "Task $taskId has no supported presentation contract." }
  if ([string]$presentation.focus_context -ne 'stage_visible_bbox/v1') { throw "Task $taskId has an invalid presentation focus context." }
  if ([string]$presentation.framing_priority -ne 'installation_activity/v1') { throw "Task $taskId does not prioritize the installation activity." }
  if ([string]$presentation.zoom_anchor -ne 'installation_activity_center/v1') { throw "Task $taskId has an invalid zoom anchor." }
  if ([string]$presentation.centering.schema_version -ne 'adaptive-screen-center/v1') { throw "Task $taskId has no adaptive centering contract." }
  if ([string]$presentation.centering.initial_estimate -ne 'cad_activity_origin/v1') { throw "Task $taskId has an invalid centering initial estimate." }
  if ([string]$presentation.centering.focus_center -ne 'midpoint_subject_arrow/v1') { throw "Task $taskId has an invalid centering focus definition." }
  if ([string]$presentation.centering.probe_policy -ne 'on_gate_failure/v1') { throw "Task $taskId has an invalid centering probe policy." }
  if ([string]$presentation.centering.response_cache_scope -ne 'camera_frame_environment/v2') { throw "Task $taskId has an invalid PAN response cache scope." }
  if ([int]$presentation.centering.max_probe_rounds -ne 2) { throw "Task $taskId has an invalid PAN probe round limit." }
  if ([string]$presentation.zoom_recovery.schema_version -ne 'centered-span-zoom/v1') { throw "Task $taskId has no supported Zoom recovery contract." }
  if ([double]$presentation.zoom_recovery.target_subject_span -ne 0.55) { throw "Task $taskId has an invalid Zoom target span." }
  if ([double]$presentation.zoom_recovery.min_zoom -ne 0.4 -or [double]$presentation.zoom_recovery.max_zoom -ne 3.2) { throw "Task $taskId has invalid Zoom recovery bounds." }
  if ([int]$presentation.zoom_recovery.max_rounds -ne 2) { throw "Task $taskId has an invalid Zoom recovery round limit." }
  $variants = @($presentation.variants)
  if ($VariantIndex -ge $variants.Count) { throw "Task $taskId has no presentation variant $VariantIndex." }
  $variant = $variants[$VariantIndex]
  if ([string]::IsNullOrWhiteSpace([string]$variant.variant_id)) { throw "Task $taskId has an unnamed presentation variant." }
  $cameraId = [string]$variant.camera_id
  if ($cameraId -notin @('fixed_123', 'fixed_456')) { throw "Task $taskId has an invalid fixed camera." }
  $cameraProperty = $payload.camera_catalog.PSObject.Properties[$cameraId]
  if ($null -eq $cameraProperty) { throw "Task $taskId has no camera catalog entry for $cameraId." }
  $camera = $cameraProperty.Value
  if (@($camera.position_direction_root).Count -ne 3 -or @($camera.up_reference_root).Count -ne 3) {
    throw "Task $taskId has an invalid camera basis."
  }
  $zoom = [double]$variant.zoom
  if ([double]::IsNaN($zoom) -or [double]::IsInfinity($zoom) -or $zoom -lt 0.8 -or $zoom -gt 3.2) {
    throw "Task $taskId has a zoom outside the compiled repair bounds."
  }
  $cameraSpec = 'ABS:' + (& $formatVector $camera.position_direction_root)
  $cameraSpec += ',UP:' + (& $formatVector $camera.up_reference_root)
  $cameraSpec += ',ZOOM:' + $zoom.ToString('G17', $culture) + ',CENTER,LOOKAT_ACTIVITY'
  $pan = @($variant.pan)
  if ($pan.Count -ne 2) { throw "Task $taskId has an invalid pan offset." }
  $panX = [double]$pan[0]
  $panY = [double]$pan[1]
  $maxPan = [double]$presentation.centering.max_abs_pan
  if ($maxPan -ne 1.0) { throw "Task $taskId has an unsupported PAN bound." }
  # max_abs_pan is the Zoom=1 envelope.  Creo's corner-anchored native Zoom
  # requires proportionally more normalized PAN to preserve the same screen
  # centre; this must match AgentNativeCreoWorker._effective_pan_bound.
  $effectiveMaxPan = $maxPan * [Math]::Max(1.0, $zoom)
  if ([double]::IsNaN($panX) -or [double]::IsInfinity($panX) -or [Math]::Abs($panX) -gt $effectiveMaxPan -or
      [double]::IsNaN($panY) -or [double]::IsInfinity($panY) -or [Math]::Abs($panY) -gt $effectiveMaxPan) {
    throw "Task $taskId has a pan offset outside the compiled repair bounds."
  }
  $cameraSpec += ',PAN:' + $panX.ToString('G17', $culture) + ':' + $panY.ToString('G17', $culture)
  $focusOccurrences = @($moving + $receivers | Sort-Object -Unique)
  $anchorRows = New-Object Collections.Generic.List[string]
  foreach ($anchor in @($payload.arrow_anchors)) {
    $anchorOccurrence = [string]$anchor.occurrence_id
    $anchorPoint = @($anchor.complete_point_root)
    if ($moving -notcontains $anchorOccurrence -or $anchorPoint.Count -ne 3) {
      throw "Task $taskId has an invalid planned arrow anchor."
    }
    $anchorRows.Add($anchorOccurrence + '=' + (& $formatVector $anchorPoint))
  }
  if ($anchorRows.Count -ne $moving.Count) {
    throw "Task $taskId does not have one planned arrow anchor per moving occurrence."
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
    $audit,
    ($focusOccurrences -join ';'),
    ($anchorRows -join ';')
  ) -join "`t"))
  $renderedFiles.Add($image)
  $auditFiles.Add($audit)
  Write-Output ("[AGENT_RENDER] task {0} presentation_variant {1} camera {2} zoom {3}" -f $taskId,$VariantIndex,$cameraId,$zoom)
}

& (Join-Path $here 'test_license_binding.ps1') -LicenseFile $runtime.LicenseFile -CreoLoadpoint $runtime.CreoLoadpoint
$nativeClass = Join-Path $here 'build\NativeArrowBatch.class'
$nativeSource = Join-Path $here 'src\NativeArrowBatch.java'
$workerClass = Join-Path $here 'build\NativeArrowWorker.class'
$workerSource = Join-Path $here 'src\NativeArrowWorker.java'
if (-not (Test-Path -LiteralPath $nativeClass) -or -not (Test-Path -LiteralPath $workerClass) -or
    (Get-Item $nativeSource).LastWriteTimeUtc -gt (Get-Item $nativeClass).LastWriteTimeUtc -or
    (Get-Item $workerSource).LastWriteTimeUtc -gt (Get-Item $workerClass).LastWriteTimeUtc) {
  & (Join-Path $here 'build.ps1') -RuntimeConfig $runtime.ConfigPath
  if ($LASTEXITCODE -ne 0) { throw 'J-Link build failed.' }
}
$manifest = Join-Path $internalRoot ('native-arrow-' + [guid]::NewGuid().ToString('N') + '.tsv')
[IO.File]::WriteAllLines($manifest, $manifestRows, [Text.UTF8Encoding]::new($false))

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

$ready = $false
if ($WorkerRoot) {
  $worker = [IO.Path]::GetFullPath($WorkerRoot)
  if (-not $worker.StartsWith($runRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'WorkerRoot must stay inside the current Agent run workspace.'
  }
  New-Item -ItemType Directory -Force -Path $worker | Out-Null
  $current = Join-Path $worker 'current-worker.tsv'
  $generation = $null
  $workerPid = 0
  if (Test-Path -LiteralPath $current -PathType Leaf) {
    $currentFields = (Get-Content -Raw -LiteralPath $current).Trim().Split("`t")
    if ($currentFields.Count -eq 4 -and $currentFields[0] -eq 'native-arrow-worker-current/v1' -and
        [int]::TryParse($currentFields[2], [ref]$workerPid)) {
      $candidate = [IO.Path]::GetFullPath($currentFields[1])
      if ($candidate.StartsWith($worker + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
          $currentFields[3] -eq $preparedAssembly) {
        $candidateProcess = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
        $candidateHeartbeat = Join-Path $candidate 'heartbeat.tsv'
        # A synchronous Creo render cannot update the periodic heartbeat while
        # it is inside J-Link.  Command completion refreshes the heartbeat, and
        # reuse is bounded by the configured idle lifetime instead of an
        # invalid five-second device-specific window.
        $heartbeatFresh = (Test-Path -LiteralPath $candidateHeartbeat -PathType Leaf) -and
          ((Get-Item -LiteralPath $candidateHeartbeat).LastWriteTimeUtc -ge
            (Get-Date).ToUniversalTime().AddSeconds(-($WorkerIdleSeconds + $CompletionGraceSeconds)))
        if ($null -ne $candidateProcess -and $heartbeatFresh -and
            (Test-Path -LiteralPath (Join-Path $candidate 'ready.tsv') -PathType Leaf)) {
          $generation = $candidate
        }
        elseif ($null -ne $candidateProcess) {
          Stop-Process -Id $workerPid -Force -ErrorAction SilentlyContinue
        }
      }
    }
  }
  if ($null -eq $generation) {
    $generation = Join-Path $worker ('generation-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $generation | Out-Null
    $log = Join-Path $generation 'native-arrow-worker.log'
    $errorLog = $log + '.err'
    $launcher = Join-Path $here 'invoke_agent_native_worker.ps1'
    $launcherArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
    $launcherArguments += ' -ProjectRoot "' + $projectRoot + '"'
    $launcherArguments += ' -RuntimeConfig "' + $runtime.ConfigPath + '"'
    $launcherArguments += ' -PreparedModelsRoot "' + $prepared + '"'
    $launcherArguments += ' -PreparedAssembly "' + $preparedAssembly + '"'
    $launcherArguments += ' -WorkerGenerationRoot "' + $generation + '"'
    $launcherArguments += ' -MaxCommands ' + $MaxWorkerCommands
    $launcherArguments += ' -IdleSeconds ' + $WorkerIdleSeconds
    $hostExecutable = (Get-Process -Id $PID).Path
    $launcherProcess = Start-Process -FilePath $hostExecutable -ArgumentList $launcherArguments -PassThru `
      -RedirectStandardOutput $log -RedirectStandardError $errorLog -WindowStyle Hidden
    $startDeadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $readyMarker = Join-Path $generation 'ready.tsv'
    while ((Get-Date) -lt $startDeadline -and -not (Test-Path -LiteralPath $readyMarker -PathType Leaf)) {
      $launcherProcess.Refresh()
      if ($launcherProcess.HasExited) { break }
      Start-Sleep -Milliseconds 200
    }
    if (-not (Test-Path -LiteralPath $readyMarker -PathType Leaf)) {
      $details = @()
      if (Test-Path -LiteralPath $log) { $details += Get-Content -Raw -LiteralPath $log }
      if (Test-Path -LiteralPath $errorLog) { $details += Get-Content -Raw -LiteralPath $errorLog }
      throw ("NativeArrowWorker did not become ready.`n" + ($details -join "`n"))
    }
    $readyFields = (Get-Content -Raw -LiteralPath $readyMarker).Trim().Split("`t")
    if ($readyFields.Count -ne 3 -or $readyFields[0] -ne 'native-arrow-worker/v1' -or
        -not [int]::TryParse($readyFields[1], [ref]$workerPid)) {
      throw 'NativeArrowWorker wrote an invalid readiness marker.'
    }
    $temporaryCurrent = $current + '.tmp'
    [IO.File]::WriteAllText(
      $temporaryCurrent,
      ((@('native-arrow-worker-current/v1',$generation,$workerPid,$preparedAssembly) -join "`t") + "`n"),
      [Text.UTF8Encoding]::new($false)
    )
    Move-Item -LiteralPath $temporaryCurrent -Destination $current -Force
  }
  Write-Output ("[AGENT_RENDER] worker_generation {0}" -f $generation)

  $commandId = [guid]::NewGuid().ToString('N')
  $commands = Join-Path $generation 'commands'
  $manifests = Join-Path $generation 'manifests'
  $results = Join-Path $generation 'results'
  New-Item -ItemType Directory -Force -Path $commands,$manifests,$results | Out-Null
  $workerManifest = Join-Path $manifests ($commandId + '.tsv')
  $temporaryManifest = $workerManifest + '.tmp'
  [IO.File]::WriteAllBytes($temporaryManifest, [IO.File]::ReadAllBytes($manifest))
  Move-Item -LiteralPath $temporaryManifest -Destination $workerManifest
  $request = Join-Path $commands ($commandId + '.request')
  $temporaryRequest = $request + '.tmp'
  [IO.File]::WriteAllText(
    $temporaryRequest,
    ("RENDER`t" + [IO.Path]::GetFileName($workerManifest) + "`n"),
    [Text.UTF8Encoding]::new($false)
  )
  Move-Item -LiteralPath $temporaryRequest -Destination $request
  $resultMarker = Join-Path $results ($commandId + '.result')
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $resultMarker -PathType Leaf)) {
    if ($null -eq (Get-Process -Id $workerPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 200
  }
  if (Test-Path -LiteralPath $resultMarker -PathType Leaf) {
    $resultFields = (Get-Content -Raw -LiteralPath $resultMarker).Trim().Split("`t")
    if ($resultFields.Count -ge 2 -and $resultFields[0] -eq 'passed') {
      $ready = Test-NativeArtifactsReady
    }
    elseif ($resultFields.Count -ge 2) {
      throw ("NativeArrowWorker command failed: " + ($resultFields -join ' '))
    }
  }
  if ($ready) {
    Start-Sleep -Seconds $CompletionGraceSeconds
    $ready = Test-NativeArtifactsReady
  }
  if (-not $ready) {
    Stop-Process -Id $workerPid -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Join-Path $generation 'ready.tsv') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
    throw 'NativeArrowWorker did not produce complete JPEG and arrow audit artifacts.'
  }
}
else {
  $log = Join-Path $output 'native-arrow-jlink.log'
  $errorLog = $log + '.err'
  $launcher = Join-Path $here 'invoke_agent_native_jlink.ps1'
  $launcherArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
  $launcherArguments += ' -ProjectRoot "' + $projectRoot + '"'
  $launcherArguments += ' -RuntimeConfig "' + $runtime.ConfigPath + '"'
  $launcherArguments += ' -PreparedModelsRoot "' + $prepared + '"'
  $launcherArguments += ' -PreparedAssembly "' + $preparedAssembly + '"'
  $launcherArguments += ' -Manifest "' + $manifest + '"'
  $hostExecutable = (Get-Process -Id $PID).Path
  $process = Start-Process -FilePath $hostExecutable -ArgumentList $launcherArguments -PassThru `
    -RedirectStandardOutput $log -RedirectStandardError $errorLog -WindowStyle Hidden
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
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
