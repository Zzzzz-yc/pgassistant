#Requires -Version 5.1
<#
.SYNOPSIS
    pgassistant 一键启动（Windows；PowerShell 5.1 与 7 都能跑）。

.DESCRIPTION
    依次做五件事，任何一步失败就停下并打印原因：
      1. 检查 Python >= 3.11 与 Node >= 20；
      2. 缺 .venv 才创建，并确保 backend/requirements.txt 已装好；
      3. frontend/dist 不存在才 npm ci（无 lock 时 npm install）+ npm run build；
      4. 幂等跑 bootstrap_index.py（内容没变会直接跳过）；
      5. 在 8000–8010 里挑空闲端口，写 data/runtime.json，起 uvicorn，开浏览器。

    文件编码必须是 UTF-8 **带 BOM**：Windows PowerShell 5.1 读无 BOM 的脚本时按
    系统 ANSI（zh-CN 上是 GBK）解码，中文字符串会被切坏并引发语法/运行时怪错。

.PARAMETER NoBrowser
    只起服务，不开浏览器。

.PARAMETER Dev
    开发态：设 PG_DEV_ORIGINS=http://127.0.0.1:5173 并另起 Vite（:5173，代理 /api 到本进程端口）。

.EXAMPLE
    .\start.ps1
    .\start.ps1 -NoBrowser
    .\start.ps1 -Dev
#>
[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$Dev
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Frontend = Join-Path $Root "frontend"
$RuntimeFile = Join-Path $Root "data\runtime.json"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "  ok  $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "  !!  $Message" -ForegroundColor Yellow }
function Stop-Fatal([string]$Message) {
    Write-Host "  !!  $Message" -ForegroundColor Red
    exit 1
}

function Test-PythonAtLeast311 {
    <# 真跑一次 --version：PATH 上的 python 可能是 WindowsApps 的占位符（退出码 9009）。 #>
    param([string]$Exe, [string[]]$PreArgs = @())
    try {
        $raw = & $Exe @PreArgs --version 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0) { return $false }
        if ($raw -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            return ($major -gt 3 -or ($major -eq 3 -and $minor -ge 11))
        }
    } catch {
        return $false
    }
    return $false
}

function Find-SystemPython {
    <# 顺序：PATH 上所有真 python.exe（跳过 WindowsApps 占位符）→ py 启动器。 #>
    $candidates = @()
    foreach ($name in @("python", "python3")) {
        $lines = & where.exe $name 2>$null
        if ($lines) { $candidates += @($lines) }
    }
    foreach ($exe in $candidates) {
        if (-not $exe) { continue }
        if ($exe -like "*\WindowsApps\*") { continue }
        if (Test-PythonAtLeast311 -Exe $exe) { return @{ exe = $exe; args = @() } }
    }
    $py = Get-Command "py" -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($version in @("-3.13", "-3.12", "-3.11", "-3")) {
            if (Test-PythonAtLeast311 -Exe $py.Source -PreArgs @($version)) {
                return @{ exe = $py.Source; args = @($version) }
            }
        }
    }
    return $null
}

function Get-NodeExe {
    <# 只认 Node >= 20。 #>
    $cmd = Get-Command "node" -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    $raw = & $cmd.Source --version 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { return $null }
    if ($raw -match "v(\d+)\.") {
        if ([int]$Matches[1] -ge 20) { return $cmd.Source }
    }
    return $null
}

function Get-NpmExe {
    <# npm.cmd 优先：避免 PowerShell 执行策略拦住 npm.ps1。 #>
    foreach ($name in @("npm.cmd", "npm")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    return $null
}

function Find-FreePort {
    <# 只在 127.0.0.1 上试，和 uvicorn 的绑定地址保持一致。 #>
    param([int]$Start = 8000, [int]$End = 8010)
    for ($port = $Start; $port -le $End; $port++) {
        $listener = $null
        try {
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $port)
            $listener.Start()
            return $port
        } catch {
            continue
        } finally {
            if ($listener) { $listener.Stop() }
        }
    }
    return $null
}

function Wait-Http {
    <# 只用「200 才算就绪」；非 200 在 5.1 会抛异常，正好当「还没起来」处理。 #>
    param([string]$Url, [int]$TimeoutSeconds = 40)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -TimeoutSec 2 -UseBasicParsing
            if ($response.StatusCode -eq 200) { return $true }
        } catch {
            # 还没起来，继续等
        }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

