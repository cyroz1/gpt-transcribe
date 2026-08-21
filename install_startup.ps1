$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$executable = Join-Path $projectRoot 'outputs\GPTTranscribe.exe'
$startupDirectory = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDirectory 'GPT Transcribe.lnk'

if ($args -contains '-Remove') {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath
    }
    Write-Output 'Removed GPT Transcribe from Windows startup.'
    exit 0
}

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Build the app first with .\build.ps1"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $executable
$shortcut.WorkingDirectory = Split-Path -Parent $executable
$shortcut.Description = 'Start GPT Transcribe'
$shortcut.Save()
Write-Output 'Added GPT Transcribe to Windows startup.'
