# 为已入库的证券补齐 A 股日线（真实上游 AKShare，不造假数据）
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File sync_universe.ps1 [-Start 2026-01-01] [-End 2026-10-15]
#
# 上游（东方财富）经本机系统代理转发，偶尔会掐断连接，因此单只标的带重试。
param(
    [string]$Start = '2026-01-01',
    [string]$End   = '2026-10-15',
    [string]$Api   = 'http://127.0.0.1:8000',
    [int]$Attempts = 4
)

$ErrorActionPreference = 'Stop'
$OutputEncoding = [System.Text.Encoding]::UTF8

$symbols = @(
    '000001.SZ','000333.SZ','000568.SZ','000858.SZ','002415.SZ','002475.SZ','002594.SZ','300059.SZ','300750.SZ',
    '600030.SH','600036.SH','600276.SH','600519.SH','600900.SH','601012.SH','601166.SH','601318.SH','601888.SH','601899.SH','603259.SH',
    '000001.SH','000300.SH','399001.SZ','399006.SZ'
)

function Get-Token {
    $body = '{"username":"admin","password":"FsScope!2026"}'
    $login = Invoke-RestMethod -Uri "$Api/api/v1/auth/login" -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 30
    return $login.data.access_token
}

$token = Get-Token
Write-Host "登录成功，开始同步 $($symbols.Count) 个标的 ($Start ~ $End)"

$ok = 0; $fail = 0; $index = 0; $failedSymbols = @()
foreach ($symbol in $symbols) {
    $index++
    $payload = @{
        display_symbol    = $symbol
        observation_start = $Start
        observation_end   = $End
        adjustment        = 'none'
    } | ConvertTo-Json -Compress

    $done = $false
    for ($attempt = 1; $attempt -le $Attempts -and -not $done; $attempt++) {
        try {
            $r = Invoke-RestMethod -Uri "$Api/api/v1/admin/akshare-syncs" -Method Post `
                -Headers @{ Authorization = "Bearer $token" } -ContentType 'application/json' `
                -Body $payload -TimeoutSec 180
            $d = $r.data
            Write-Host ("[{0,2}/{1}] {2,-11} 写入 {3,4} 行  拒绝 {4}  重复跳过 {5}" -f $index, $symbols.Count, $symbol, $d.rows_written, $d.rows_rejected, $d.duplicates_skipped)
            $ok++
            $done = $true
        } catch {
            $msg = $_.Exception.Message
            if ($_.Exception.Response) {
                try { $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream()); $msg = $sr.ReadToEnd() } catch { }
            }
            if ($msg -match 'UNAUTHORIZED|401') { $token = Get-Token; Write-Host '  令牌已刷新' }
            if ($attempt -eq $Attempts) {
                Write-Host ("[{0,2}/{1}] {2,-11} 失败({3} 次): {4}" -f $index, $symbols.Count, $symbol, $Attempts, $msg)
                $fail++
                $failedSymbols += $symbol
            } else {
                Start-Sleep -Milliseconds (500 * $attempt)
            }
        }
    }
}

Write-Host ""
Write-Host "同步结束: 成功 $ok / 失败 $fail"
if ($failedSymbols.Count -gt 0) {
    Write-Host ("失败清单: " + ($failedSymbols -join ', '))
}
