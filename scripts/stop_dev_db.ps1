$ErrorActionPreference = "Stop"

$pgHome = if ($env:PINGPONG_PG_HOME) { $env:PINGPONG_PG_HOME } else { "E:\pgsql" }
$pgCtl = Join-Path $pgHome "bin\pg_ctl.exe"
$dataDir = Join-Path $pgHome "data"

if (-not (Test-Path -LiteralPath $pgCtl)) {
    throw "找不到便携版 PostgreSQL：$pgCtl"
}

& $pgCtl -D $dataDir status *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PostgreSQL 当前未运行。"
    exit 0
}

& $pgCtl -D $dataDir -m fast -w stop
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL 停止失败。"
}

Write-Host "PostgreSQL 已停止。"
