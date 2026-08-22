# Architecture

## Runtime flow

```text
Focused text box
       │
       │ Ctrl+Shift+Space
       ▼
Win32 RegisterHotKey thread
       │
       ▼
sounddevice RawInputStream ──► in-memory PCM chunks
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
       ├─► restore original target window
       ├─► place transcript in CF_UNICODETEXT clipboard
       └─► synthesize Ctrl+V
```

## Components

### Global hotkey

The app creates a dedicated thread and calls the Win32 `RegisterHotKey` API with a configurable modifier/key combination. Because the registration is associated with the thread rather than a visible window, the tray app can receive the hotkey while another application owns the foreground window.

The default is `ctrl+shift+space`. The hotkey toggles between recording and stopping; it is not a push-to-talk key because `RegisterHotKey` exposes the registered key event rather than a reliable key-up stream.

### Audio capture

The recorder uses `sounddevice.RawInputStream` with one `int16` channel. It first tries the configured sample rate and falls back to the device's native rate if necessary. Callback frames are appended to an in-memory byte buffer. A timer limits recordings to the configured 5–180 second range.

When recording stops, the PCM bytes are wrapped in a standard mono 16-bit WAV container using Python's `wave` module. The WAV bytes are not written to a temporary file.

### Transcription request

`urllib.request` sends a multipart form request to:

```text
POST https://api.openai.com/v1/audio/transcriptions
model=gpt-transcribe
response_format=json
```

An optional `language` hint is included when configured. The API key is read from the process environment immediately before the request. Error handling deliberately converts invalid-key responses into a generic message so API responses cannot echo credential fragments into the UI.

### Target capture and paste

At recording start, the app captures the current foreground window handle. After transcription, it restores that window if it is still valid, writes the transcript to the Unicode clipboard format, and sends `Ctrl+V` through Win32 keyboard injection.

The previous Unicode text clipboard value is retained in memory and restored one second later only if the clipboard still contains the inserted transcript. This avoids overwriting a new copy action made by the user.

### Tray and configuration

`pystray` owns the tray icon and menu. Pillow generates the icon at runtime, with a light state while recording. Settings are saved under `%APPDATA%\GPTTranscribe\config.json`; logs are written beside it in `app.log`.

The Launch on login setting is stored in the config file and mirrored to the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value. It launches the installed executable at sign-in and is disabled by default.

The app uses a named local mutex so a second launch exits instead of registering a duplicate hotkey or creating competing clipboard writes.

### System-wide installer

The tagged-release workflow builds the PyInstaller executable, then compiles `installer/GPTTranscribe.iss` with Inno Setup. The installer requires administrator approval and places the executable under `%ProgramFiles%\GPT Transcribe`, with Start Menu and optional common-desktop shortcuts. User configuration remains in each user's `%APPDATA%` directory.

## State machine

```text
idle ──start──► starting ──stream ready──► recording
  ▲                                      │
  │                                      │ stop / timeout
  │                                      ▼
  └────────────── finished ◄──── transcribing
```

The transcription worker runs separately from the tray and hotkey threads so the UI remains responsive while the network request is in progress. A new recording is ignored until the current transcription completes.

## Trust and data boundaries

| Data | Lifetime | Destination |
| --- | --- | --- |
| Microphone PCM | In memory during recording and request preparation | OpenAI transcription endpoint after stop |
| API key | Process memory only | Authorization header to OpenAI |
| Transcript | Process memory, clipboard, target app | Target foreground window and clipboard |
| Settings | Persistent local JSON | `%APPDATA%\GPTTranscribe\config.json` |
| Launch-on-login command | Per-user Windows Run value | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` |
| Logs | Persistent local text | `%APPDATA%\GPTTranscribe\app.log` |

The application has no local server, database, cloud storage, or background upload queue. It requires the user's Windows session and microphone permission.