# ---------------------------------------------------------------- 1. 工具链
Write-Step "检查工具链"

$venvReady = $false
if (Test-Path -LiteralPath $VenvPython) {
    if (Test-PythonAtLeast311 -Exe $VenvPython) {
        $venvReady = $true
    } else {
        Stop-Fatal "现有 .venv 的 Python 低于 3.11；删掉 .venv 目录后重跑本脚本。"
    }
}

if (-not $venvReady) {
    $systemPython = Find-SystemPython
    if (-not $systemPython) {
        Stop-Fatal "找不到 Python >= 3.11。装一个（https://www.python.org/downloads/）后重开终端。"
    }
    Write-Host "  用 $($systemPython.exe) $($systemPython.args -join ' ') 创建 .venv" -ForegroundColor DarkGray
} else {
    $systemPython = $null
}

$node = Get-NodeExe
if (-not $node) {
    Stop-Fatal "找不到 Node >= 20（node --version 应 >= v20）。装一个后重开终端。"
}
$npm = Get-NpmExe
if (-not $npm) { Stop-Fatal "找不到 npm（随 Node 一起安装）。" }
Write-Ok "node: $node"
Write-Ok "npm:  $npm"

# ------------------------------------------------------------------- 2. venv
Write-Step "准备 Python 环境（.venv）"
$createdVenv = $false
if (-not $venvReady) {
    & $systemPython.exe @($systemPython.args) -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) { Stop-Fatal "python -m venv .venv 失败（退出码 $LASTEXITCODE）" }
    $createdVenv = $true
    Write-Ok "已创建 .venv"
} else {
    Write-Ok ".venv 已存在，跳过创建"
}

& $VenvPython -c "import fastapi, uvicorn, httpx, pydantic" 2>$null
$depsReady = ($LASTEXITCODE -eq 0)
if ($createdVenv -or -not $depsReady) {
    Write-Step "安装 backend/requirements.txt（首次会慢一点）"
    & $VenvPython -m pip install --disable-pip-version-check -r (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Stop-Fatal "pip install 失败（退出码 $LASTEXITCODE）。网络慢可改用镜像：`$env:PIP_INDEX_URL='https://pypi.tuna.tsinghua.edu.cn/simple'"
    }
    Write-Ok "Python 依赖就绪"
} else {
    Write-Ok "Python 依赖已就绪，跳过安装"
}

# -------------------------------------------------------------- 3. 前端构建
$distIndex = Join-Path $Frontend "dist\index.html"
if (-not (Test-Path -LiteralPath $distIndex)) {
    Write-Step "构建前端（frontend/dist 不存在）"
    Push-Location -LiteralPath $Frontend
    try {
        if (-not (Test-Path -LiteralPath (Join-Path $Frontend "node_modules"))) {
            $lock = Join-Path $Frontend "package-lock.json"
            $npmArgs = if (Test-Path -LiteralPath $lock) { @("ci") } else { @("install") }
            & $npm @npmArgs --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) {
                Stop-Fatal "npm $($npmArgs[0]) 失败（退出码 $LASTEXITCODE）。网络问题可加 --registry=https://registry.npmmirror.com 重试一次。"
            }
        }
        & $npm run build
        if ($LASTEXITCODE -ne 0) { Stop-Fatal "npm run build 失败（退出码 $LASTEXITCODE）" }
    } finally {
        Pop-Location
    }
    if (-not (Test-Path -LiteralPath $distIndex)) { Stop-Fatal "构建结束但 frontend/dist/index.html 仍不存在" }
    Write-Ok "前端构建完成"
} else {
    Write-Ok "frontend/dist 已存在，跳过构建（改了前端代码请先 npm run build）"
}

# ----------------------------------------------------------------- 4. 索引
Write-Step "准备数据库与索引（幂等，内容没变会跳过）"
& $VenvPython (Join-Path $Root "backend\scripts\bootstrap_index.py")
if ($LASTEXITCODE -ne 0) { Stop-Fatal "bootstrap_index.py 失败（退出码 $LASTEXITCODE）" }
Write-Ok "索引就绪"

