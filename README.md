# GPT Transcribe

GPT Transcribe is a lightweight, cross-platform dictation utility. Press one global hotkey, speak, press it again, and the transcript is inserted into the text field that was focused when recording began. It uses OpenAI's `gpt-transcribe` model and keeps the recording in memory until the transcription request is sent; failed requests retain the latest WAV for retry.

The repository contains two native desktop implementations:

- macOS: a Swift/AppKit menu-bar app for macOS 13 and newer.
- Windows: a Python tray app for Windows 10 and 11.

The default hotkey is `Ctrl+Shift+Space` on Windows and `Control+Shift+Space` on macOS. Both versions support an optional language hint, an optional 5–180 second recording limit, launch at login, and clipboard-based insertion. Leave the limit blank or enter `0` to record until you press the hotkey again.

## macOS

### Install

Download `GPTTranscribe.dmg` from the [GitHub Releases page](https://github.com/cyroz1/gpt-transcribe/releases), open it, and copy **GPT Transcribe** to Applications. On first use, macOS asks for microphone access. Before recording, the app checks for Accessibility access and opens **System Settings → Privacy & Security → Accessibility** when it is missing.

Open the menu-bar microphone icon and choose **Settings…**. The macOS app stores an API key in the macOS Keychain; it never writes the key to the settings file. It also accepts `OPENAI_API_KEY` from the process environment, which is useful for development. If `Command+V` is unavailable in the secure field, use the field's **Paste** button.

### Use

1. Focus a text field in another app.
2. Press `Control+Shift+Space` to start listening.
3. Speak normally.
4. Press the same hotkey to stop.
5. The transcript is pasted into the original app.

Open the menu-bar microphone menu to retry or delete a saved failed recording.

The menu-bar app uses the macOS default input device. Change it in **System Settings → Sound → Input**. Settings and logs live under `~/Library/Application Support/GPT Transcribe/`; preferences are stored through `UserDefaults`.

### Build from source

Requirements: macOS 13 or newer, Xcode Command Line Tools, and network access to `api.openai.com`.

```bash
./macos/build-macos.sh
```

The script runs the Swift tests, builds a release binary, creates `dist/GPTTranscribe.app`, signs it with `CODESIGN_IDENTITY` or the first local Apple Development identity when available, and creates `dist/GPTTranscribe.dmg` when `hdiutil` is available. It falls back to ad-hoc signing when no identity is available. The app is intentionally not notarized by this repository; a distribution certificate and Apple notarization credentials belong in the release environment.

## Windows

### Install and use

Create a Windows **user** environment variable named `OPENAI_API_KEY` containing an OpenAI Platform API key. Do not put the key in source code, `config.json`, the repository, or a shell command committed to history. Fully quit and relaunch the app after changing the variable.

Download `GPTTranscribe-Setup.exe` from the [GitHub Releases page](https://github.com/cyroz1/gpt-transcribe/releases) and run it as an administrator. The installer places GPT Transcribe under Program Files and adds a Start Menu shortcut. For a source build, run the executable produced by `build.ps1`.

1. Click inside a text box in any application.
2. Press `Ctrl+Shift+Space` to start listening.
3. Speak normally.
4. Press `Ctrl+Shift+Space` again to stop.
5. The transcript is pasted into the original text box.

Right-click the microphone icon in the system tray for Settings, retrying or deleting a saved failed recording, the log folder, or Quit. The app prevents multiple copies from running at the same time.

The Windows Settings window supports the hotkey, language hint, maximum recording length, microphone selection, and launch at sign-in. For unlimited recording, leave **Max seconds** blank or enter `0`. Settings and logs are stored under `%APPDATA%\GPTTranscribe\`; the API key is not stored there.

## Development checks

Windows:

```powershell
python -m unittest discover -s tests -v
python gpt_transcribe.py --check
python gpt_transcribe.py --list-devices
```

macOS:

```bash
swift test --package-path macos
./macos/build-macos.sh
```

The tests are offline and do not send audio to OpenAI. The `--check` command verifies the Windows runtime and Python dependencies; `--list-devices` prints PortAudio input devices.

## Privacy and security

- Audio is held in memory until the user stops listening, then sent to OpenAI for transcription.
- If transcription or insertion fails, the latest WAV is retained for retry at `%APPDATA%\GPTTranscribe\failed-recording.wav` on Windows or `~/Library/Application Support/GPT Transcribe/failed-recording.wav` on macOS. It is deleted after a successful retry or through **Delete saved recording** in the tray/menu-bar menu.
- Apart from that failure-recovery file, the app does not intentionally write recordings to disk.
- macOS stores a configured API key in Keychain; Windows reads `OPENAI_API_KEY` at runtime.
- Clipboard insertion temporarily exposes the transcript to local applications according to normal platform clipboard behavior.
- Synthetic keyboard input may be rejected by elevated, secure, password, sandboxed, or otherwise protected text fields.
- Launch-at-login is per-user on both platforms and does not grant elevation.

See [`docs/architecture.md`](docs/architecture.md) for the component and data-flow design and [`SECURITY.md`](SECURITY.md) for security boundaries and reporting guidance.

## Troubleshooting

### The hotkey does nothing

Another application may already own the hotkey. Open Settings, choose another combination, save, and try again. On macOS, global registration does not require Accessibility permission; Accessibility is needed for the final paste step.

### The microphone is unavailable

On macOS, allow microphone access in **System Settings → Privacy & Security → Microphone** and choose an input under **System Settings → Sound → Input**. On Windows, run `python gpt_transcribe.py --list-devices` and confirm microphone access in **Privacy & security → Microphone**.

### The API rejects the request

Confirm the key is a valid OpenAI Platform API key. The macOS app reads the Keychain value first after checking `OPENAI_API_KEY`; the Windows app reads the environment variable. The app does not use a ChatGPT login or ChatGPT subscription as API authentication.

### Text is not inserted

Try a normal text editor first. On macOS, grant Accessibility access to GPT Transcribe when prompted. If the app was rebuilt or moved, remove the old GPT Transcribe entry and add the current app again, then retry. On Windows, make sure the target window accepts `Ctrl+V`; applications running as administrator may reject input from a non-elevated tray app.

## Project layout

```text
gpt_transcribe.py                    Windows tray app and platform integration
tests/test_core.py                   Offline Python tests
build.ps1                            Windows PyInstaller build
installer/GPTTranscribe.iss         Windows Inno Setup definition
macos/Package.swift                  Native macOS Swift package
macos/Sources/GPTTranscribeMac/      Native macOS menu-bar app
macos/Tests/                         Native macOS unit tests
macos/build-macos.sh                 macOS .app and DMG build
.github/workflows/                   Tagged Windows and macOS release builds
SECURITY.md                          Security boundaries and reporting guidance
```

## References

- [OpenAI Create transcription API](https://developers.openai.com/api/reference/resources/audio/subresources/transcriptions/methods/create)
- [GPT Transcribe model](https://developers.openai.com/api/docs/models/gpt-transcribe)
