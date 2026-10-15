# FinScope AI - 查看各个服务的运行状态
. (Join-Path $PSScriptRoot '_common.ps1')

# 按显示宽度补齐（中文占 2 列），让控制台表格对齐
function Format-Cell {
    param([string]$Text, [int]$Width)
    $display = 0
    foreach ($ch in $Text.ToCharArray()) {
        if ([int]$ch -gt 0x2E80) { $display += 2 } else { $display += 1 }
    }
    $pad = $Width - $display
    if ($pad -lt 1) { $pad = 1 }
    return $Text + (' ' * $pad)
}

Write-Host ''
Write-Host '========================================' -ForegroundColor White
Write-Host '   FinScope AI  运行状态' -ForegroundColor White
Write-Host '========================================' -ForegroundColor White
Write-Host ''

function Show-ServiceRow {
    param([string]$Label, [string]$Pattern, [string[]]$Names, [int]$Port)
    $prefix = '   ' + (Format-Cell $Label 12)
    $procs = @(Get-ProjectServiceProcess -Pattern $Pattern -Names $Names)
    if ($procs.Count -gt 0) {
        $ids = ($procs | ForEach-Object { $_.Id }) -join ', '
        Write-Host ($prefix + '运行中    PID ' + $ids) -ForegroundColor Green
    } elseif ($Port -gt 0 -and (Test-TcpPort -Port $Port)) {
        Write-Host ($prefix + '运行中    （非本脚本启动，端口 ' + $Port + '）') -ForegroundColor Green
    } else {
        Write-Host ($prefix + '已停止') -ForegroundColor DarkGray
    }
}

$dbPrefix = '   ' + (Format-Cell '数据库' 12)
if (Test-TcpPort -Port $DbPort) {
    Write-Host ($dbPrefix + '运行中    端口 ' + $DbPort) -ForegroundColor Green
} else {
    Write-Host ($dbPrefix + '已停止') -ForegroundColor DarkGray
}

Show-ServiceRow -Label '后端 API' -Pattern $ApiProcessPattern -Names $PythonNames -Port $ApiPort
Show-ServiceRow -Label 'worker' -Pattern $WorkerProcessPattern -Names $PythonNames -Port 0
Show-ServiceRow -Label '前端界面' -Pattern $WebProcessPattern -Names $NodeNames -Port $WebPort
if ($DevPort -ne $WebPort -and (Test-TcpPort -Port $DevPort)) {
    Write-Host ('   ' + (Format-Cell '前端 dev' 12) + '运行中    端口 ' + $DevPort + '（开发模式）') -ForegroundColor Green
}

Write-Host ''
if (Test-TcpPort -Port $ApiPort) {
    Write-Host '   就绪检查 /api/v1/health/ready：' -ForegroundColor Cyan
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/api/v1/health/ready" -UseBasicParsing -TimeoutSec 15
        Write-Host "   HTTP $($response.StatusCode)" -ForegroundColor Green
        Write-Host "   $($response.Content)" -ForegroundColor Gray
    } catch {
        $status = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        Write-Host "   HTTP $status —— 未就绪（通常是 worker 未运行）" -ForegroundColor Yellow
        if ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            Write-Host "   $($reader.ReadToEnd())" -ForegroundColor Gray
        }
    }
}

if (Test-TcpPort -Port $WebPort) {
    Write-Host ''
    Write-Host "   前端地址   http://127.0.0.1:$WebPort" -ForegroundColor Cyan
} elseif (Test-TcpPort -Port $DevPort) {
    Write-Host ''
    Write-Host "   前端地址   http://127.0.0.1:$DevPort" -ForegroundColor Cyan
}

Write-Host ''
Write-Host "   日志目录   $LogDir" -ForegroundColor Gray
Write-Host ''
