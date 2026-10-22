param([Parameter(Mandatory=$true)][string[]]$Files)
$ErrorActionPreference = "Stop"
if (-not $env:WINDOWS_SIGNING_CERTIFICATE -or -not $env:WINDOWS_SIGNING_PASSWORD) {
    throw "Tagged Windows releases require WINDOWS_SIGNING_CERTIFICATE (base64 PFX) and WINDOWS_SIGNING_PASSWORD in the release environment."
}
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Sort-Object FullName | Select-Object -Last 1
if (-not $signtool) { throw "Windows SDK signtool.exe was not found" }
$certificate = Join-Path $env:RUNNER_TEMP "dpdfnet-release-signing.pfx"
try {
    [IO.File]::WriteAllBytes($certificate, [Convert]::FromBase64String($env:WINDOWS_SIGNING_CERTIFICATE))
    foreach ($file in $Files) {
        & $signtool.FullName sign /fd SHA256 /td SHA256 /tr "http://timestamp.digicert.com" /f $certificate /p $env:WINDOWS_SIGNING_PASSWORD $file
        if ($LASTEXITCODE -ne 0) { throw "Signing failed: $file" }
        & $signtool.FullName verify /pa /all $file
        if ($LASTEXITCODE -ne 0) { throw "Signature verification failed: $file" }
    }
} finally {
    if (Test-Path $certificate) { Remove-Item -LiteralPath $certificate -Force }
}
