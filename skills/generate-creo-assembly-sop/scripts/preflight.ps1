param(
  [Parameter(Mandatory=$true)][string]$ProjectRoot,
  [string]$JobContract = 'data\runs\corrected-v2-render-jobs.json',
  [string]$OutputJson = ''
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($ProjectRoot)
$errors = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]
$details = [ordered]@{}

function Require-File([string]$RelativePath) {
  $full = Join-Path $root $RelativePath
  if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
    $errors.Add("missing file: $RelativePath")
  }
  return $full
}

if (-not (Test-Path -LiteralPath $root -PathType Container)) {
  throw "project root does not exist: $root"
}

$required = @(
  'creo_java\run_stage_batch.ps1',
  'creo_java\run_render.ps1',
  'creo_java\src\RenderAssemblyImage.java',
  'creo_java\src\ArrowProjection.java',
  'scripts\fit_creo_image.ps1'
)
foreach ($path in $required) { [void](Require-File $path) }

$contractPath = Require-File $JobContract
if (Test-Path -LiteralPath $contractPath -PathType Leaf) {
  try {
    $contract = Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $schema = if ($contract.schema_version) { [string]$contract.schema_version } else { [string]$contract.schema }
    $details.contract_schema = $schema
    $details.job_count = @($contract.jobs).Count
    if ($schema -notmatch '^(creo-render-jobs|step-contract)/v([3-9]|[1-9][0-9]+)$') {
      $errors.Add("formal job contract must be creo-render-jobs/v3 or a supported newer schema: $schema")
    }
    if (@($contract.jobs).Count -eq 0) { $errors.Add('job contract contains no jobs') }

    if ($contract.authoritative_assembly_manifest) {
      $manifestPath = Join-Path (Split-Path -Parent $contractPath) ([string]$contract.authoritative_assembly_manifest)
      if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        $errors.Add("authoritative assembly manifest missing: $($contract.authoritative_assembly_manifest)")
      }
    } else {
      $errors.Add('job contract has no authoritative_assembly_manifest')
    }

    $ids = @{}
    foreach ($job in @($contract.jobs)) {
      $id = [string]$job.job_id
      if ([string]::IsNullOrWhiteSpace($id)) { $errors.Add('job without job_id'); continue }
      if ($ids.ContainsKey($id)) { $errors.Add("duplicate job_id: $id") } else { $ids[$id] = $true }
      if (@($job.moving_occurrences).Count -eq 0) { $errors.Add("$id has no moving occurrences") }
      if (@($job.visible_occurrences).Count -eq 0) { $errors.Add("$id has no visible occurrences") }
      $moving = @($job.moving_occurrences | ForEach-Object { [string]$_ })
      $visible = @($job.visible_occurrences | ForEach-Object { [string]$_ })
      foreach ($occurrence in $moving) {
        if ($visible -notcontains $occurrence) { $errors.Add("$id moving occurrence is not visible: $occurrence") }
      }
      $cameraReference = if ($job.camera_contract_file) { [string]$job.camera_contract_file } else { [string]$job.camera_contract }
      if ($cameraReference) {
        $cameraPath = Join-Path (Split-Path -Parent $contractPath) $cameraReference
        if (-not (Test-Path -LiteralPath $cameraPath -PathType Leaf)) {
          $errors.Add("$id camera contract missing: $cameraReference")
        } else {
          try {
            $camera = Get-Content -LiteralPath $cameraPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $cameraId = if ($camera.selected.id) { [string]$camera.selected.id } else { [string]$job.camera_id }
            if ($cameraId -notin @('fixed_123','fixed_456')) {
              $errors.Add("$id uses non-fixed camera: $cameraId")
            }
          } catch {
            $errors.Add("$id camera contract cannot be parsed: $cameraReference")
          }
        }
      } else {
        $errors.Add("$id has no camera contract reference")
      }
    }
  } catch {
    $errors.Add("cannot parse contract $JobContract`: $($_.Exception.Message)")
  }
}

$java = Get-Command java -ErrorAction SilentlyContinue
if ($null -eq $java) { $warnings.Add('java is not on PATH; run_render.ps1 may still use the bundled Creo JRE') }
$details.project_root = $root
$details.contract = $contractPath
$details.java_on_path = ($null -ne $java)

$result = [ordered]@{
  schema = 'creo-assembly-sop-preflight/v1'
  status = $(if ($errors.Count -eq 0) { 'passed' } else { 'failed' })
  errors = @($errors)
  warnings = @($warnings)
  details = $details
}
$json = $result | ConvertTo-Json -Depth 8
if ($OutputJson) {
  $output = [System.IO.Path]::GetFullPath($OutputJson)
  $parent = Split-Path -Parent $output
  if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
  [System.IO.File]::WriteAllText($output, $json, [System.Text.UTF8Encoding]::new($false))
}
$json
if ($errors.Count -gt 0) { exit 1 }