# ------------------------------------------------------------- 5. 起服务
$port = Find-FreePort -Start 8000 -End 8010
if (-not $port) { Stop-Fatal "8000–8010 都被占用了，先关掉其它实例。" }

# 配置端口必须与监听端口一致：core/config.py 按 PG_PORT 生成 Origin 白名单，
# 只传 --port 而不同步 PG_PORT，选到 8001 时所有写操作都会被 403 BAD_ORIGIN 挡掉。
$env:PG_PORT = "$port"

$env:PYTHONPATH = Join-Path $Root "backend"
if ($Dev) {
    # 开发态：Vite :5173 的写请求要过 Origin 白名单
    $env:PG_DEV_ORIGINS = "http://127.0.0.1:5173"
    $env:PG_BACKEND_ORIGIN = "http://127.0.0.1:$port"
}

$baseUrl = "http://127.0.0.1:$port"
Write-Step "启动 uvicorn（$baseUrl，只绑定 127.0.0.1）"

$uvicornArgs = @(
    "-m", "uvicorn", "app.main:app",
    "--host", "127.0.0.1",
    "--port", "$port"
)
$serverProc = Start-Process -FilePath $VenvPython -ArgumentList $uvicornArgs `
    -WorkingDirectory (Join-Path $Root "backend") -PassThru -NoNewWindow
$viteProc = $null
$runtimeWritten = $false

try {
    if (-not (Wait-Http -Url "$baseUrl/api/health" -TimeoutSeconds 45)) {
        if ($serverProc.HasExited) {
            Stop-Fatal "uvicorn 提前退出（退出码 $($serverProc.ExitCode)），看上面的报错。"
        }
        Stop-Fatal "45 秒内 /api/health 没起来，检查端口 $port 与日志。"
    }

    # runtime.json 只记「怎么连上它」；token 绝不落盘（每次启动重新生成，§13.7）
    $runtime = [ordered]@{
        url        = $baseUrl
        host       = "127.0.0.1"
        port       = $port
        pid        = $serverProc.Id
        dev        = [bool]$Dev
        started_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
    }
    $dataDir = Join-Path $Root "data"
    if (-not (Test-Path -LiteralPath $dataDir)) { New-Item -ItemType Directory -Path $dataDir | Out-Null }
    # 显式不带 BOM：PowerShell 5.1 的 `-Encoding utf8` 会写 BOM，Python 的 json 读不了
    $runtimeJson = $runtime | ConvertTo-Json
    [System.IO.File]::WriteAllText($RuntimeFile, $runtimeJson, (New-Object System.Text.UTF8Encoding($false)))
    $runtimeWritten = $true

    $openUrl = $baseUrl
    if ($Dev) {
        Write-Step "启动 Vite dev server（:5173，代理 /api 到 $baseUrl）"
        $viteProc = Start-Process -FilePath $npm -ArgumentList @("run", "dev", "--", "--port", "5173") `
            -WorkingDirectory $Frontend -PassThru -NoNewWindow
        if (Wait-Http -Url "http://127.0.0.1:5173/" -TimeoutSeconds 60) {
            $openUrl = "http://127.0.0.1:5173"
            Write-Ok "Vite 就绪"
        } else {
            Write-Warn "Vite 60 秒内没起来，先按生产地址打开；看 npm run dev 的报错"
        }
    }

    Write-Host ""
    Write-Host "  pgassistant 已启动" -ForegroundColor Green
    Write-Host "    站点      $openUrl"
    Write-Host "    接口文档  $baseUrl/docs"
    Write-Host "    令牌      在页面 <head> 的 <meta name=`"x-app-token`"> 里（不落盘，随重启失效）"
    Write-Host "    停止      Ctrl+C"
    Write-Host ""

    if (-not $NoBrowser) {
        Start-Process $openUrl | Out-Null
    }

    Wait-Process -Id $serverProc.Id
} finally {
    foreach ($proc in @($viteProc, $serverProc)) {
        if ($proc -and -not $proc.HasExited) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    # 正常退出时清掉 runtime.json；被强杀时它会留成「上一次启动」的记录
    if ($runtimeWritten -and (Test-Path -LiteralPath $RuntimeFile)) {
        Remove-Item -LiteralPath $RuntimeFile -Force -ErrorAction SilentlyContinue
    }
}
