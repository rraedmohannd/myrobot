# Runs the firmware's off-target checks inside Docker (the host needs no compiler, no PlatformIO):
#   1. pio test -e native            unit tests + Python<->C++ protocol cross-test
#   2. pio run  -e sim-check         whole application with stub drivers, then runs it (SIMULATED)
# Usage:  powershell -ExecutionPolicy Bypass -File firmware\tools\test.ps1  ["other command"]
param([string]$Command = "pio test -e native && pio run -e sim-check -t exec")

$ErrorActionPreference = "Stop"
$toolsDir = $PSScriptRoot
$repoDir = (Resolve-Path (Join-Path $toolsDir "..\..")).Path
$image = "scr-firmware-test"

docker build -q -t $image $toolsDir | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker run --rm `
  -v "${repoDir}:/work" `
  -v "scr-pio-cache:/root/.platformio" `
  -w /work/firmware `
  $image bash -c $Command
exit $LASTEXITCODE
