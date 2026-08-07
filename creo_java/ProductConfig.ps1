Set-StrictMode -Version Latest

function Resolve-ProductPath {
  param([Parameter(Mandatory = $true)][string]$ProjectRoot, [Parameter(Mandatory = $true)][string]$Value)
  if ([System.IO.Path]::IsPathRooted($Value)) { return [System.IO.Path]::GetFullPath($Value) }
  return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Value))
}

function Get-AssemblySopProduct {
  param([Parameter(Mandatory = $true)][string]$ProjectRoot, [Parameter(Mandatory = $true)][string]$ProductConfig)
  $configPath = Resolve-ProductPath -ProjectRoot $ProjectRoot -Value $ProductConfig
  if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw "产品配置不存在：$configPath" }
  try { $config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "无法解析产品配置 $configPath：$($_.Exception.Message)" }
  if ([string]$config.schema_version -ne 'assembly-sop-product/v1') { throw "不支持的产品配置版本：$($config.schema_version)" }
  foreach ($field in @('product_id', 'bom_file', 'models_dir', 'sop_template', 'final_assembly')) {
    if (-not [string]$config.$field) { throw "产品配置缺少 $field：$configPath" }
  }
  $modelsRoot = Resolve-ProductPath -ProjectRoot $ProjectRoot -Value ([string]$config.models_dir)
  if (-not (Test-Path -LiteralPath $modelsRoot -PathType Container)) { throw "产品模型目录不存在：$modelsRoot" }
  [pscustomobject]@{
    ConfigPath = $configPath
    ProductId = [string]$config.product_id
    BomFile = Resolve-ProductPath -ProjectRoot $ProjectRoot -Value ([string]$config.bom_file)
    ModelsRoot = $modelsRoot
    SopTemplate = Resolve-ProductPath -ProjectRoot $ProjectRoot -Value ([string]$config.sop_template)
    FinalAssembly = [string]$config.final_assembly
    FinalAssemblyPath = Join-Path $modelsRoot ([string]$config.final_assembly)
  }
}
