param(
  [Parameter(Mandatory=$true)][string]$ModelsRoot,
  [Parameter(Mandatory=$true)][string]$RenderPlanJson,
  [Parameter(Mandatory=$true)][string]$OutputFolder,
  [int]$StartIndex = 0,
  [string]$ExpectedTaskId = '',
  [int]$Count = 1,
  [ValidateRange(0, 3)][int]$VariantIndex = 0,
  [ValidateSet('Render','Visibility')][string]$Operation = 'Render',
  [string]$PreparedModelsRoot = '',
  [string]$RunWorkspaceRoot = '',
  [string]$WorkerRoot = '',
  [string]$RuntimeConfig = '',
  [string]$NativeBindingsManifest = '',
  # This is an internal render-command failsafe. The Python scheduler owns
  # the product contract and sends exactly one render command per formal task.
  [ValidateRange(1, 100)][int]$MaxWorkerCommands = 100,
  [ValidateRange(10, 3600)][int]$WorkerIdleSeconds = 300,
  [ValidateRange(10, 3600)][int]$TimeoutSeconds = 600,
  [ValidateRange(1, 30)][int]$CompletionGraceSeconds = 4
)

$ErrorActionPreference = 'Stop'
if ($NativeBindingsManifest) {
  $NativeBindingsManifest = (Resolve-Path -LiteralPath $NativeBindingsManifest).Path
  if (-not $WorkerRoot) { throw 'Native bindings require a dedicated worker.' }
  $bindingHash = (Get-FileHash -LiteralPath $NativeBindingsManifest -Algorithm SHA256).Hash.Substring(0,12)
  $WorkerRoot = Join-Path $WorkerRoot ('bindings-' + $bindingHash)
}
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
if ($ExpectedTaskId -and ($Count -ne 1 -or [string]$tasks[$StartIndex].task_id -cne $ExpectedTaskId)) {
  throw 'RENDER_TASK_IDENTITY_MISMATCH: selected plan index does not match requested task.'
}
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
      # Geometry/auxiliary visibility is controlled and audited by J-Link.
      # Do not inject legacy UI startup options; they can break Creo 13 RPC initialization.
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
$visibilityFiles = New-Object Collections.Generic.List[string]
$framingAudits = New-Object Collections.Generic.List[object]
for ($index = $StartIndex; $index -lt $stop; $index++) {
  $task = $tasks[$index]
  $payload = $task.payload
  if ([string]$payload.schema_version -ne 'creo-render-task/v1') { throw "Invalid task schema at index $index." }
  if ([string]$payload.execution_mode -notin @('formal','diagnostic_preview','completed_position')) { throw "Task $($task.step_id) is not eligible for Creo rendering." }
  if ([string]$payload.execution_mode -eq 'completed_position' -and ($null -eq $payload.PSObject.Properties['completed_stage_only'] -or -not [bool]$payload.completed_stage_only)) { throw 'Completed position mode requires unchanged native positions.' }
  if ([string]$payload.arrow_renderer -ne 'creo_display_list/v1') { throw "Task $($task.step_id) does not use Creo-native arrows." }
  if ([string]$payload.authoritative_assembly.assembly_file -ne $assemblyName) { throw 'A batch cannot mix authoritative assemblies.' }
  if (([string]$payload.authoritative_assembly.sha256) -ne ([string]$firstPayload.authoritative_assembly.sha256)) { throw 'A batch cannot mix assembly hashes.' }
  $taskId = [string]$task.task_id
  if ($taskId -notmatch '^[A-Za-z0-9._-]+$') { throw "Unsafe task ID: $taskId" }
  $moving = @($payload.moving_occurrences)
  $completedPosition = $null -ne $payload.PSObject.Properties['completed_stage_only'] -and [bool]$payload.completed_stage_only
  $visible = @($payload.visible_occurrences)
  # Initial render jobs predate manual rerender choices and therefore do not
  # carry visibility_enforcement.  Read the optional revision through the
  # PSObject property bag so StrictMode does not turn an absent field into a
  # batch-wide render failure.
  $visibilityProperty = $payload.PSObject.Properties['visibility_enforcement']
  $visibilityEnforcement = if ($null -ne $visibilityProperty) {
    $visibilityProperty.Value
  }
  else {
    $null
  }
  if ($null -ne $visibilityEnforcement) {
    if ([string]$visibilityEnforcement.schema_version -ne 'visibility-enforcement/v1' -or
        [string]$visibilityEnforcement.mode -ne 'rebuild_exact_exclusions/v1') {
      throw "Task $taskId has an invalid manual visibility rebuild contract."
    }
    $exactVisible = @($visibilityEnforcement.exact_visible_occurrences)
    if ($exactVisible.Count -lt 1 -or
        (@($exactVisible | Sort-Object -Unique) -join ';') -ne (@($visible | Sort-Object -Unique) -join ';')) {
      throw "Task $taskId manual visibility rebuild does not match the locked forward-stage set."
    }
    # Feed the explicit revision set into the native exclusion script. This is
    # the executable mapping behind the GUI option, not a display-only note.
    $visible = $exactVisible
  }
  $receivers = @($payload.receiver_occurrences)
  $nativeVisible = @($visible)
  $exclusionProperty = $payload.PSObject.Properties['bom_visibility_exclusions']
  if ($null -ne $exclusionProperty) {
    foreach ($excluded in @($exclusionProperty.Value)) {
      if ([string]$excluded -notmatch '^\d+(/\d+)*$') { throw 'Invalid BOM scope exclusion.' }
      foreach ($required in @($moving)+@($receivers)) {
        if ([string]$required -eq [string]$excluded -or ([string]$required).StartsWith(([string]$excluded)+'/')) { throw 'BOM exclusion hides required installation geometry.' }
      }
      $nativeVisible += '!' + [string]$excluded
    }
  }
  if ($moving.Count -lt 1 -or $visible.Count -lt 1 -or (-not $completedPosition -and $receivers.Count -lt 1)) { throw "Task $taskId has incomplete occurrence sets." }
  foreach ($required in @($moving + $receivers)) {
    if ($visible -notcontains $required) { throw "Task $taskId omits a moving or receiver occurrence from visibility." }
  }
  $translation = @($payload.translation_vector_root)
  if ($translation.Count -ne 3) { throw "Task $taskId has no pure translation vector." }
  $translationRows = New-Object Collections.Generic.List[string]
  $translationProperties = @(
    $payload.occurrence_translation_vectors_root.PSObject.Properties |
      Sort-Object Name
  )
  foreach ($property in $translationProperties) {
    $occurrence = [string]$property.Name
    $vector = @($property.Value)
    if ($moving -notcontains $occurrence -or $vector.Count -ne 3) {
      throw "Task $taskId has an invalid per-occurrence translation for $occurrence."
    }
    $translationRows.Add($occurrence + '=' + (& $formatVector $vector))
  }
  if ($translationRows.Count -ne $moving.Count) {
    throw "Task $taskId does not have one translation per moving occurrence."
  }
  $encodedTranslations = $translationRows -join ';'
  $presentation = $payload.presentation
  if ([string]$presentation.schema_version -notin @('fixed-frame-presentation/v1', 'native-viewport-presentation/v1', 'square-crop-presentation/v1')) { throw "Task $taskId has no supported presentation contract." }
  $nativeSelectedFit = $presentation.native_selected_fit
  if ([string]$nativeSelectedFit.schema_version -ne 'native-selected-fit/v2' -or
      [string]$nativeSelectedFit.command -ne 'ProCmdZoomIntoOutline' -or
      [string]$nativeSelectedFit.selection_scope -ne 'moving_occurrences_and_receiver_surfaces/v1' -or
      [string]$nativeSelectedFit.coordinate_adjustment -ne 'none_native_surface_fit/v1' -or
      [string]$nativeSelectedFit.level_policy -ne 'fixed_native_selection_margin/v1' -or
      [int]$nativeSelectedFit.max_commands_per_render -ne 1 -or
      [bool]$nativeSelectedFit.manual_pan_zoom_forbidden -ne $true) {
    throw "Task $taskId has no supported native selected-fit contract."
  }
  $selectedFitLevel = [double]$nativeSelectedFit.zoom_to_selected_level
  $adaptiveSingleFit = $false
  $singleViewProperty = $payload.PSObject.Properties['installation_view']
  if ($null -ne $singleViewProperty -and $singleViewProperty.Value.PSObject.Properties['composition_policy']) {
    $adaptiveSingleFit = [string]$singleViewProperty.Value.composition_policy -in @('single-image-context-margin/v1','single-image-anchor-fit/v2','single-image-balanced-fit/v3','single-image-balanced-fit/v4','single-image-balanced-fit/v5','single-image-balanced-fit/v6','single-image-balanced-fit/v7','single-image-balanced-fit/v8','single-image-balanced-fit/v9','single-image-balanced-fit/v10','single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') -and
      $selectedFitLevel -ge 0.35 -and $selectedFitLevel -le 1.45 -and
      [Math]::Abs($selectedFitLevel - [double]$singleViewProperty.Value.selected_fit_level) -lt 1e-6
  }
  if ([double]::IsNaN($selectedFitLevel) -or [double]::IsInfinity($selectedFitLevel) -or
      ([Math]::Abs($selectedFitLevel - 0.70) -gt 1.0e-9 -and
       [Math]::Abs($selectedFitLevel - 0.80) -gt 1.0e-9 -and
       [Math]::Abs($selectedFitLevel - 0.85) -gt 1.0e-9 -and
       [Math]::Abs($selectedFitLevel - 0.95) -gt 1.0e-9 -and
       [Math]::Abs($selectedFitLevel - 1.45) -gt 1.0e-9 -and
       [Math]::Abs($selectedFitLevel - 1.50) -gt 1.0e-9 -and -not $adaptiveSingleFit)) {
    throw "Task $taskId must use a bounded native selected-fit margin (0.70, 0.80, 0.85, 0.95, 1.45, or 1.50)."
  }
  if ($Operation -eq 'Visibility') {
    if (-not $WorkerRoot) { throw 'Visibility evidence must run inside NativeArrowWorker.' }
    $contract = $payload.camera_visibility
    if ([string]$contract.schema_version -ne 'camera-visibility-contract/v1' -or [string]$contract.status -ne 'ready' -or
        (@($contract.candidate_camera_ids) -join ';') -ne 'fixed_123;fixed_456') {
      throw "Task $taskId has no ready fixed-camera visibility contract."
    }
    $cameraSpecs = @{}
    foreach ($candidateId in @('fixed_123','fixed_456')) {
      $property = $payload.camera_catalog.PSObject.Properties[$candidateId]
      if ($null -eq $property) { throw "Task $taskId has no camera catalog entry for $candidateId." }
      $candidate = $property.Value
      if (@($candidate.position_direction_root).Count -ne 3 -or @($candidate.up_reference_root).Count -ne 3) {
        throw "Task $taskId has an invalid camera basis for $candidateId."
      }
      $spec = 'ABS:' + (& $formatVector $candidate.position_direction_root)
      $spec += ',UP:' + (& $formatVector $candidate.up_reference_root)
      $spec += ',FIT_SELECTED:' + $selectedFitLevel.ToString('G17', $culture)
      $cameraSpecs[$candidateId] = $spec
    }
    $labelRows = New-Object Collections.Generic.List[string]
    $seenOccurrences = New-Object Collections.Generic.HashSet[string]
    $seenLabels = New-Object Collections.Generic.HashSet[int]
    foreach ($group in @($contract.moving_labels, $contract.receiver_interface_labels)) {
      foreach ($property in @($group.PSObject.Properties | Sort-Object Name)) {
        $occurrence = [string]$property.Name; $label = [int]$property.Value
        if (-not $seenOccurrences.Add($occurrence) -or -not $seenLabels.Add($label) -or $label -le 0 -or $label -gt 16777215) {
          throw "Task $taskId has invalid visibility labels."
        }
        if (@($moving + $receivers) -notcontains $occurrence) { throw "Visibility label targets unknown occurrence $occurrence." }
        $labelRows.Add($occurrence + '=' + $label.ToString($culture))
      }
    }
    if ($seenOccurrences.Count -ne ($moving.Count + $receivers.Count)) { throw "Task $taskId visibility labels are incomplete." }
    $focusOccurrences = @($moving + $receivers | Sort-Object -Unique)
    foreach ($candidateId in @('fixed_123','fixed_456')) {
      foreach ($kind in @('isolated','staged')) {
        $path = Join-Path $output ($taskId + '.' + $candidateId + '.' + $kind + '.png')
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        $visibilityFiles.Add($path)
      }
    }
    $manifestRows.Add((@(
      $output,$taskId,($moving -join ';'),
      ([double]$translation[0]).ToString('G17', $culture),
      ([double]$translation[1]).ToString('G17', $culture),
      ([double]$translation[2]).ToString('G17', $culture),
      ($receivers -join ';'),($nativeVisible -join ';'),$cameraSpecs['fixed_123'],$cameraSpecs['fixed_456'],
      ($focusOccurrences -join ';'),($labelRows -join ';'),$encodedTranslations
    ) -join "`t"))
    Write-Output ("[AGENT_RENDER] task {0} visibility fixed_123 fixed_456" -f $taskId)
    continue
  }
  if ([string]$presentation.focus_context -ne 'installation_activity_bbox/v1') { throw "Task $taskId has an invalid presentation focus context." }
  if ([string]$presentation.framing_priority -ne 'installation_activity/v1') { throw "Task $taskId does not prioritize the installation activity." }
  if ([string]$presentation.zoom_anchor -ne 'installation_activity_center/v1') { throw "Task $taskId has an invalid zoom anchor." }
  $variants = @($presentation.variants)
  if ($VariantIndex -ge $variants.Count) { throw "Task $taskId has no presentation variant $VariantIndex." }
  $variant = $variants[$VariantIndex]
  if ([string]::IsNullOrWhiteSpace([string]$variant.variant_id)) { throw "Task $taskId has an unnamed presentation variant." }
  $cameraId = [string]$variant.camera_id
  $localView = $payload.PSObject.Properties['installation_view']
  $localAllowed = $null -ne $localView -and [string]$localView.Value.schema_version -eq 'installation-interface-view/v1' -and $cameraId -eq 'interface_oblique'
  if ($cameraId -notin @('fixed_123', 'fixed_456') -and -not $localAllowed) { throw "Task $taskId has an invalid camera." }
  $cameraProperty = $payload.camera_catalog.PSObject.Properties[$cameraId]
  if ($null -eq $cameraProperty) { throw "Task $taskId has no camera catalog entry for $cameraId." }
  $camera = $cameraProperty.Value
  if (@($camera.position_direction_root).Count -ne 3 -or @($camera.up_reference_root).Count -ne 3) {
    throw "Task $taskId has an invalid camera basis."
  }
  $zoom = [double]$variant.zoom
  if ([double]::IsNaN($zoom) -or [double]::IsInfinity($zoom) -or $zoom -ne 1.0) {
    throw "Task $taskId must use the native selected-fit Zoom."
  }
  $cameraSpec = 'ABS:' + (& $formatVector $camera.position_direction_root)
  $cameraSpec += ',UP:' + (& $formatVector $camera.up_reference_root)
  $cameraSpec += ',FIT_SELECTED:' + $selectedFitLevel.ToString('G17', $culture)
  if ($null -ne $payload.PSObject.Properties['completed_stage_framing']) {
    $stageFrame=$payload.completed_stage_framing
    if (-not $payload.completed_stage_only -or [string]$stageFrame.schema_version -ne 'native-stage-bounds-fit/v1') { throw 'Stage bounds framing requires an unchanged completed stage.' }
    $stageBoxes=@($stageFrame.bounds)
    if ((@($stageBoxes | ForEach-Object {$_.occurrence_id} | Sort-Object -Unique) -join ';') -ne (@($visible | Sort-Object -Unique) -join ';') -or $stageBoxes.Count -ne $visible.Count) { throw 'Completed framing must cover exactly the visible stage.' }
    $encodedStage=New-Object Collections.Generic.List[string]
    foreach ($box in $stageBoxes) {
      if (@($box.min).Count -ne 3 -or @($box.max).Count -ne 3) { throw 'Invalid stage CAD bounds.' }
      for ($a=0;$a -lt 3;$a++) { if (-not [double]::IsFinite([double]$box.min[$a]) -or -not [double]::IsFinite([double]$box.max[$a]) -or [double]$box.min[$a] -gt [double]$box.max[$a]) { throw 'Invalid stage CAD bound coordinates.' } }
      $encodedStage.Add((& $formatVector $box.min)+':'+(& $formatVector $box.max))
    }
    $cameraSpec += ',STAGE_BOUNDS:'+($encodedStage -join ';')
  }
  if ($null -ne $payload.PSObject.Properties['installation_notes'] -and $null -ne $payload.installation_notes.PSObject.Properties['layout_policy'] -and [string]$payload.installation_notes.layout_policy -eq 'ordered-perimeter-lane/v1') {
    if ([string]$payload.image_coverage.role -ne 'location' -or [string]$payload.installation_notes.anchor_state -ne 'authoritative_complete' -or [string]$localView.Value.composition_policy -ne 'single-image-balanced-fit/v15') { throw 'Spatial perimeter notes require a v15 complete-position locator.' }
    $cameraSpec += ',NOTE_LAYOUT:spatial_perimeter'
  }

  if ($null -ne $localView) {
    $fitScope=[string]$localView.Value.fit_scope
    if ($fitScope -notin @('moving_only','moving_interfaces','anchor_surfaces','balanced_surfaces','protected_surfaces','protected_groups')) { throw "Task $taskId has an invalid interface fit scope." }
    $cameraSpec += ',FIT_SCOPE:'+$fitScope
    if ($null -ne $localView.Value.PSObject.Properties['coordinate_adapter'] -and [string]$localView.Value.coordinate_adapter -in @('cad_roi_screen_transform/v1','cad_location_screen_transform/v1')) {
      if ([string]$localView.Value.composition_policy -notin @('single-image-balanced-fit/v11','single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15') -or
          $null -eq $payload.PSObject.Properties['image_coverage'] -or [string]$payload.image_coverage.role -notin @('detail','location') -or
          -not [double]::IsFinite([double]$localView.Value.roi_feature_span) -or [double]$localView.Value.roi_feature_span -le 0) {
        throw 'CAD ROI requires a v11 detail role and a positive CAD feature span.'
      }
      $expectedRole = if ([string]$localView.Value.coordinate_adapter -eq 'cad_location_screen_transform/v1') { 'location' } else { 'detail' }
      if ([string]$payload.image_coverage.role -ne $expectedRole -or
          ($expectedRole -eq 'location' -and [string]$localView.Value.composition_policy -notin @('single-image-balanced-fit/v12','single-image-balanced-fit/v13','single-image-balanced-fit/v14','single-image-balanced-fit/v15'))) {
        throw 'Native ROI adapter does not match its image role.'
      }
      $cameraSpec += ',ROI_SPAN:'+([double]$localView.Value.roi_feature_span).ToString('G17',$culture)
      if ($null -ne $localView.Value.PSObject.Properties['roi_frame_fraction']) {
        $fraction = [double]$localView.Value.roi_frame_fraction
        if ($expectedRole -ne 'detail' -or -not [double]::IsFinite($fraction) -or $fraction -lt .5 -or $fraction -gt .9) {
          throw 'Detail ROI frame fraction must be bounded between 0.5 and 0.9.'
        }
        $cameraSpec += ',ROI_FRACTION:'+$fraction.ToString('G17',$culture)
      }
      if ([string]$localView.Value.coordinate_adapter -eq 'cad_location_screen_transform/v1') { $cameraSpec += ',ROI_ROLE:location' }
    }
  }
  $ghostProperty=$payload.PSObject.Properties['receiver_ghosting']
  if ($null -ne $ghostProperty) {
    $ghost=$ghostProperty.Value
    if ($ghost.schema_version -ne 'native-receiver-ghost/v1' -or $ghost.style -ne 'native_transparent') { throw 'Unknown native receiver appearance contract.' }
    $ghostPaths=@($ghost.occurrence_ids)
    if ($ghostPaths.Count -eq 0) { throw 'Receiver appearance requires occurrence paths.' }
    foreach($ghostPath in $ghostPaths) {
      if ($ghostPath -notmatch '^\d+(?:/\d+)*$' -or $ghostPath -notin @($payload.receiver_occurrences) -or $ghostPath -in @($payload.moving_occurrences)) { throw 'Only exact native receiving occurrences can be made transparent.' }
    }
    $cameraSpec+=',GHOST_RECEIVERS:'+($ghostPaths -join ';')
  }
  $pan = @($variant.pan)
  if ($pan.Count -ne 2) { throw "Task $taskId has an invalid pan offset." }
  $panX = [double]$pan[0]
  $panY = [double]$pan[1]
  if ([double]::IsNaN($panX) -or [double]::IsInfinity($panX) -or $panX -ne 0.0 -or
      [double]::IsNaN($panY) -or [double]::IsInfinity($panY) -or $panY -ne 0.0) {
    throw "Task $taskId must use the native selected-fit center."
  }
  $focusOccurrences = @($moving | Sort-Object -Unique)
  $anchorRows = New-Object Collections.Generic.List[string]
  foreach ($anchor in @($payload.arrow_anchors)) {
    $anchorOccurrence = [string]$anchor.occurrence_id
    $anchorPoint = @($anchor.complete_point_root)
    if ($moving -notcontains $anchorOccurrence -or $anchorPoint.Count -ne 3) {
      throw "Task $taskId has an invalid planned arrow anchor."
    }
    $anchorRows.Add($anchorOccurrence + '=' + (& $formatVector $anchorPoint))
  }
  if (-not $completedPosition -and $anchorRows.Count -ne $moving.Count) {
    throw "Task $taskId does not have one planned arrow anchor per moving occurrence."
  }
  $receiverSelectionRows = New-Object Collections.Generic.List[string]
  $seenReceiverSelections = New-Object Collections.Generic.HashSet[string]
  foreach ($selection in @($payload.receiver_interface_selections)) {
    $receiverOccurrence = [string]$selection.occurrence_id
    $receiverItemId = [int]$selection.item_id
    if ($receivers -notcontains $receiverOccurrence -or
        [string]$selection.item_type -ne 'SURFACE' -or $receiverItemId -le 0) {
      throw "Task $taskId has an invalid receiver interface surface selection."
    }
    $encodedSelection = $receiverOccurrence + '#' + $receiverItemId.ToString($culture)
    if (-not $seenReceiverSelections.Add($encodedSelection)) {
      throw "Task $taskId has a duplicate receiver interface surface selection."
    }
    $receiverSelectionRows.Add($encodedSelection)
  }
  if (-not $completedPosition -and $receiverSelectionRows.Count -lt 1) {
    throw "Task $taskId has no receiver interface surface selection."
  }
  $image = Join-Path $output ($taskId + '.jpg')
  $audit = Join-Path $output ($taskId + '.arrow.json')
  Remove-Item -LiteralPath $image,$audit -Force -ErrorAction SilentlyContinue
  if ($null -ne $payload.PSObject.Properties['moving_visibility_evidence'] -and [string]$payload.moving_visibility_evidence -eq 'native-occurrence-difference/v1') { $cameraSpec += ',MOVING_EVIDENCE:occurrence_difference' }
  if ($null -ne $payload.PSObject.Properties['arrow_evidence'] -and [string]$payload.arrow_evidence -eq 'native-background-difference/v1') { $cameraSpec += ',ARROW_EVIDENCE:background_difference' }
  if ($null -ne $payload.PSObject.Properties['physical_component_display'] -and [string]$payload.physical_component_display -eq 'bom_explicit_include/v1') { $cameraSpec += ',PHYSICAL_DISPLAY:explicit_include' }
  $torqueRows = New-Object Collections.Generic.List[string]
  $torqueBounds = New-Object Collections.Generic.List[string]
  $torqueHash = ''
  $torqueProperty = $payload.PSObject.Properties['torque_annotations']
  $installationNote = $false
  if ($null -eq $torqueProperty -and $null -ne $payload.PSObject.Properties['installation_notes']) {
    $torqueProperty = $payload.PSObject.Properties['installation_notes']; $installationNote = $true
    $cameraSpec += ',NOTE_KIND:installation'
  }
  if ($null -ne $payload.PSObject.Properties['annotation_bundle']) {
    $torqueProperty=$payload.PSObject.Properties['annotation_bundle']; $installationNote=$true
    $cameraSpec += ',NOTE_KIND:installation'
  }
  if ($null -ne $torqueProperty -and @($torqueProperty.Value.labels).Count -gt 0) {
    $annotations = $torqueProperty.Value
    if ([string]$annotations.schema_version -ne $(if ($installationNote) {'installation-notes/v1'} else {'torque-annotations/v1'}) -or
        [string]$annotations.status -ne 'confirmed' -or [string]$annotations.renderer -ne 'creo_display_list/v1') {
      throw 'Unconfirmed torque annotations cannot reach Creo.'
    }
    $torqueHash = [string]$annotations.contract_hash
    if ($torqueHash -notmatch '^sha256:[0-9a-f]{64}$') { throw 'Torque annotation contract hash missing.' }
    $seenTorque = New-Object Collections.Generic.HashSet[string]
    foreach ($label in @($annotations.labels)) {
      $text = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$label.text))
      $connection = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes([string]$label.connection_id))
      foreach ($occurrence in @($label.occurrence_ids)) {
        if ($moving -notcontains $occurrence -or -not $seenTorque.Add($(if ($null -ne $payload.PSObject.Properties['annotation_bundle']) {[string]$label.connection_id+'|'+[string]$occurrence} else {[string]$occurrence}))) { throw 'Invalid torque occurrence coverage.' }
        $torqueRows.Add(([string]$occurrence) + '#' + $text + '#' + $connection)
      }
    }
    $movingBounds = @($payload.stage_geometry_root.moving_bounds)
    if ($movingBounds.Count -ne $moving.Count) { throw 'Torque labels require all native moving bounds.' }
    for ($boundIndex=0;$boundIndex -lt $moving.Count;$boundIndex++) {
      $box = $movingBounds[$boundIndex]
      $vectorProperty = $payload.occurrence_translation_vectors_root.PSObject.Properties[[string]$moving[$boundIndex]]
      $vector = @($vectorProperty.Value)
      $values = @()
      foreach ($corner in @($box.min,$box.max)) {
        for ($axis=0;$axis -lt 3;$axis++) { $values += ([double]$corner[$axis]+[double]$vector[$axis]) }
      }
      $torqueBounds.Add((& $formatVector $values))
    }
    foreach ($box in @($(if ($installationNote) {$annotations.protected_bounds} else {$payload.stage_geometry_root.context_bounds}))) { $torqueBounds.Add((& $formatVector @($box.min+$box.max))) }
    Remove-Item -LiteralPath ($image + '.torque.json') -Force -ErrorAction SilentlyContinue
  }
  $manifestRows.Add((@(
    $image,
    ($moving -join ';'),
    ([double]$translation[0]).ToString('G17', $culture),
    ([double]$translation[1]).ToString('G17', $culture),
    ([double]$translation[2]).ToString('G17', $culture),
    ($nativeVisible -join ';'),
    $cameraSpec,
    $audit,
    ($focusOccurrences -join ';'),
    ($anchorRows -join ';'),
    ($receiverSelectionRows -join ';'),
    $encodedTranslations,
    ($torqueRows -join ';'),
    ($torqueBounds -join ';'),
    $torqueHash,
    $(if ($null -ne $payload.PSObject.Properties['completed_stage_only'] -and $payload.completed_stage_only) { 'completed' } else { 'installation' })
  ) -join "`t"))
  $renderedFiles.Add($image)
  $auditFiles.Add($audit)
  $framingAudits.Add([pscustomobject]@{
    task_id = $taskId
    image_file = [IO.Path]::GetFileName($image)
    zoom_to_selected_level = $selectedFitLevel
    selection_scope = [string]$nativeSelectedFit.selection_scope
    coordinate_adjustment = [string]$nativeSelectedFit.coordinate_adjustment
    terminal_stage_visibility = $(if ($null -ne $payload.PSObject.Properties['terminal_stage_visibility']) { $payload.terminal_stage_visibility } else { $null })
    visible_occurrences = @($payload.visible_occurrences)
    excluded_occurrences = @($(if ($null -ne $payload.PSObject.Properties['bom_visibility_exclusions']) { $payload.bom_visibility_exclusions }))
  })
  Write-Output ("[AGENT_RENDER] task {0} presentation_variant {1} camera {2} zoom {3}" -f $taskId,$VariantIndex,$cameraId,$zoom)
}

