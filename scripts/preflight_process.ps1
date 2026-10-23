param(
  [string]$RuntimeConfig = '',
  [switch]$CheckLicense,
  [string]$OutputJson = ''
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$errors = New-Object Collections.Generic.List[string]
foreach ($relative in @('creo_java/RuntimeConfig.ps1','creo_java/run_input_discovery.ps1',
    'creo_java/run_agent_native_batch.ps1','creo_java/src/NativeTorqueAnnotations.java',
    'creo_java/src/TorqueLabelLayout.java','assets/sop-template.xlsx')) {
  if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) { $errors.Add('Missing: ' + $relative) }
}
try {
  . (Join-Path $root 'creo_java/RuntimeConfig.ps1')
  $runtime = Get-CreoRuntime -ProjectRoot $root -ConfigPath $RuntimeConfig
  & $runtime.PythonCommand -c 'import openpyxl, PIL, numpy'
  if ($LASTEXITCODE -ne 0) { $errors.Add('Python dependencies unavailable') }
  if ($CheckLicense) { $null = & (Join-Path $root 'creo_java/test_license_binding.ps1') -LicenseFile $runtime.LicenseFile -CreoLoadpoint $runtime.CreoLoadpoint }
} catch { $errors.Add($_.Exception.Message) }
$result = [ordered]@{ schema_version='process-preflight/v1'; status=$(if($errors.Count){'failed'}else{'passed'}); license_checked=[bool]$CheckLicense; license_check_scope='host_binding_only'; native_startup_status='not_checked'; errors=@($errors) }
$json = $result | ConvertTo-Json -Depth 4
if ($OutputJson) { [IO.File]::WriteAllText([IO.Path]::GetFullPath($OutputJson),$json,[Text.UTF8Encoding]::new($false)) }
$json
if ($errors.Count) { exit 1 }
