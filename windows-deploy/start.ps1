# FinScope AI - 一键启动（数据库 + 后端 API + 后台 worker + 前端界面）
#
# 用法：
#   .\start.ps1                 生产模式，前端跑在 8080（使用已构建的 dist）
#   .\start.ps1 -Mode dev       开发模式，前端跑在 5173（带热更新）
#   .\start.ps1 -Rebuild        先重新构建前端再启动
#   .\start.ps1 -NoBrowser      启动后不自动打开浏览器
param(
    [ValidateSet('prod', 'dev')]
    [string]$Mode = 'prod',
    [switch]$Rebuild,
    [switch]$NoBrowser
)

. (Join-Path $PSScriptRoot '_common.ps1')

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Host ''
Write-Host '========================================' -ForegroundColor White
Write-Host '   FinScope AI  本地启动' -ForegroundColor White
Write-Host '========================================' -ForegroundColor White

# ---------- 0. 环境自检 ----------
if (-not (Test-Path $PythonExe)) {
    Write-Err "缺少后端虚拟环境：$PythonExe"
    Show-EnvironmentHints
    exit 1
}
if (-not (Test-Path $PgCtl)) {
    Write-Err "缺少 PostgreSQL 运行时：$PgCtl"
    Write-Info '数据库运行时目录应为 C:\finscope-runtime'
    exit 1
}
$NodeExe = Get-NodeExe
if (-not $NodeExe) {
    Write-Err '未找到 node.exe，请确认 Node.js 已安装并在 PATH 中。'
    exit 1
}

# ---------- 1. 数据库 ----------
Write-Head "1/4 数据库 PostgreSQL + pgvector（端口 $DbPort）"
if (Test-TcpPort -Port $DbPort) {
    Write-Ok '数据库已在运行，跳过启动'
} else {
    $pgLog = Join-Path $LogDir 'postgres.log'
    # 同样走分离式启动：postgres 会继承 pg_ctl 的标准句柄，
    # 若用 Start-Process -RedirectStandardOutput 启动 pg_ctl，
    # 数据库进程会一直攥着调用方的管道，让 start 脚本无法返回。
    Start-ServiceProcess -Name 'pgctl' -FilePath $PgCtl `
        -Arguments @('-D', $PgData, '-l', $pgLog, '-w', 'start') -WorkingDirectory $RuntimeHome | Out-Null
    if (Wait-ForPort -Port $DbPort -TimeoutSec 60) {
        Write-Ok "数据库已启动（数据目录 $PgData）"
    } else {
        Write-Err '数据库启动失败，日志尾部：'
        Get-Content $pgLog -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Info $_ }
        exit 1
    }
}

# ---------- 2. 数据库结构迁移 ----------
Write-Head '2/4 数据库结构迁移（alembic upgrade head）'
$mig = Invoke-Captured -FilePath $AlembicExe -Arguments @('upgrade', 'head') -WorkingDirectory $BackendDir -LogName 'alembic'
if ($mig.ExitCode -eq 0) {
    Write-Ok '数据库结构已是最新'
} else {
    Write-Err "迁移失败（退出码 $($mig.ExitCode)），日志：$($mig.Err)"
    Get-Content $mig.Err -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Info $_ }
    exit 1
}

# ---------- 3. 后端 API 与 worker ----------
Write-Head "3/4 后端 API（端口 $ApiPort）与后台 worker"
$apiRunning = @(Get-ProjectServiceProcess -Pattern $ApiProcessPattern -Names $PythonNames).Count -gt 0
if ($apiRunning) {
    Write-Ok 'API 已在运行'
} elseif (Test-TcpPort -Port $ApiPort) {
    Write-Ok "端口 $ApiPort 已被其他进程占用，沿用现有服务"
} else {
    Start-ServiceProcess -Name 'api' -FilePath $PythonExe `
        -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$ApiPort", '--no-access-log') `
        -WorkingDirectory $BackendDir | Out-Null
    if (Wait-ForHttp -Url "http://127.0.0.1:$ApiPort/api/v1/health/live" -TimeoutSec 90 -Label 'API 健康检查') {
        Write-Ok "API 已启动（日志 $(Join-Path $LogDir 'api.out.log')）"
    } else {
        Write-Err "API 启动失败，日志：$(Join-Path $LogDir 'api.err.log')"
        Get-Content (Join-Path $LogDir 'api.err.log') -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Info $_ }
        exit 1
    }
}

