param(
    [Parameter(Mandatory = $true)]
    [string]$Zip,

    [Parameter(Mandatory = $false)]
    [string]$KeyFile = "C:\Projects\Bonuska\secrets\bonuska-update-signing-key.txt"
)

$ErrorActionPreference = "Stop"

$ZipPath = (Resolve-Path -LiteralPath $Zip).Path
$KeyPath = (Resolve-Path -LiteralPath $KeyFile).Path

$KeyText = (Get-Content -LiteralPath $KeyPath -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($KeyText)) {
    throw "Signing key is empty: $KeyPath"
}

try {
    $KeyBytes = [Convert]::FromBase64String($KeyText)
}
catch {
    throw "Signing key is not valid Base64: $KeyPath"
}

if ($KeyBytes.Length -lt 32) {
    throw "Signing key is too short"
}

$ZipBytes = [IO.File]::ReadAllBytes($ZipPath)

$Sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $HashBytes = $Sha.ComputeHash($ZipBytes)
}
finally {
    $Sha.Dispose()
}

$HashHex = ([BitConverter]::ToString($HashBytes)).Replace("-", "").ToLowerInvariant()
$ShaPath = "$ZipPath.sha256"

if (Test-Path -LiteralPath $ShaPath -PathType Leaf) {
    $Expected = (Get-Content -LiteralPath $ShaPath -Raw).Trim().Split()[0].ToLowerInvariant()
    if ($Expected -ne $HashHex) {
        throw "Existing SHA mismatch: expected=$Expected actual=$HashHex"
    }
}
else {
    $Leaf = Split-Path -Leaf $ZipPath
    Set-Content -LiteralPath $ShaPath -Value "$HashHex  $Leaf" -NoNewline -Encoding ascii
}

$Hmac = New-Object System.Security.Cryptography.HMACSHA256
try {
    $Hmac.Key = $KeyBytes
    $SignatureBytes = $Hmac.ComputeHash($ZipBytes)
}
finally {
    $Hmac.Dispose()
}

$SignatureHex = ([BitConverter]::ToString($SignatureBytes)).Replace("-", "").ToLowerInvariant()
$SigPath = "$ZipPath.sig"
Set-Content -LiteralPath $SigPath -Value $SignatureHex -NoNewline -Encoding ascii

Write-Host "ZIP=$ZipPath"
Write-Host "SHA256=$HashHex"
Write-Host "SHA256_FILE=$ShaPath"
Write-Host "HMAC_SHA256=$SignatureHex"
Write-Host "SIGNATURE_FILE=$SigPath"
Write-Host "UPDATE_PACKAGE_SIGNING=PASS"
