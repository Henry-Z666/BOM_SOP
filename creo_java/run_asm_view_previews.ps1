param([string]$OutputFolder = (Join-Path (Split-Path -Parent $PSScriptRoot) 'outputs\images\jlink\asm_view_previews'))

$sourceModels = Join-Path (Split-Path -Parent $PSScriptRoot) '零件图'
New-Item -ItemType Directory -Force -Path $OutputFolder | Out-Null
Get-ChildItem -LiteralPath $sourceModels -File -Filter '*.asm.*' | Sort-Object Name | ForEach-Object {
  $label = $_.Name -replace '[^A-Za-z0-9]+', '_'
  $front = Join-Path $OutputFolder ($label + '_default.jpg')
  $back = Join-Path $OutputFolder ($label + '_back.jpg')
  if ((Test-Path -LiteralPath $front) -and (Test-Path -LiteralPath $back)) {
    Write-Host "[PREVIEW] skip complete $($_.Name)"
    return
  }
  Write-Host "[PREVIEW] $($_.Name)"
  & (Join-Path $PSScriptRoot 'run_render.ps1') -AssemblyFile $_.FullName -OutputJpeg $front -SecondOutputJpeg $back -SecondCameraRotate 'Y:180'
  if ($LASTEXITCODE -ne 0) { throw "预览渲染失败：$($_.Name)" }
}
