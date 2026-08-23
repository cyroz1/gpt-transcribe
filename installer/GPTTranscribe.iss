#ifndef AppVersion
  #define AppVersion "0.3.2"
#endif

#define AppName "GPT Transcribe"
#define AppPublisher "GPT Transcribe"
#define AppExeName "GPTTranscribe.exe"

[Setup]
AppId={{B5E4A8D6-1E6B-4D3D-9B4F-7D2D7A9B1E42}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/cyroz1/gpt-transcribe
DefaultDirName={autopf}\GPT Transcribe
DefaultGroupName=GPT Transcribe
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
OutputDir=..\dist\installer
OutputBaseFilename=GPTTranscribe-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
Uninstallable=yes
UninstallDisplayIcon={app}\{#AppExeName}
CloseApplications=yes
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\outputs\GPTTranscribe.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\GPT Transcribe"; Filename: "{app}\{#AppExeName}"
Name: "{commondesktop}\GPT Transcribe"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch GPT Transcribe"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{app}\{#AppExeName}"; Parameters: "--remove-launch-on-login"; Flags: runhidden waituntilterminated
