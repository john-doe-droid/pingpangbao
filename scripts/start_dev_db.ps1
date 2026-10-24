$ErrorActionPreference = "Stop"

$pgHome = if ($env:PINGPONG_PG_HOME) { $env:PINGPONG_PG_HOME } else { "E:\pgsql" }
$pgCtl = Join-Path $pgHome "bin\pg_ctl.exe"
$dataDir = Join-Path $pgHome "data"
$logFile = Join-Path $pgHome "postgres.log"

if (-not (Test-Path -LiteralPath $pgCtl)) {
    throw "找不到便携版 PostgreSQL：$pgCtl。可设置 PINGPONG_PG_HOME 指向实际目录。"
}
if (-not (Test-Path -LiteralPath $dataDir)) {
    throw "PostgreSQL 数据目录不存在：$dataDir"
}

& $pgCtl -D $dataDir status *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "PostgreSQL 已在运行。"
    exit 0
}

& $pgCtl -D $dataDir -l $logFile -o "-c listen_addresses=127.0.0.1 -p 5432" start
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL 启动失败，请查看 $logFile"
}

Write-Host "PostgreSQL 已启动：127.0.0.1:5432"