$workerRunning = @(Get-ProjectServiceProcess -Pattern $WorkerProcessPattern -Names $PythonNames).Count -gt 0
if ($workerRunning) {
    Write-Ok 'worker 已在运行'
} else {
    Start-ServiceProcess -Name 'worker' -FilePath $PythonExe `
        -Arguments @('-m', 'app.workers.main') -WorkingDirectory $BackendDir | Out-Null
    Write-Ok "worker 已启动（日志 $(Join-Path $LogDir 'worker.out.log')）"
}

if (Wait-ForHttp -Url "http://127.0.0.1:$ApiPort/api/v1/health/ready" -TimeoutSec 45 -Label '就绪检查（需要 worker 心跳）') {
    Write-Ok '数据库与 worker 心跳均正常'
} else {
    Write-Warn '就绪检查未通过，请运行 status.bat 并查看日志'
}

# ---------- 4. 前端界面 ----------
if ($Mode -eq 'prod') {
    Write-Head "4/4 前端界面（生产模式，端口 $WebPort）"
} else {
    Write-Head "4/4 前端界面（开发模式，端口 $DevPort）"
}

if (-not (Test-Path $ViteJs)) {
    Write-Err "缺少前端依赖：$ViteJs"
    Show-EnvironmentHints
    exit 1
}

if ($Mode -eq 'prod') {
    if ($Rebuild -or -not (Test-Path $DistIndex)) {
        Write-Info '正在构建前端（npm run build）...'
        $npm = Join-Path (Split-Path -Parent $NodeExe) 'npm.cmd'
        $build = Invoke-Captured -FilePath $npm -Arguments @('run', 'build') -WorkingDirectory $FrontendDir -LogName 'npm-build'
        if ($build.ExitCode -ne 0 -or -not (Test-Path $DistIndex)) {
            Write-Err "前端构建失败，日志：$($build.Out) / $($build.Err)"
            Get-Content $build.Err -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Info $_ }
            exit 1
        }
        Write-Ok '前端已构建'
    } else {
        Write-Ok '已存在构建产物 dist，跳过构建（需要重建请加 -Rebuild）'
    }

    $webRunning = @(Get-ProjectServiceProcess -Pattern $WebProcessPattern -Names $NodeNames).Count -gt 0
    if ($webRunning) {
        Write-Ok '前端服务已在运行'
    } elseif (Test-TcpPort -Port $WebPort) {
        Write-Ok "端口 $WebPort 已被其他进程占用，沿用现有服务"
    } else {
        Start-ServiceProcess -Name 'web' -FilePath $NodeExe `
            -Arguments @($ViteJs, 'preview', '--host', '127.0.0.1', '--port', "$WebPort", '--strictPort') `
            -WorkingDirectory $FrontendDir | Out-Null
        if (Wait-ForPort -Port $WebPort -TimeoutSec 60) {
            Write-Ok "前端服务已启动（日志 $(Join-Path $LogDir 'web.out.log')）"
        } else {
            Write-Err "前端启动失败，日志：$(Join-Path $LogDir 'web.err.log')"
            Get-Content (Join-Path $LogDir 'web.err.log') -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { Write-Info $_ }
            exit 1
        }
    }
    $webUrl = "http://127.0.0.1:$WebPort"
} else {
    $webRunning = @(Get-ProjectServiceProcess -Pattern $WebProcessPattern -Names $NodeNames).Count -gt 0
    if ($webRunning) {
        Write-Ok '开发服务器已在运行'
    } elseif (Test-TcpPort -Port $DevPort) {
        Write-Ok "端口 $DevPort 已被其他进程占用，沿用现有服务"
    } else {
        Start-ServiceProcess -Name 'web' -FilePath $NodeExe `
            -Arguments @($ViteJs, '--host', '127.0.0.1', '--port', "$DevPort", '--strictPort') `
            -WorkingDirectory $FrontendDir | Out-Null
        if (Wait-ForPort -Port $DevPort -TimeoutSec 60) {
            Write-Ok "开发服务器已启动（日志 $(Join-Path $LogDir 'web.out.log')）"
        } else {
            Write-Err "前端启动失败，日志：$(Join-Path $LogDir 'web.err.log')"
            exit 1
        }
    }
    $webUrl = "http://127.0.0.1:$DevPort"
}

# ---------- 完成 ----------
Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host '   FinScope AI 已就绪' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host ''
Write-Host "   前端界面   $webUrl" -ForegroundColor White
Write-Host "   接口文档   http://127.0.0.1:$ApiPort/docs" -ForegroundColor White
Write-Host "   健康检查   http://127.0.0.1:$ApiPort/api/v1/health/ready" -ForegroundColor White
Write-Host ''
Write-Host "   登录账号   $AppUser" -ForegroundColor White
Write-Host "   登录密码   $AppPassword" -ForegroundColor White
Write-Host ''
Write-Host "   日志目录   $LogDir" -ForegroundColor Gray
Write-Host '   停止服务   双击 stop.bat' -ForegroundColor Gray
Write-Host ''

if (-not $NoBrowser) { Start-Process $webUrl }
