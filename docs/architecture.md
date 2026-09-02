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
       │ mode toggle
       ├─ live-transcribe ─► resample to 24 kHz ─► Realtime WebSocket
       │                                             intent=transcription
       │                                             model=gpt-live-transcribe
       │                                             │
       │                                             ├─ deltas ─► ordered paste into captured target
       │                                             └─ stop ─► commit / final suffix reconciliation
       │
       └─ transcribe ─────► stop / max duration
                              ▼
                         WAV encoder ──► HTTPS multipart request
                                           model=gpt-transcribe
                                           │
                                           └─ full result ─► clipboard / paste
```

## Components

### Global hotkey

On Windows, a dedicated thread calls the Win32 `RegisterHotKey` API. On macOS, `HotKeyManager` calls Carbon `RegisterEventHotKey` and listens for `kEventHotKeyPressed` events on the application event target. Both registrations allow the menu/tray app to receive the hotkey while another application owns the foreground window.

The default is `ctrl+shift+space` / `Control+Shift+Space`. The hotkey toggles between recording and stopping; it is not a push-to-talk key because both native registration APIs expose the registered key event rather than a reliable key-up stream.

### Audio capture

The Windows recorder uses `sounddevice.RawInputStream` with one `int16` channel. It first tries the configured sample rate and falls back to the device's native rate if necessary. The macOS recorder uses `AVAudioEngine` and converts tap buffers to mono 16-bit PCM in memory. macOS follows the system default input device; Windows can select a PortAudio input device. A timer limits recordings to the configured positive 5–180 second range; a limit of `0` means no automatic stop timer.

When recording stops, both implementations wrap the PCM bytes in a standard mono 16-bit WAV container. The WAV bytes are not written to a temporary file.

### Transcription request

With standard `transcribe` mode selected, both clients send a multipart request to:

```text
POST https://api.openai.com/v1/audio/transcriptions
model=gpt-transcribe
response_format=json
prompt=<optional recording context>
keywords[]=<optional literal term>  (repeated)
languages[]=<optional language code> (repeated)
```

Optional `prompt`, `keywords[]`, and `languages[]` context settings are included when configured. The clients use the plural `languages` field and never send the legacy singular `language` field. Windows reads the API key from `OPENAI_API_KEY`. macOS checks that environment variable first and otherwise reads the value saved in the macOS Keychain. Error handling converts invalid-key responses into a generic message so credential fragments are not echoed into the UI.

With `live-transcribe` mode selected, each client opens `wss://api.openai.com/v1/realtime?intent=transcription`, then sends a transcription `session.update` selecting `gpt-live-transcribe`. It streams mono PCM16 audio at 24 kHz through `input_audio_buffer.append`, sends `input_audio_buffer.commit` when the hotkey stops recording, and collects the documented delta and completed events. Each delta is pasted into the target captured when recording began; the final completed transcript is used only to append a missing suffix, so the final result is not pasted twice. The same prompt, keywords, and language context settings are included in the realtime session update.

If transcription or paste fails, the latest WAV is atomically retained at `%APPDATA%\GPTTranscribe\failed-recording.wav` on Windows or `~/Library/Application Support/GPT Transcribe/failed-recording.wav` on macOS. The tray/menu-bar menu can retry that file without recording again; a successful retry removes it, and the user can delete it directly from the same menu.

### Target capture and paste

At recording start, Windows captures the current foreground window handle and macOS captures the current `NSRunningApplication`. Before recording on macOS, the app preflights the Accessibility/post-event permission and opens the Accessibility settings when access is missing, avoiding an unnecessary transcription request. Standard mode restores the target, writes the complete transcript to its native clipboard, and sends one synthetic paste shortcut. Live mode repeats that clipboard/paste operation for each ordered delta and restores the user's prior clipboard after the stream settles. Windows posts `Ctrl+V` through Win32 keyboard injection; macOS posts `Command+V` through `CGEvent` after Accessibility permission is granted.

The previous text clipboard value is retained in memory and restored one second later only if the clipboard still contains the inserted transcript. This avoids overwriting a new copy action made by the user.

### Tray, menu bar, and configuration

Windows uses `pystray` for the tray icon, Pillow for the runtime icon, and a Tk settings window. macOS uses an `NSStatusItem`, SF Symbols, and an AppKit settings window. Windows settings are saved under `%APPDATA%\GPTTranscribe\config.json`; macOS uses native `UserDefaults`. Both write status logs to the platform application-support directory.

The launch-at-login setting is disabled by default. Windows mirrors it to the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value. macOS uses `SMAppService.mainApp` to register the bundled app as a login item.

Both implementations prevent a second copy from registering another hotkey or competing for clipboard insertion.

### Installers

The Windows tagged-release workflow builds the PyInstaller executable, then compiles `installer/GPTTranscribe.iss` with Inno Setup. The installer requires administrator approval and places the executable under `%ProgramFiles%\GPT Transcribe`, with Start Menu and optional common-desktop shortcuts. The macOS workflow runs `macos/build-macos.sh`, which creates a native `.app`, uses the configured signing identity when available, and packages it as a DMG. Release signing and notarization are intentionally left to the distribution environment.

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
| Failed recording | Until successful retry or user deletion | Windows: `%APPDATA%\GPTTranscribe\failed-recording.wav`; macOS: `~/Library/Application Support/GPT Transcribe/failed-recording.wav` |
| API key | Process memory; macOS may persist it in Keychain | Authorization header to OpenAI |
| Transcript | Process memory, clipboard, target app | Target foreground application and clipboard |
| Settings | Persistent native preferences | Windows JSON; macOS `UserDefaults` |
| Launch-at-login command | Per-user startup registration | Windows Run value; macOS `SMAppService` |
| Logs | Persistent local text | Platform application-support directory, `app.log` |

The application has no local server, database, cloud storage, or background upload queue. It requires the user's desktop session and microphone permission. macOS also requires Accessibility permission for cross-application paste.
