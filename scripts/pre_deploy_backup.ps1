param(
    [string]$ApplicationRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path,
    [string]$OutputRoot = (Join-Path (Split-Path $ApplicationRoot -Parent) 'lv360-predeploy-backups')
)

$ErrorActionPreference = 'Stop'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$target = Join-Path $OutputRoot $stamp
$stage = Join-Path $env:TEMP "lv360-predeploy-$stamp"
New-Item -ItemType Directory -Force -Path $target, $stage | Out-Null

$exclude = @('.git', '.venv', '__pycache__', '.pytest_cache', 'tests\.runtime', 'backups', 'lv360-predeploy-backups')
Get-ChildItem -LiteralPath $ApplicationRoot -Force | ForEach-Object {
    if ($exclude -contains $_.Name) { return }
    Copy-Item -LiteralPath $_.FullName -Destination $stage -Recurse -Force
}
Get-ChildItem -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\__pycache__\\|\\.pytest_cache\\|\\tests\\.runtime\\|\\data\\portal\.db-(shm|wal)$' } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath (Join-Path $target 'application-source.zip') -CompressionLevel Optimal
Remove-Item -LiteralPath $stage -Recurse -Force

$dbStatus = 'not-captured'
$dbUrl = if ($env:LV360_PORTAL_MIGRATION_DATABASE_URL) { $env:LV360_PORTAL_MIGRATION_DATABASE_URL } else { $env:LV360_PORTAL_DATABASE_URL }
if ($dbUrl -and $dbUrl.StartsWith('postgresql') -and (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    & pg_dump $dbUrl -Fc -f (Join-Path $target 'portal.dump')
    $dbStatus = 'postgresql-pg-dump'
} elseif ((Get-Command docker -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $ApplicationRoot 'docker-compose.yml'))) {
    $dbContainer = (& docker compose -f (Join-Path $ApplicationRoot 'docker-compose.yml') ps -q db 2>$null)
    if ($dbContainer) {
        $dbUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { 'lv360' }
        $dbName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { 'lv360_portal' }
        $dumpPath = Join-Path $target 'portal.dump'
        cmd /c "docker compose -f `"$ApplicationRoot\docker-compose.yml`" exec -T db pg_dump -U $dbUser -d $dbName -Fc > `"$dumpPath`""
        $dbStatus = 'postgresql-docker-pg-dump'
    }
}
if ($dbStatus -eq 'not-captured') {
    $sqlite = if ($env:LV360_PORTAL_SQLITE_PATH) { $env:LV360_PORTAL_SQLITE_PATH } else { Join-Path $ApplicationRoot 'data\portal.db' }
    if (Test-Path $sqlite) {
        Copy-Item $sqlite (Join-Path $target 'portal.db') -Force
        $dbStatus = 'sqlite-file-copy'
    }
}

$objectStatus = 'not-captured'
$localStorage = if ($env:LV360_PORTAL_LOCAL_STORAGE_PATH) { $env:LV360_PORTAL_LOCAL_STORAGE_PATH } else { Join-Path $ApplicationRoot 'data\private' }
if (Test-Path $localStorage) {
    Compress-Archive -Path (Join-Path $localStorage '*') -DestinationPath (Join-Path $target 'object-storage.zip') -CompressionLevel Optimal
    $objectStatus = 'local-storage-archive'
}

$manifest = [ordered]@{
    created_at_utc = $stamp
    application_root = $ApplicationRoot
    database_backup = $dbStatus
    object_storage_backup = $objectStatus
    purpose = 'Pre-deployment immutable backup of live application source, database and private objects'
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $target 'manifest.json') -Encoding UTF8
Get-ChildItem -LiteralPath $target -File | Where-Object { $_.Name -ne 'SHA256SUMS' } | ForEach-Object {
    $hash = Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256
    "$($hash.Hash.ToLower())  $($_.Name)"
} | Set-Content -LiteralPath (Join-Path $target 'SHA256SUMS') -Encoding ASCII
Write-Output $target
