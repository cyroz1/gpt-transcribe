$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPath = Join-Path $projectRoot 'work\.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv $venvPath
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $projectRoot 'requirements.txt') pyinstaller

& $venvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name GPTTranscribe `
    --collect-all sounddevice `
    (Join-Path $projectRoot 'gpt_transcribe.py')

$outputDirectory = Join-Path $projectRoot 'outputs'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$outputExecutable = Join-Path $outputDirectory 'GPTTranscribe.exe'
if (-not (Test-Path -LiteralPath $outputExecutable)) {
    New-Item -ItemType File -Path $outputDirectory -Name 'GPTTranscribe.exe' | Out-Null
}
Copy-Item -LiteralPath (Join-Path $projectRoot 'dist\GPTTranscribe.exe') -Destination $outputExecutable -Force
Write-Output "Built $outputExecutable"