& (Join-Path $here 'test_license_binding.ps1') -LicenseFile $runtime.LicenseFile -CreoLoadpoint $runtime.CreoLoadpoint
$needsBuild = $false
foreach ($className in @('AutoCadDiscovery', 'ArrowProjection', 'TorqueLabelLayout', 'UnicodeVectorText', 'NativeTorqueAnnotations', 'RenderAssemblyImage', 'NativeArrowBatch', 'NativeArrowWorker')) {
  $classFile = Join-Path $here ('build\' + $className + '.class')
  $sourceFile = Join-Path $here ('src\' + $className + '.java')
  if (-not (Test-Path -LiteralPath $classFile) -or
      (Get-Item -LiteralPath $sourceFile).LastWriteTimeUtc -gt (Get-Item -LiteralPath $classFile).LastWriteTimeUtc) {
    $needsBuild = $true
  }
}
if ($needsBuild) {
  & (Join-Path $here 'build.ps1') -RuntimeConfig $runtime.ConfigPath
  if ($LASTEXITCODE -ne 0) { throw 'J-Link build failed.' }
}
$manifest = Join-Path $internalRoot ('native-arrow-' + [guid]::NewGuid().ToString('N') + '.tsv')
[IO.File]::WriteAllLines($manifest, $manifestRows, [Text.UTF8Encoding]::new($false))

function Test-NativeArtifactsReady {
  if ($Operation -eq 'Visibility') {
    foreach ($path in $visibilityFiles) {
      if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -lt 100) { return $false }
    }
    return $visibilityFiles.Count -eq ($manifestRows.Count * 4)
  }
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
  $startupHasher = [Security.Cryptography.SHA256]::Create()
  try { $startupKey = ([BitConverter]::ToString($startupHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($worker.ToLowerInvariant())))).Replace('-','').Substring(0,24) }
  finally { $startupHasher.Dispose() }
  $startupMutex = [Threading.Mutex]::new($false, ('Local\CreoSopWorkerStartup_' + $startupKey))
  $startupOwned = $false
  try {
    try { $startupOwned = $startupMutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds)) }
    catch [Threading.AbandonedMutexException] { $startupOwned = $true }
    if (-not $startupOwned) { throw 'Timed out waiting for this run worker startup lock.' }
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
            (Get-Date).ToUniversalTime().AddSeconds(-([Math]::Max($WorkerIdleSeconds,$TimeoutSeconds) + $CompletionGraceSeconds)))
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
    if ($NativeBindingsManifest) { $launcherArguments += ' -NativeBindingsManifest "' + $NativeBindingsManifest + '"' }
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
  } finally {
    if ($startupOwned) { $startupMutex.ReleaseMutex() }
    $startupMutex.Dispose()
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
    ($Operation.ToUpperInvariant() + "`t" + [IO.Path]::GetFileName($workerManifest) + "`n"),
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
  Add-Type -AssemblyName System.Drawing
  $bitmap = [System.Drawing.Bitmap]::new($image)
  try {
    if ($bitmap.Width -ne 1600 -or $bitmap.Height -ne 1600) {
      throw "Creo square publication export has invalid dimensions $($bitmap.Width)x$($bitmap.Height): $image"
    }
  }
  finally { $bitmap.Dispose() }
}
foreach ($item in $framingAudits) {
  $auditPath = Join-Path $output ([string]$item.task_id + '.framing.json')
  $temporaryAudit = $auditPath + '.tmp'
  $auditPayload = [ordered]@{
    schema_version = 'native-framing-audit/v1'
    task_id = [string]$item.task_id
    image_file = [string]$item.image_file
    creo_version_datecode = Split-Path -Leaf ([string]$runtime.CreoLoadpoint)
    command = 'ProCmdZoomIntoOutline'
    command_verified = $true
    selection_scope = [string]$item.selection_scope
    zoom_to_selected_level = [double]$item.zoom_to_selected_level
    max_commands_per_render = 1
    manual_pan_zoom_forbidden = $true
    coordinate_adjustment = [string]$item.coordinate_adjustment
    final_frame = @(1600, 1600)
    source_policy = 'native_viewport_overscan/v1'
    crop_policy = 'fixed_center_square/v1'
    padding_forbidden = $true
    stretching_forbidden = $true
    terminal_stage_visibility = $item.terminal_stage_visibility
    visible_occurrences = @($item.visible_occurrences)
    excluded_occurrences = @($item.excluded_occurrences)
    rendered_image_sha256 = (Get-FileHash -LiteralPath (Join-Path $output ([string]$item.image_file)) -Algorithm SHA256).Hash.ToLowerInvariant()
  }
  [IO.File]::WriteAllText(
    $temporaryAudit,
    (($auditPayload | ConvertTo-Json -Depth 4) + "`n"),
    [Text.UTF8Encoding]::new($false)
  )
  Move-Item -LiteralPath $temporaryAudit -Destination $auditPath -Force
}
Write-Output ("[AGENT_RENDER] complete {0} {1} tasks" -f $manifestRows.Count,$Operation.ToLowerInvariant())
