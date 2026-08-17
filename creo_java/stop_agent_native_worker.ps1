param(
  [Parameter(Mandatory=$true)][string]$RunWorkspaceRoot,
  [Parameter(Mandatory=$true)][string]$WorkerRoot,
  [ValidateRange(1, 60)][int]$TimeoutSeconds = 15
)

$ErrorActionPreference = 'Stop'
$runRoot = [IO.Path]::GetFullPath($RunWorkspaceRoot)
$worker = [IO.Path]::GetFullPath($WorkerRoot)
if (-not $worker.StartsWith($runRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
  throw 'WorkerRoot must stay inside the current Agent run workspace.'
}
$current = Join-Path $worker 'current-worker.tsv'
if (-not (Test-Path -LiteralPath $current -PathType Leaf)) { return }
$fields = (Get-Content -Raw -LiteralPath $current).Trim().Split("`t")
if ($fields.Count -ne 4 -or $fields[0] -ne 'native-arrow-worker-current/v1') { return }
$generation = [IO.Path]::GetFullPath($fields[1])
if (-not $generation.StartsWith($worker + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { return }
$workerPid = 0
if (-not [int]::TryParse($fields[2], [ref]$workerPid)) { return }
$process = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
if ($null -eq $process -or -not (Test-Path -LiteralPath (Join-Path $generation 'ready.tsv') -PathType Leaf)) {
  Remove-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
  return
}

$commandId = 'shutdown-' + [guid]::NewGuid().ToString('N')
$commands = Join-Path $generation 'commands'
$results = Join-Path $generation 'results'
New-Item -ItemType Directory -Force -Path $commands,$results | Out-Null
$temporary = Join-Path $commands ($commandId + '.request.tmp')
$request = Join-Path $commands ($commandId + '.request')
[IO.File]::WriteAllText($temporary, "SHUTDOWN`n", [Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $request
$result = Join-Path $results ($commandId + '.result')
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $result -PathType Leaf)) {
  Start-Sleep -Milliseconds 100
}
$process = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
if ($null -ne $process) {
  $exited = $process.WaitForExit([Math]::Max(1000, $TimeoutSeconds * 1000))
  if (-not $exited) { Stop-Process -Id $workerPid -Force -ErrorAction SilentlyContinue }
}
Remove-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
