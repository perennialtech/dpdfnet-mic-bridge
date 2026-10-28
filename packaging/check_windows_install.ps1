param([Parameter(Mandatory=$true)][string]$Installer)
$ErrorActionPreference = "Stop"
$directory = Join-Path $env:RUNNER_TEMP "Bridge install test with spaces"
foreach ($iteration in 1..2) {
    $taskChoices = Join-Path $env:RUNNER_TEMP "bridge-tasks-$iteration.ini"
    Remove-Item $taskChoices -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $Installer -ArgumentList @("/SAVEINF=`"$taskChoices`"", '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$directory`"") -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Installer/upgrade failed: $($process.ExitCode)" }
    if (-not (Test-Path $taskChoices)) { throw "Installer did not save selected tasks for run $iteration" }
    $choices = Get-Content $taskChoices -Raw
    if ($choices -match '(?m)^Tasks=([^\r\n]*)') {
        if ($Matches[1] -match 'installvbcable') { throw "VB-CABLE was selected by default on run $iteration" }
    } else {
        throw "Installer did not record tasks for run $iteration"
    }
    $vendor = Join-Path $directory 'vendor\vbcable'
    foreach ($name in @('VBCABLE_Setup_x64.exe', 'vbMmeCable64_win10.inf', 'vbaudio_cable64_win10.cat', 'readme.txt')) {
        if (-not (Test-Path (Join-Path $vendor $name))) { throw "Bundled VB-CABLE is missing $name" }
    }
    $expected = (Get-Content packaging/vbcable.json -Raw | ConvertFrom-Json).setup_x64_sha256
    $actual = (Get-FileHash (Join-Path $vendor 'VBCABLE_Setup_x64.exe') -Algorithm SHA256).Hash
    if ($actual -ne $expected) { throw 'Bundled VB-CABLE setup hash mismatch' }
    & "$directory\dpdfnet-mic-bridge-cli.exe" --help
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI startup failed" }
    & "$directory\dpdfnet-mic-bridge-cli.exe" _desktop-ui-test
    if ($LASTEXITCODE -ne 0) { throw "Installed desktop smoke test failed" }
}
$process = Start-Process -FilePath "$directory\unins000.exe" -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
if ($process.ExitCode -ne 0) { throw "Uninstall failed: $($process.ExitCode)" }
if (Test-Path $directory) { throw 'Installer left application files behind' }
