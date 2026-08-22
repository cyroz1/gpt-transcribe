# Security notes

## Credential handling

The application reads `OPENAI_API_KEY` from the Windows user environment at request time. It does not accept a key through the tray UI, write a key to `config.json`, embed a key in the executable, or commit a key to Git.

If a credential is ever exposed, revoke it in the OpenAI Platform and replace the Windows user environment variable. Restart the tray app after changing the variable.

## Audio and transcript handling

Recordings are retained in memory until the user stops dictation and the transcription request completes. The app does not intentionally write audio to disk. The transcript is placed in the Windows clipboard and pasted into the target application; other local processes may be able to observe clipboard contents according to normal Windows behavior.

Do not use the tool for secrets or regulated information unless the user's OpenAI account, organization policies, and data controls are appropriate for that use.

## Windows integration boundaries

The app uses a global hotkey, foreground-window APIs, the clipboard, and synthetic keyboard input. Protected or elevated applications may reject focus changes or simulated paste. This is a compatibility limitation, not a bypass mechanism.

The system-wide installer requires administrator approval because it installs under Program Files. The optional Launch on login setting writes only the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` entry; it does not grant elevation and does not write settings for other users.

## Reporting

For a suspected vulnerability, do not open a public issue with credentials, recordings, logs, or personal data. Contact the repository owner privately through GitHub and include a minimal reproduction, affected version, and impact. Rotate any credentials before sharing diagnostic material.
