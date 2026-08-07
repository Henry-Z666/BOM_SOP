param(
  [Parameter(Mandatory=$true)][string]$ProductConfig,
  [Parameter(Mandatory=$true)][string]$JobsJson,
  [Parameter(Mandatory=$true)][string]$OutputFolder,
  [int]$StartIndex = 0,
  [int]$Count = 0,
  [int]$TimeoutSeconds = 180
)

# Creo may leave the J-Link launcher process alive after it has exported the
# JPEG and arrow audit.  Each job is deliberately isolated: completion is the
# pair of immutable artifacts, not a launcher exit code.  The exact launcher
# process is then stopped, never the source model or a user-owned Creo session.
$doc = Get-Content -Raw -LiteralPath $JobsJson | ConvertFrom-Json
$jobs = @($doc.jobs)
$root = Split-Path -Parent $PSScriptRoot
$start = [Math]::Max(0, $StartIndex)
$stop = if ($Count -gt 0) { [Math]::Min($start + $Count, $jobs.Count) } else { $jobs.Count }
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null

for ($index=$start; $index -lt $stop; $index++) {
  $job = $jobs[$index]
  $target = Join-Path $OutputFolder ($job.job_id + '.jpg')
  $audit = [System.IO.Path]::ChangeExtension($target, '.arrow.json')
  Remove-Item -LiteralPath $target,$audit -Force -ErrorAction SilentlyContinue
  $log = Join-Path $OutputFolder ($job.job_id + '.launcher.log')
  Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
  Write-Output ("[BOUNDED] {0}/{1} {2} {3}" -f ($index+1),$jobs.Count,$job.bom_level,$job.title)
  $arg = '-NoProfile -ExecutionPolicy Bypass -File "' + (Join-Path $PSScriptRoot 'run_stage_batch.ps1') + '" -ProductConfig "' + [System.IO.Path]::GetFullPath($ProductConfig) + '" -JobsJson "' + [System.IO.Path]::GetFullPath($JobsJson) + '" -OutputFolder "' + [System.IO.Path]::GetFullPath($OutputFolder) + '" -StartIndex ' + $index + ' -Count 1'
  $process = Start-Process -FilePath 'pwsh.exe' -ArgumentList $arg -PassThru -RedirectStandardOutput $log -RedirectStandardError ($log + '.err') -WindowStyle Hidden
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  $ready = $false
  while ((Get-Date) -lt $deadline) {
    if ((Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $audit)) {
      Start-Sleep -Seconds 4
      if ((Get-Item -LiteralPath $target).Length -gt 10000 -and (Get-Item -LiteralPath $audit).Length -gt 100) { $ready = $true; break }
    }
    Start-Sleep -Seconds 2
    if (-not $process.HasExited) { $process.Refresh() }
  }
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
  if (-not $ready) {
    $detail = if (Test-Path -LiteralPath $log) { Get-Content -Raw -LiteralPath $log } else { '' }
    throw ("J-Link job did not produce both JPEG and arrow audit: " + $job.job_id + "`n" + $detail)
  }
  & (Join-Path $root 'scripts\fit_creo_image.ps1') -Path $target -Frame square
  # fit_creo_image is a PowerShell script (not a native executable); validate
  # its actual artifact rather than an inherited $LASTEXITCODE from J-Link.
  Add-Type -AssemblyName System.Drawing
  $bitmap = [System.Drawing.Bitmap]::new($target)
  try {
    if ($bitmap.Width -ne 1600 -or $bitmap.Height -ne 1600) { throw ('fixed square output failed: ' + $job.job_id) }
  }
  finally { $bitmap.Dispose() }
  $arrow = Get-Content -Raw -LiteralPath $audit | ConvertFrom-Json
  if ([string]$arrow.status -ne 'passed') { throw ('arrow audit failed: ' + $job.job_id) }
  $covered = @($arrow.arrows | ForEach-Object { $_.covered_occurrences } | ForEach-Object { $_ }) | Sort-Object
  $expected = @($job.moving_occurrences) | Sort-Object
  if (($covered -join ';') -ne ($expected -join ';')) { throw ('arrow occurrence coverage mismatch: ' + $job.job_id) }
  Write-Output ('[BOUNDED] passed ' + $job.job_id)
}
Write-Output ('[BOUNDED] completed ' + ($stop-$start) + ' jobs')
