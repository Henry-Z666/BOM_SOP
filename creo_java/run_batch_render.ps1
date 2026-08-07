param([Parameter(Mandatory=$true)][string]$JobsJson, [int]$Limit = 0, [string]$OutputFolder = 'batch_v2')
$jobs = (Get-Content -Raw -LiteralPath $JobsJson | ConvertFrom-Json).jobs
$root = Split-Path -Parent $PSScriptRoot
$count = 0
foreach ($job in $jobs) {
  if ($Limit -gt 0 -and $count -ge $Limit) { break }
  $base = Join-Path $root ('outputs\images\jlink\' + $OutputFolder + '\' + $job.job_id)
  $v = $job.translation.vector
  $ids = [string]::Join(',', @($job.moving_feature_ids))
  & (Join-Path $PSScriptRoot 'run_render.ps1') -AssemblyFile (Join-Path $root ('零件图\' + $job.assembly_file)) -OutputJpeg ($base + '_exploded.jpg') -ExplodeComponentIds $ids -Translation @($v[0], $v[1], $v[2])
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  & (Join-Path $root 'scripts\fit_creo_image.ps1') -Path ($base + '_exploded.jpg')
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $count++
}
Write-Output "Completed $count render jobs."
