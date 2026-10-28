$ErrorActionPreference = 'Stop'
$compiler = Join-Path (Get-Item 'Env:ProgramFiles(x86)').Value 'Inno Setup 6\ISCC.exe'
$probeDir = Join-Path $env:RUNNER_TEMP 'vbcable-inno-probe'
New-Item -ItemType Directory -Force -Path $probeDir | Out-Null
& $compiler "/O$probeDir" packaging/vbcable-registry-probe.iss
if ($LASTEXITCODE -ne 0) { throw 'Could not compile the Inno VB-CABLE registry probe' }
$exe = Join-Path $probeDir 'vbcable-registry-probe.exe'
$service = 'HKLM:\SYSTEM\CurrentControlSet\Services\VBAudioVACMME'
if (Test-Path $service) { throw 'VB-CABLE service already exists on this test runner' }

function Assert-Probe([string]$Expected) {
    $env:VBCABLE_PROBE_RESULT = Join-Path $probeDir 'result.txt'
    Remove-Item $env:VBCABLE_PROBE_RESULT -ErrorAction SilentlyContinue
    $process = Start-Process -FilePath $exe -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -Wait -PassThru
    if ($process.ExitCode -eq 0) { throw 'Registry probe unexpectedly proceeded with installation' }
    if (-not (Test-Path $env:VBCABLE_PROBE_RESULT)) { throw 'Registry probe produced no result' }
    $actual = (Get-Content $env:VBCABLE_PROBE_RESULT -Raw).Trim()
    if ($actual -ne $Expected) { throw "Inno detected '$actual', expected '$Expected'" }
}

try {
    Assert-Probe 'absent'
    # The actual HKLM service path is used, not a mocked Python winreg result.
    New-Item -Path $service -Force | Out-Null
    Assert-Probe 'present'
} finally {
    Remove-Item -Path $service -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item Env:VBCABLE_PROBE_RESULT -ErrorAction SilentlyContinue
}
