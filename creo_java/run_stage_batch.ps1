param(
  [Parameter(Mandatory=$true)][string]$ProductConfig,
  [Parameter(Mandatory=$true)][string]$JobsJson,
  [Parameter(Mandatory=$true)][string]$OutputFolder,
  [int]$StartIndex = 0,
  [int]$Count = 0,
  [string]$PreparedModelsRoot = ''
)
$document = Get-Content -Raw -LiteralPath $JobsJson | ConvertFrom-Json
$schema = [string]$document.schema_version
if ($schema -ne 'creo-render-jobs/v3') { throw '正式总装批次只接受 creo-render-jobs/v3；旧中间 ASM 任务已标记为 legacy。' }
$jobs = $document.jobs
$jobsRoot = Split-Path -Parent ([System.IO.Path]::GetFullPath($JobsJson))
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'ProductConfig.ps1')
$product = Get-AssemblySopProduct -ProjectRoot $root -ProductConfig $ProductConfig
$manifestPath = [string]$document.authoritative_assembly_manifest
if (-not $manifestPath) { throw 'v3 批次缺少 authoritative_assembly_manifest。' }
if (-not [System.IO.Path]::IsPathRooted($manifestPath)) { $manifestPath = Join-Path $jobsRoot $manifestPath }
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
if ([string]$manifest.schema_version -ne 'authoritative-assembly/v1') { throw '不支持的权威总装清单。' }
$authoritativeAssembly = Join-Path $product.ModelsRoot ([string]$manifest.assembly_file)
if ([string]$manifest.assembly_file -ne $product.FinalAssembly) { throw '产品配置 final_assembly 与权威总装清单不一致。' }
if (-not (Test-Path -LiteralPath $authoritativeAssembly)) { throw ('权威总装不存在: ' + $authoritativeAssembly) }
$manifestHash = [string]$manifest.assembly_sha256
if (-not $manifestHash) { throw '权威总装清单缺少 SHA-256。' }
$basisPath = [string]$manifest.camera_basis_file
if (-not [System.IO.Path]::IsPathRooted($basisPath)) { $basisPath = Join-Path $root $basisPath }
$basis = Get-Content -Raw -LiteralPath $basisPath | ConvertFrom-Json
$stop = if ($Count -gt 0) { [Math]::Min($StartIndex + $Count, $jobs.Count) } else { $jobs.Count }
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null
$allowedBatchRoot = [System.IO.Path]::GetFullPath((Join-Path $root 'data\runs'))
if ($PreparedModelsRoot) {
  $batchStagedModels = [System.IO.Path]::GetFullPath($PreparedModelsRoot)
  if (-not $batchStagedModels.StartsWith($allowedBatchRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'PreparedModelsRoot 必须位于项目 data\runs 内。'
  }
  if (-not (Test-Path -LiteralPath $batchStagedModels -PathType Container)) { throw 'PreparedModelsRoot 不存在。' }
}
else {
  $batchRunRoot = Join-Path $root ('data\runs\batch-render-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
  $batchStagedModels = Join-Path $batchRunRoot 'models'
  New-Item -ItemType Directory -Force -Path $batchRunRoot | Out-Null
  Copy-Item -LiteralPath $product.ModelsRoot -Destination $batchStagedModels -Recurse
  Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'isolated_config.pro') -Destination (Join-Path $batchStagedModels 'config.pro') -Force
}
Write-Output ("[BATCH] prepared_isolated_models {0}" -f $batchStagedModels)
for ($index = $StartIndex; $index -lt $stop; $index++) {
  $job = $jobs[$index]
  $target = Join-Path $OutputFolder ($job.job_id + '.jpg')
  $vector = $job.translation.vector
  $moving = [string]::Join(';', @($job.moving_occurrences))
  $visible = [string]::Join(';', @($job.visible_occurrences))
  if (-not $moving) { throw ('v3 任务缺少 moving_occurrences: ' + $job.job_id) }
  if (-not $visible) { throw ('v3 任务缺少 forward stage visible_occurrences: ' + $job.job_id) }
  if ([string]$job.stage_visibility.policy -eq 'forward_exact/v1') {
    $expectedVisible = @($job.stage_visibility.completed_occurrences) + @($job.moving_occurrences) + @($job.receiver_occurrences) + @($job.stage_visibility.required_context_occurrences)
    $expectedVisible = @($expectedVisible | Where-Object { $_ } | Sort-Object -Unique)
    $actualVisible = @($job.visible_occurrences | Where-Object { $_ } | Sort-Object -Unique)
    if (($expectedVisible -join ';') -ne ($actualVisible -join ';')) {
      throw ('forward_exact 可见集不等于此前件＋活动件＋接收件＋必要上下文: ' + $job.job_id)
    }
    $rigid = @($job.stage_visibility.rigid_completed_subassemblies)
    foreach ($path in $actualVisible) {
      if ($rigid -contains $path) { continue }
      if ($actualVisible | Where-Object { $_ -ne $path -and $_.StartsWith($path + '/') }) {
        throw ('可见集包含未经声明的宽泛父 occurrence ' + $path + ': ' + $job.job_id)
      }
    }
  }
  Write-Output ("[BATCH] {0}/{1} {2} {3}" -f ($index + 1), $jobs.Count, $job.bom_level, $job.title)
  $renderArgs = @{
    ModelsRoot = $product.ModelsRoot
    AssemblyFile = $authoritativeAssembly
    OutputJpeg = $target
    ExplodeOccurrencePaths = $moving
    Translation = @([double]$vector[0], [double]$vector[1], [double]$vector[2])
    VisibleOccurrencePaths = $visible
    ExpectedAssemblySha256 = $manifestHash
    PreparedModelsRoot = $batchStagedModels
  }
  # v3 jobs normally carry camera_contract_file only.  Read the retired inline
  # camera field defensively so PowerShell 7 does not treat its absence as a
  # property error before the formal camera contract is loaded.
  $camera = if ($job.PSObject.Properties['camera']) { $job.camera } else { $null }
  if ($camera -is [string] -and $camera -in @('fixed_123','fixed_456')) {
    $cameraId = [string]$camera
    $camera = [PSCustomObject]@{
      selected = [PSCustomObject]@{
        id = $cameraId
        position_direction_root = $basis.($cameraId + '_position_direction_root')
        up_reference_root = $basis.up_reference_root
      }
      framing = [PSCustomObject]@{ center = $true }
    }
  }
  if ($job.camera_contract_file) {
    $cameraPath = [string]$job.camera_contract_file
    if (-not [System.IO.Path]::IsPathRooted($cameraPath)) { $cameraPath = Join-Path $jobsRoot $cameraPath }
    $camera = Get-Content -Raw -LiteralPath $cameraPath | ConvertFrom-Json
    $cameraSchema = [string]$camera.schema_version
    if ($cameraSchema -notin @('creo-stage-camera-contract/v2','creo-stage-camera-contract/v3')) { throw ("不支持的相机合同: " + $cameraPath) }
    if ($cameraSchema -eq 'creo-stage-camera-contract/v2') { Write-Warning ("legacy_camera_contract_v2: " + $cameraPath) }
    if ($cameraSchema -eq 'creo-stage-camera-contract/v3') {
      $group = [string]$camera.view_policy.view_group
      $expectedGroup = if ([int]$camera.receiver_face.face_id -le 3) { '123' } else { '456' }
      if ([string]$camera.view_policy.id -ne 'fixed_two_view/v1') { throw ("v3 必须使用固定双视角策略: " + $cameraPath) }
      # Explosion direction and camera direction are independent.  The former
      # follows the receiving-face normal; the latter is whichever of the two
      # locked product views exposes both moving and receiving geometry.  Keep
      # the legacy face-to-view check only for contracts that were not created
      # from an explicit visual correction.
      $selectionStatus = [string]$camera.selection.status
      if ($selectionStatus -ne 'user_correction_selected' -and $group -ne $expectedGroup) {
        throw ("v3 固定视角策略与接收面不一致: " + $cameraPath)
      }
      if ([string]$camera.selected.id -ne ('fixed_' + $group)) { throw ("v3 selected 不是固定视角: " + $cameraPath) }
      # PAN is native framing only. It must not change the fixed 123/456 direction.
      $panValues = if ($null -eq $camera.framing.pan) { @() } else { @($camera.framing.pan) }
      if ($panValues.Count -ne 0 -and $panValues.Count -ne 2) {
        throw ("PAN 必须包含两个原生构图分量: " + $cameraPath)
      }
      $focus = $camera.framing.focus_context
      if ($focus) {
        if ([string]$focus.policy -ne 'stage_visible_bbox/v1') { throw ("不支持的特写焦点策略: " + $cameraPath) }
        if ([string]$focus.occlusion_policy -ne 'temporary_simplified_rep/v1') { throw ("正式特写必须使用临时简化表示: " + $cameraPath) }
        if ([string]$focus.section_fallback -ne 'receiver_normal_only/v1') { throw ("剖切回退必须受接收面法向约束: " + $cameraPath) }
        if (-not $camera.framing.look_at_stage) { throw ("特写焦点必须启用 LOOKAT_STAGE: " + $cameraPath) }
      }
    }
    Write-Output ("[BATCH] camera_contract {0}" -f $cameraPath)
  }
  if ($camera -and $camera.selected -and $camera.selected.position_direction_root) {
    $legacyCameraRotate = if ($job.PSObject.Properties['camera_rotate']) { $job.camera_rotate } else { $null }
    if ($legacyCameraRotate) { throw ("新相机合同不能包含 camera_rotate: " + $job.job_id) }
    $culture = [System.Globalization.CultureInfo]::InvariantCulture
    $formatVector = { param($values) (($values | ForEach-Object { [double]$_ }) | ForEach-Object { $_.ToString('G17', $culture) }) -join ':' }
    $position = & $formatVector $camera.selected.position_direction_root
    $upValues = if ($camera.selected.up_reference_root) { $camera.selected.up_reference_root } elseif ($camera.up_reference_root) { $camera.up_reference_root } else { @(0,0,1) }
    $up = & $formatVector $upValues
    $spec = "ABS:$position,UP:$up"
    if ($camera.framing -and $camera.framing.zoom) { $spec += ',' + ('ZOOM:' + ([double]$camera.framing.zoom).ToString('G17', $culture)) }
    if (-not $camera.framing -or $null -eq $camera.framing.center -or [bool]$camera.framing.center) { $spec += ',CENTER' }
    if ($camera.framing -and $camera.framing.pan -and @($camera.framing.pan).Count -eq 2) {
      $spec += ',PAN:' + ([double]$camera.framing.pan[0]).ToString('G17', $culture) + ':' + ([double]$camera.framing.pan[1]).ToString('G17', $culture)
    }
    if ($camera.framing -and $camera.framing.look_at_stage) { $spec += ',LOOKAT_STAGE' }
    if ($camera.framing -and $camera.framing.frame) { $spec += ',FRAME:' + ([string]$camera.framing.frame).ToUpperInvariant() }
    $renderArgs.CameraSpec = $spec
    if ($camera.framing -and $camera.framing.frame) { $renderArgs.Frame = [string]$camera.framing.frame }
    Write-Output ("[BATCH] absolute_camera {0}" -f $spec)
  }
  else { throw ("任务缺少结构化绝对相机: " + $job.job_id) }
  if ($job.render.draw_install_arrows) {
    $renderArgs.DrawInstallArrows = $true
    $renderArgs.ArrowAuditJson = [System.IO.Path]::ChangeExtension($target, '.arrow.json')
  }
  & (Join-Path $PSScriptRoot 'run_render.ps1') @renderArgs
  if (-not $?) { throw ("Creo render failed for " + $job.job_id) }
}
Write-Output ("[BATCH] complete {0} jobs" -f ($stop - $StartIndex))
