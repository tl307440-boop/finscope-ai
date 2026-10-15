# FinScope AI - Windows 本地部署 公共配置与工具函数
# 该文件被 start.ps1 / stop.ps1 / status.ps1 点源加载，不单独运行。

$DeployRoot  = $PSScriptRoot
$RepoRoot    = Split-Path -Parent $PSScriptRoot
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'

$PythonExe   = Join-Path $BackendDir '.venv\Scripts\python.exe'
$AlembicExe  = Join-Path $BackendDir '.venv\Scripts\alembic.exe'
$ViteJs      = Join-Path $FrontendDir 'node_modules\vite\bin\vite.js'
$DistIndex   = Join-Path $FrontendDir 'dist\index.html'

# 数据库运行时固定放在纯 ASCII 路径：
# PostgreSQL 16 在 Windows 上以 GBK 解析路径，含中文的目录会导致 initdb 失败。
$RuntimeHome = 'C:\finscope-runtime'
$PgHome      = Join-Path $RuntimeHome 'pgsql'
$PgData      = Join-Path $RuntimeHome 'pgdata'
$PgCtl       = Join-Path $PgHome 'bin\pg_ctl.exe'
$LogDir      = Join-Path $RuntimeHome 'logs'

$DbPort  = 5432
$ApiPort = 8000
$WebPort = 8080
$DevPort = 5173

$AppUser     = 'admin'
$AppPassword = 'FsScope!2026'

# 用命令行特征识别本项目的服务进程（比 PID 文件可靠：强杀之后 PID 文件会失效）
$ApiProcessPattern    = [regex]::Escape($PythonExe) + '.*uvicorn'
$WorkerProcessPattern = [regex]::Escape($PythonExe) + '.*app\.workers'
$WebProcessPattern    = [regex]::Escape($ViteJs)
$PythonNames = @('python.exe', 'pythonw.exe')
$NodeNames   = @('node.exe')

function Write-Head([string]$Message) { Write-Host ''; Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message)   { Write-Host "    [OK] $Message" -ForegroundColor Green }
function Write-Info([string]$Message) { Write-Host "    $Message" -ForegroundColor Gray }
function Write-Warn([string]$Message) { Write-Host "    [!] $Message" -ForegroundColor Yellow }
function Write-Err([string]$Message)  { Write-Host "    [X] $Message" -ForegroundColor Red }

# 本机端口是否已被监听。所有服务统一绑定 127.0.0.1，因此只探测 IPv4 回环。
function Test-TcpPort {
    param([int]$Port, [string]$Target = '127.0.0.1')
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($Target, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(800)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-NodeExe {
    $cmd = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @('D:\Program Files\nodejs\node.exe', 'C:\Program Files\nodejs\node.exe')) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-HttpOk {
    param([string]$Url, [int]$TimeoutSec = 5)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Wait-ForHttp {
    param([string]$Url, [int]$TimeoutSec = 60, [string]$Label = $Url)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-HttpOk -Url $Url -TimeoutSec 3) { return $true }
        Start-Sleep -Milliseconds 800
    }
    Write-Err "等待超时：$Label"
    return $false
}

function Wait-ForPort {
    param([int]$Port, [int]$TimeoutSec = 60)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -Port $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

# 按命令行特征查找本项目启动的服务进程
function Get-ProjectServiceProcess {
    param([string]$Pattern, [string[]]$Names)
    $found = @()
    foreach ($item in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if ($Names -notcontains $item.Name) { continue }
        if (-not $item.CommandLine) { continue }
        if ($item.CommandLine -match $Pattern) {
            $found += [pscustomobject]@{
                Id          = [int]$item.ProcessId
                Name        = $item.Name
                CommandLine = $item.CommandLine
            }
        }
    }
    return $found
}

# 分离式启动（要点，缺一不可）：
#   1) 交给 cmd.exe 做 stdout/stderr 重定向到日志文件；
#   2) 通过 ShellExecute 启动 cmd.exe（UseShellExecute = $true），这样子进程
#      不会继承本进程的标准句柄。若用 Start-Process -RedirectStandardOutput，
#      被启动的常驻进程会一直攥着调用方的管道，调用方在它退出前一直挂住；
#   3) 窗口设为隐藏，启动完成后即后台常驻，关掉命令行窗口也不受影响。
function Start-ServiceProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $out = Join-Path $LogDir "$Name.out.log"
    $err = Join-Path $LogDir "$Name.err.log"
    $quoted = ($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $line = '/c cd /d "' + $WorkingDirectory + '" && "' + $FilePath + '" ' + $quoted + ' 1> "' + $out + '" 2> "' + $err + '"'

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $env:ComSpec
    $psi.Arguments = $line
    $psi.UseShellExecute = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    return [System.Diagnostics.Process]::Start($psi)
}

function Stop-ServiceProcess {
    param(
        [string]$Label,
        [string]$Pattern,
        [string[]]$Names,
        [int]$Port = 0
    )
    $stopped = $false
    foreach ($item in @(Get-ProjectServiceProcess -Pattern $Pattern -Names $Names)) {
        Stop-Process -Id $item.Id -Force -ErrorAction SilentlyContinue
        Write-Ok "已停止 $Label (PID $($item.Id))"
        $stopped = $true
    }
    if (-not $stopped -and $Port -gt 0 -and (Test-TcpPort -Port $Port)) {
        foreach ($conn in @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
            Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
            Write-Ok "已停止占用端口 $Port 的进程：$Label (PID $($conn.OwningProcess))"
            $stopped = $true
        }
    }
    if (-not $stopped) { Write-Info "$Label 未在运行" }
    return $stopped
}

# 前台执行一条命令并把输出落到日志文件，返回退出码
# （避免 PowerShell 把子进程的 stderr 当成异常抛出）
function Invoke-Captured {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$LogName
    )
    $out = Join-Path $LogDir "$LogName.out.log"
    $err = Join-Path $LogDir "$LogName.err.log"
    $proc = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -Wait -PassThru
    return @{ ExitCode = $proc.ExitCode; Out = $out; Err = $err }
}

function Show-EnvironmentHints {
    Write-Host ''
    Write-Host '   运行环境不完整。请在项目根目录执行一次初始化：' -ForegroundColor Yellow
    Write-Host '     cd backend' -ForegroundColor Gray
    Write-Host '     uv sync --extra dev' -ForegroundColor Gray
    Write-Host '     cd ..\frontend' -ForegroundColor Gray
    Write-Host '     npm ci' -ForegroundColor Gray
}
