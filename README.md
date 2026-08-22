# GPT Transcribe for Windows

A lightweight Windows tray service that lets you dictate into almost any focused text box using OpenAI's GPT Transcribe model instead of Windows voice typing. The published Windows installer installs it system-wide under Program Files.

> This is a user-session tray application, not a Windows Service Control Manager service. It runs only while the signed-in user session is active.

## What it does

- Registers a global hotkey (`Ctrl+Shift+Space` by default).
- Records from the selected microphone while listening.
- Holds the recording in memory as a WAV payload; it does not create a local audio file.
- Sends the completed recording to `POST /v1/audio/transcriptions` with model `gpt-transcribe`.
- Returns focus to the text box that was active when dictation started.
- Inserts the transcript through the Windows clipboard and simulated `Ctrl+V`.
- Restores the previous text clipboard contents when possible.
- Shows status and errors from the system tray icon.

## Quick start

### 1. Set the API key

Create a Windows **user** environment variable:

| Name | Value |
| --- | --- |
| `OPENAI_API_KEY` | Your OpenAI Platform API key |

Use **Edit the system environment variables** → **Environment Variables** → **User variables**. Do not put the key in source code, `config.json`, the repository, or a command committed to shell history.

After changing the variable, fully quit and relaunch GPT Transcribe so Windows gives the app the updated environment.

### 2. Install and launch

Download `GPTTranscribe-Setup.exe` from the [GitHub Releases page](https://github.com/cyroz1/gpt-transcribe-windows/releases) and run it as an administrator. The installer places GPT Transcribe under Program Files and adds a Start Menu shortcut. For a source build, run the executable produced by `build.ps1`.

1. Click inside a text box in any application.
2. Press `Ctrl+Shift+Space` to start listening.
3. Speak normally.
4. Press `Ctrl+Shift+Space` again to stop.
5. The transcript is pasted into the original text box.

Right-click the microphone icon in the system tray for Settings, the log folder, or Quit. The app prevents multiple copies from running at the same time.

## Settings

The tray Settings window supports:

- Hotkey: a modifier combination such as `ctrl+shift+space` or `alt+space`.
- Language hint: optional ISO-639-1 code such as `en`.
- Maximum recording length: 5–180 seconds.
- Microphone: default input device or a specific input device.
- Launch GPT Transcribe when I sign in: per-user startup setting, off by default.

Settings are stored at `%APPDATA%\GPTTranscribe\config.json`. The API key is not stored there. Logs are written to `%APPDATA%\GPTTranscribe\app.log` and are intended to contain app status and error messages, not audio or credentials.

## Build from source

Requirements:

- Windows 10 or Windows 11
- Python 3.12 or newer
- Network access to `api.openai.com`
- Microphone permission in Windows Privacy & security settings
- Inno Setup 6, only when compiling the installer locally

From PowerShell in the project folder:

```powershell
.\build.ps1
```

The script creates an isolated build environment under `work\.venv`, installs the dependencies, and packages the app with PyInstaller. To compile the system-wide installer locally after installing Inno Setup 6, run `iscc .\installer\GPTTranscribe.iss` after the build completes.

The installer is compiled automatically by GitHub Actions whenever a `v*` tag is pushed. It publishes only `GPTTranscribe-Setup.exe` as the release asset. Installing requires administrator approval because it targets Program Files; the launch-on-login setting itself is per-user.

## Development checks

```powershell
python -m unittest discover -s tests -v
python gpt_transcribe.py --check
python gpt_transcribe.py --list-devices
```

`--check` verifies the Windows runtime, environment variable presence, and installed Python dependencies. `--list-devices` prints the audio devices visible to PortAudio. Tests are intentionally offline and do not send audio to the API.

## Privacy and security

- Audio is held in memory only until the user stops listening, then sent to OpenAI for transcription.
- No recording is written to disk by the app.
- The API key is read from `OPENAI_API_KEY` at runtime and never persisted by the app.
- The launch-on-login setting writes only the current user's Windows Run entry; it does not require or store the API key.
- The repository must remain private if it contains the packaged executable or internal documentation.
- Clipboard insertion is used for compatibility with ordinary text inputs. Clipboard data may be visible to other local applications while it is being used.
- The app uses synthetic keyboard input. Elevated, secure, password, sandboxed, or otherwise protected text fields may reject paste input.

See [`docs/architecture.md`](docs/architecture.md) for the component and data-flow design and [`SECURITY.md`](SECURITY.md) for reporting guidance.

## Troubleshooting

### The tray icon does not appear

Run `python gpt_transcribe.py --check` from the project folder. If dependencies are missing, rerun `.\build.ps1`. Check `%APPDATA%\GPTTranscribe\app.log` for startup details.

### The hotkey does nothing

Another application may already own the hotkey. Open Settings, choose another combination, save, then restart GPT Transcribe. Avoid hotkeys reserved by Windows or the application you use most.

### The microphone is unavailable

Run `python gpt_transcribe.py --list-devices`, select an input device in Settings, and confirm microphone access is enabled in Windows Privacy & security → Microphone.

### The API rejects the request

Confirm the Windows user variable is named exactly `OPENAI_API_KEY`, contains a valid OpenAI API key, and was set before launching the app. Restart the app after changing it. The app does not use a ChatGPT login or ChatGPT subscription as API authentication.

### Launch on login does not work

Open Settings and save **Launch GPT Transcribe when I sign in** again. The setting applies to the current Windows user and starts the installed Program Files executable at sign-in. If the app was uninstalled, reinstall it or turn the setting off before uninstalling.

### Text is not inserted

Make sure the target window still exists and accepts `Ctrl+V`. Try a normal text editor first. Applications running as administrator may not accept input from a non-elevated tray app.

## Project layout

```text
gpt_transcribe.py            Main tray app and Windows integration
tests/test_core.py           Offline tests for audio, multipart, and settings helpers
build.ps1                    Reproducible PyInstaller build
installer/GPTTranscribe.iss System-wide Inno Setup installer definition
.github/workflows/           Automated tagged-release installer build
docs/architecture.md         Detailed runtime and data-flow documentation
SECURITY.md                  Security boundary and reporting notes
```

## References

- [OpenAI Create transcription API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [GPT Transcribe model](https://developers.openai.com/api/docs/models/gpt-transcribe)
