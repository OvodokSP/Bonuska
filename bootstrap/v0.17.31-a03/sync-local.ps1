param(
    [Parameter(Mandatory = $false)]
    [string]$Root = 'C:\\Projects\\Bonuska\\current'
)

$ErrorActionPreference = 'Stop'
$BaseFingerprint = '43139dcdfd543197961369c2d3163a38ae763a4be8b642d186779b4bd8d36b41'
$Marker = 'BONUSKA_V01731_A03_HEADERLESS_CONTINUATION_BEGIN'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppendFile = Join-Path $ScriptDir 'pdf_invoice_v01731_a03_append.py'

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    $FallbackRoot = 'C:\\Projects\\Bonuska'
    if (Test-Path -LiteralPath (Join-Path $FallbackRoot 'backend\\bonuska\\parsers\\pdf_invoice.py')) {
        $Root = $FallbackRoot
    }
    else {
        throw "Bonuska root not found: $Root"
    }
}

$Parser = Join-Path $Root 'backend\\bonuska\\parsers\\pdf_invoice.py'
$FingerprintScript = Join-Path $Root 'backend\\scripts\\code_fingerprint.py'

foreach ($Path in @($Parser, $FingerprintScript, $AppendFile)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file missing: $Path"
    }
}

$CurrentText = [IO.File]::ReadAllText($Parser)
if ($CurrentText.Contains($Marker)) {
    Write-Host 'LOCAL_PARSER_PATCH=ALREADY_PRESENT'
    python $FingerprintScript --root $Root --fingerprint-only
    exit 0
}

$ActualBase = (& python $FingerprintScript --root $Root --fingerprint-only).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not calculate local source fingerprint"
}

Write-Host "BASE_FINGERPRINT_EXPECTED=$BaseFingerprint"
Write-Host "BASE_FINGERPRINT_ACTUAL=$ActualBase"

if ($ActualBase -ne $BaseFingerprint) {
    throw "Local source is not exact v0.17.31 a02"
}

$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$BackupDir = Join-Path $Root "local_backups\\before_v0.17.31_a03_$Stamp"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
Copy-Item -LiteralPath $Parser -Destination (Join-Path $BackupDir 'pdf_invoice.py') -Force

$AppendText = [IO.File]::ReadAllText($AppendFile)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::AppendAllText($Parser, [Environment]::NewLine + $AppendText, $Utf8NoBom)

$UpdatedText = [IO.File]::ReadAllText($Parser)
if (-not $UpdatedText.Contains($Marker)) {
    throw "Parser marker was not written"
}

& python -m py_compile $Parser
if ($LASTEXITCODE -ne 0) {
    Copy-Item -LiteralPath (Join-Path $BackupDir 'pdf_invoice.py') -Destination $Parser -Force
    throw "Parser compile failed; original file restored"
}

$TargetFingerprint = (& python $FingerprintScript --root $Root --fingerprint-only).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not calculate target fingerprint"
}

Write-Host "LOCAL_SOURCE_ROOT=$Root"
Write-Host "LOCAL_BACKUP=$BackupDir"
Write-Host "TARGET_FINGERPRINT=$TargetFingerprint"
Write-Host "LOCAL_PARSER_PATCH=PASS"
