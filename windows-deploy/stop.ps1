# FinScope AI - 停止全部服务
#
# 用法：
#   .\stop.ps1                 停止前端、API、worker 和数据库
#   .\stop.ps1 -KeepDatabase   保留数据库继续运行（下次启动更快）
param(
    [switch]$KeepDatabase
)

. (Join-Path $PSScriptRoot '_common.ps1')

Write-Host ''
Write-Host '========================================' -ForegroundColor White
Write-Host '   FinScope AI  停止服务' -ForegroundColor White
Write-Host '========================================' -ForegroundColor White

Write-Head '前端界面（生产 8080 / 开发 5173）'
Stop-ServiceProcess -Label '前端服务' -Pattern $WebProcessPattern -Names $NodeNames -Port $WebPort | Out-Null
if ($DevPort -ne $WebPort -and (Test-TcpPort -Port $DevPort)) {
    Stop-ServiceProcess -Label '开发服务器' -Pattern $WebProcessPattern -Names $NodeNames -Port $DevPort | Out-Null
}

Write-Head "后端 API（端口 $ApiPort）"
Stop-ServiceProcess -Label 'API' -Pattern $ApiProcessPattern -Names $PythonNames -Port $ApiPort | Out-Null

Write-Head '后台 worker'
Stop-ServiceProcess -Label 'worker' -Pattern $WorkerProcessPattern -Names $PythonNames | Out-Null

if ($KeepDatabase) {
    Write-Head '数据库'
    Write-Info '按参数要求保留数据库，未停止（下次启动会更快）'
} else {
    Write-Head "数据库（端口 $DbPort）"
    if (-not (Test-TcpPort -Port $DbPort)) {
        Write-Info '数据库未在运行'
    } elseif (-not (Test-Path $PgCtl)) {
        Stop-ServiceProcess -Label '数据库' -Pattern 'this-will-never-match' -Names $PythonNames -Port $DbPort | Out-Null
    } else {
        Start-ServiceProcess -Name 'pgctl-stop' -FilePath $PgCtl `
            -Arguments @('-D', $PgData, '-m', 'fast', '-w', 'stop') -WorkingDirectory $RuntimeHome | Out-Null
        # 等端口真正关闭（最多 30 秒）
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline -and (Test-TcpPort -Port $DbPort)) { Start-Sleep -Milliseconds 500 }
        if (Test-TcpPort -Port $DbPort) {
            Stop-ServiceProcess -Label '数据库' -Pattern 'this-will-never-match' -Names $PythonNames -Port $DbPort | Out-Null
        } else {
            Write-Ok '数据库已停止（数据保留在 C:\finscope-runtime\pgdata）'
        }
    }
}

Write-Host ''
Write-Host '   全部服务已停止。' -ForegroundColor Green
Write-Host ''
