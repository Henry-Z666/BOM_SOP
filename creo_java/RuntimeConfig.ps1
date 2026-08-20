Set-StrictMode -Version Latest

function Resolve-RuntimeCommand {
  param([string]$Configured, [string]$Name)
  $command = if ($Configured) { $Configured } else { $Name }
  $resolved = Get-Command $command -ErrorAction SilentlyContinue
  if ($null -eq $resolved) { throw "找不到运行时命令 $Name：$command。请在 config/creo-runtime.json 中配置，或加入 PATH。" }
  return $resolved.Source
}

function Get-CreoRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$ConfigPath = $env:CREO_RUNTIME_CONFIG
  )

  if (-not $ConfigPath) { $ConfigPath = Join-Path $ProjectRoot 'config\creo-runtime.json' }
  $configFile = [System.IO.Path]::GetFullPath($ConfigPath)
  if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
    throw "缺少本机运行时配置：$configFile。请复制 config/creo-runtime.example.json 为 config/creo-runtime.json 并填写 Creo 与许可证路径。"
  }

  try { $config = Get-Content -LiteralPath $configFile -Raw -Encoding UTF8 | ConvertFrom-Json }
  catch { throw "无法解析 Creo 运行时配置 $configFile：$($_.Exception.Message)" }
  if ([string]$config.schema_version -ne 'creo-runtime/v1') { throw "不支持的 Creo 运行时配置版本：$($config.schema_version)" }

  $loadpoint = [Environment]::ExpandEnvironmentVariables([string]$config.creo_loadpoint)
  $license = [Environment]::ExpandEnvironmentVariables([string]$config.license_file)
  if (-not (Test-Path -LiteralPath $loadpoint -PathType Container)) { throw "Creo 安装目录不存在：$loadpoint" }
  if (-not (Test-Path -LiteralPath $license -PathType Leaf)) { throw "Creo 授权文件不存在：$license" }

  $common = Join-Path $loadpoint 'Common Files'
  $native = Join-Path $common 'x86e_win64\lib'
  $parametric = Join-Path $loadpoint 'Parametric'
  $start = Join-Path $parametric 'bin\parametric.bat'
  $comm = Join-Path $common 'x86e_win64\obj\pro_comm_msg.exe'
  $nameService = Join-Path $common 'x86e_win64\nms\nmsd.exe'
  foreach ($path in @($common, $native, $parametric, $start, $comm, $nameService)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Creo 运行时缺少必要文件：$path" }
  }

  [pscustomobject]@{
    ConfigPath = $configFile
    CreoLoadpoint = $loadpoint
    LicenseFile = $license
    CommonFiles = $common
    NativeLibrary = $native
    ParametricDirectory = $parametric
    CreoCommand = ('"' + $start + '"')
    ProCommMessage = $comm
    NameService = $nameService
    CreoApp = if ($config.creo_app) { [string]$config.creo_app } else { 'PMA' }
    CreoFeatureName = [string]$config.creo_feature_name
    JavaCommand = Resolve-RuntimeCommand ([string]$config.java_command) 'java'
    JavacCommand = Resolve-RuntimeCommand ([string]$config.javac_command) 'javac'
    PythonCommand = Resolve-RuntimeCommand ([string]$config.python_command) 'python'
  }
}

function Start-CreoNameService {
  param(
    [Parameter(Mandatory = $true)]$Runtime,
    [int]$IdleTimeoutSeconds = 300
  )
  $target = [System.IO.Path]::GetFullPath([string]$Runtime.NameService)
  $running = Get-Process -Name 'nmsd' -ErrorAction SilentlyContinue | Where-Object {
    try { [System.IO.Path]::GetFullPath($_.Path) -eq $target } catch { $false }
  } | Select-Object -First 1
  if ($null -ne $running) { return $running }
  $process = Start-Process -FilePath $target `
    -ArgumentList @('-noservice', '-timeout', [string]$IdleTimeoutSeconds) `
    -WindowStyle Hidden -PassThru
  Start-Sleep -Milliseconds 500
  if ($process.HasExited) {
    throw "Creo name service exited during startup: $target"
  }
  return $process
}

function Set-CreoRuntimeEnvironment {
  param([Parameter(Mandatory = $true)]$Runtime)
  $env:PATH = $Runtime.NativeLibrary + ';' + (Join-Path $Runtime.ParametricDirectory 'bin') + ';' + $env:PATH
  $env:PRO_DIRECTORY = $Runtime.ParametricDirectory
  $env:CREO_COMMON_FILES = $Runtime.CommonFiles
  $env:PRO_COMM_MSG_EXE = $Runtime.ProCommMessage
  $env:PTC_D_LICENSE_FILE = $Runtime.LicenseFile
  $env:CREO_APP = $Runtime.CreoApp
  if ($Runtime.CreoFeatureName) { $env:CREOPMA_FEATURE_NAME = $Runtime.CreoFeatureName }
  else { Remove-Item Env:CREOPMA_FEATURE_NAME -ErrorAction SilentlyContinue }
}
