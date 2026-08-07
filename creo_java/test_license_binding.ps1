param(
  [Parameter(Mandatory=$true)][string]$LicenseFile,
  [Parameter(Mandatory=$true)][string]$CreoLoadpoint
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $LicenseFile -PathType Leaf)) { throw ('PTC 授权文件不存在: ' + $LicenseFile) }
$ptcHostId = Join-Path $CreoLoadpoint 'Parametric\bin\ptchostid.bat'
if (-not (Test-Path -LiteralPath $ptcHostId -PathType Leaf)) { throw ('找不到 PTC Host ID 工具: ' + $ptcHostId) }

$licenseText = Get-Content -LiteralPath $LicenseFile -Raw -Encoding UTF8
$licensed = [regex]::Match($licenseText, 'PTC_HOSTID=([0-9A-Fa-f-]+)')
if (-not $licensed.Success) { throw '授权文件没有可验证的 PTC_HOSTID 绑定。' }
$licensedHost = $licensed.Groups[1].Value.Replace('-', '').ToUpperInvariant()

Push-Location (Split-Path -Parent $ptcHostId)
try { $hostOutput = (& $ptcHostId 2>&1 | Out-String) }
finally { Pop-Location }
$current = [regex]::Match($hostOutput, '([0-9A-Fa-f]{2}(?:[-:][0-9A-Fa-f]{2}){5})')
if (-not $current.Success) { throw 'PTC Host ID 工具未返回可验证的主机 ID。' }
$currentHost = $current.Groups[1].Value.Replace(':', '').Replace('-', '').ToUpperInvariant()
if ($currentHost -ne $licensedHost) {
  throw 'PTC 学生许可证与当前主机 ID 不匹配（Invalid host）。请在 PTC 学生许可渠道重新激活/重新绑定本机；渲染未启动。'
}

[pscustomobject]@{ schema_version = 'creo-license-binding/v1'; status = 'passed'; license_file = $LicenseFile } | ConvertTo-Json
