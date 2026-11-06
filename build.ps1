$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot 'work\.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv $venvPath
}

# Pin pip itself so the tool that installs the hashed requirements below
# is also bound to a reviewed version. (The self-upgrade is version-pinned
# only: pip cannot hash-verify its own bootstrap upgrade.)
& $venvPython -m pip install --upgrade "pip==26.3"
# requirements.txt pins every runtime and build dependency (including
# PyInstaller) to an exact version with a SHA-256 hash; pip verifies the
# hashes automatically because they are present in the file.
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt')

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name GPTTranscribe `
    --collect-all sounddevice `
    --hidden-import websocket `
    (Join-Path $projectRoot 'gpt_transcribe.py')

$outputDirectory = Join-Path $projectRoot 'outputs'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$outputExecutable = Join-Path $outputDirectory 'GPTTranscribe.exe'
if (-not (Test-Path -LiteralPath $outputExecutable)) {
    New-Item -ItemType File -Path $outputDirectory -Name 'GPTTranscribe.exe' | Out-Null
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'dist\GPTTranscribe.exe') -Destination $outputExecutable -Force
Write-Output "Built $outputExecutable"
