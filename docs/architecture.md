# Architecture

GPT Transcribe has one platform-neutral transcription flow and two native desktop shells. The Windows implementation remains in `gpt_transcribe.py`; the macOS implementation is in `macos/Sources/GPTTranscribeMac/main.swift`.

## Runtime flow

```text
Focused text box
       │
       │ global hotkey
       ▼
Platform hotkey registration
       │
       ▼
Native microphone capture ──► in-memory PCM chunks
       │
       │ hotkey pressed again / max duration reached
       ▼
WAV encoder (memory only)
       │
       │ HTTPS multipart request
       ▼
OpenAI /v1/audio/transcriptions
       │ model=gpt-transcribe
       ▼
Transcript text
       │
       ├─► restore original target application
       ├─► place transcript in the platform clipboard
       └─► synthesize paste shortcut
```

## Components

### Global hotkey

On Windows, a dedicated thread calls the Win32 `RegisterHotKey` API. On macOS, `HotKeyManager` calls Carbon `RegisterEventHotKey` and listens for `kEventHotKeyPressed` events on the application event target. Both registrations allow the menu/tray app to receive the hotkey while another application owns the foreground window.

The default is `ctrl+shift+space` / `Control+Shift+Space`. The hotkey toggles between recording and stopping; it is not a push-to-talk key because both native registration APIs expose the registered key event rather than a reliable key-up stream.

### Audio capture

The Windows recorder uses `sounddevice.RawInputStream` with one `int16` channel. It first tries the configured sample rate and falls back to the device's native rate if necessary. The macOS recorder uses `AVAudioEngine` and converts tap buffers to mono 16-bit PCM in memory. macOS follows the system default input device; Windows can select a PortAudio input device. A timer limits recordings to the configured 5–180 second range.

When recording stops, both implementations wrap the PCM bytes in a standard mono 16-bit WAV container. The WAV bytes are not written to a temporary file.

### Transcription request

Both clients send a multipart request to:

```text
POST https://api.openai.com/v1/audio/transcriptions
model=gpt-transcribe
response_format=json
```

An optional `language` hint is included when configured. Windows reads the API key from `OPENAI_API_KEY`. macOS checks that environment variable first and otherwise reads the value saved in the macOS Keychain. Error handling converts invalid-key responses into a generic message so credential fragments are not echoed into the UI.

### Target capture and paste

At recording start, Windows captures the current foreground window handle and macOS captures the current `NSRunningApplication`. After transcription, each implementation restores the target, writes the transcript to its native clipboard, and sends a synthetic paste shortcut. Windows posts `Ctrl+V` through Win32 keyboard injection; macOS posts `Command+V` through `CGEvent` after Accessibility permission is granted.

The previous text clipboard value is retained in memory and restored one second later only if the clipboard still contains the inserted transcript. This avoids overwriting a new copy action made by the user.

### Tray, menu bar, and configuration

Windows uses `pystray` for the tray icon, Pillow for the runtime icon, and a Tk settings window. macOS uses an `NSStatusItem`, SF Symbols, and an AppKit settings window. Windows settings are saved under `%APPDATA%\GPTTranscribe\config.json`; macOS uses native `UserDefaults`. Both write status logs to the platform application-support directory.

The launch-at-login setting is disabled by default. Windows mirrors it to the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value. macOS uses `SMAppService.mainApp` to register the bundled app as a login item.

Both implementations prevent a second copy from registering another hotkey or competing for clipboard insertion.

### Installers

The Windows tagged-release workflow builds the PyInstaller executable, then compiles `installer/GPTTranscribe.iss` with Inno Setup. The installer requires administrator approval and places the executable under `%ProgramFiles%\GPT Transcribe`, with Start Menu and optional common-desktop shortcuts. The macOS workflow runs `macos/build-macos.sh`, which creates a native `.app`, ad-hoc signs it for the build, and packages it as a DMG. Release signing and notarization are intentionally left to the distribution environment.

## State machine

```text
idle ──start──► starting ──stream ready──► recording
  ▲                                      │
  │                                      │ stop / timeout
  │                                      ▼
  └────────────── finished ◄──── transcribing
```

The transcription worker runs separately from the tray/menu-bar and audio threads so the UI remains responsive while the network request is in progress. A new recording is ignored until the current transcription completes.

## Trust and data boundaries

| Data | Lifetime | Destination |
| --- | --- | --- |
| Microphone PCM | In memory during recording and request preparation | OpenAI transcription endpoint after stop |
| API key | Process memory; macOS may persist it in Keychain | Authorization header to OpenAI |
| Transcript | Process memory, clipboard, target app | Target foreground application and clipboard |
| Settings | Persistent native preferences | Windows JSON; macOS `UserDefaults` |
| Launch-at-login command | Per-user startup registration | Windows Run value; macOS `SMAppService` |
| Logs | Persistent local text | Platform application-support directory, `app.log` |

The application has no local server, database, cloud storage, or background upload queue. It requires the user's desktop session and microphone permission. macOS also requires Accessibility permission for cross-application paste.
