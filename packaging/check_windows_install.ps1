param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = "Stop"
$directory = Join-Path $env:RUNNER_TEMP "Bridge install test with spaces"
foreach ($iteration in 1..2) {
    $process = Start-Process -FilePath $Installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$directory`"") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer/upgrade failed: $($process.ExitCode)" }
    & "$directory\dpdfnet-mic-bridge-cli.exe" --help
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI startup failed" }
    & "$directory\dpdfnet-mic-bridge-cli.exe" _desktop-ui-test
    if ($LASTEXITCODE -ne 0) { throw "Installed desktop smoke test failed" }
}
$process = Start-Process -FilePath "$directory\unins000.exe" -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Uninstall failed: $($process.ExitCode)" }
