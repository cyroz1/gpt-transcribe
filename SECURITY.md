# Security notes

## Credential handling

The Windows app reads `OPENAI_API_KEY` from the user environment at request time. The macOS app reads the same environment variable for development and can store a configured key in the macOS Keychain. Neither app writes an API key to its settings file, embeds a key in the executable, or commits a key to Git.

If a credential is ever exposed, revoke it in the OpenAI Platform and replace the environment variable or Keychain value. Fully quit and relaunch the app after changing the variable.

## Audio and transcript handling

Recordings are retained in memory until the user stops dictation and the transcription request completes. The app does not intentionally write audio to disk. The transcript is placed in the platform clipboard and pasted into the target application; other local processes may be able to observe clipboard contents according to normal platform behavior.

Do not use the tool for secrets or regulated information unless the user's OpenAI account, organization policies, and data controls are appropriate for that use.

## Platform integration boundaries

The app uses a global hotkey, foreground-application APIs, the clipboard, and synthetic keyboard input. Protected or elevated applications may reject focus changes or simulated paste. This is a compatibility limitation, not a bypass mechanism.

- Windows installation requires administrator approval because it installs under Program Files. Launch at sign-in writes only the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry.
- macOS paste insertion requires the user to grant Accessibility permission. The app uses that permission only to activate the target application and post Command-V; it does not inspect or modify protected UI contents.
- macOS microphone capture requires the standard microphone permission declared in the app bundle. The app uses the system default input device and does not persist raw audio.

Neither platform's launch-at-login option grants elevation or writes settings for other users.

## Reporting

For a suspected vulnerability, do not open a public issue with credentials, recordings, logs, or personal data. Contact the repository owner privately through GitHub and include a minimal reproduction, affected version, and impact. Rotate any credentials before sharing diagnostic material.
