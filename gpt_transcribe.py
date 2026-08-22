from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import json
import logging
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
import wave

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only dependency
    winreg = None

try:
    import sounddevice as sd
except ImportError:  # pragma: no cover - exercised by the dependency check
    sd = None

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - exercised by the dependency check
    pystray = None
    Image = None
    ImageDraw = None


APP_NAME = "GPT Transcribe"
APP_VERSION = "0.2.0"
MODEL = "gpt-transcribe"
TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_HOTKEY = "ctrl+shift+space"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_MAX_RECORDING_SECONDS = 90
STARTUP_REGISTRY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE_NAME = "GPTTranscribe"

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9
ERROR_ALREADY_EXISTS = 183


USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

USER32.RegisterHotKey.argtypes = [wintypes.HWND, wintypes.INT, wintypes.UINT, wintypes.UINT]
USER32.RegisterHotKey.restype = wintypes.BOOL
USER32.UnregisterHotKey.argtypes = [wintypes.HWND, wintypes.INT]
USER32.UnregisterHotKey.restype = wintypes.BOOL
USER32.GetForegroundWindow.argtypes = []
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.IsWindow.argtypes = [wintypes.HWND]
USER32.IsWindow.restype = wintypes.BOOL
USER32.IsIconic.argtypes = [wintypes.HWND]
USER32.IsIconic.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [wintypes.HWND, wintypes.INT]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [wintypes.HWND]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.BringWindowToTop.argtypes = [wintypes.HWND]
USER32.BringWindowToTop.restype = wintypes.BOOL
USER32.OpenClipboard.argtypes = [wintypes.HWND]
USER32.OpenClipboard.restype = wintypes.BOOL
USER32.CloseClipboard.argtypes = []
USER32.CloseClipboard.restype = wintypes.BOOL
USER32.EmptyClipboard.argtypes = []
USER32.EmptyClipboard.restype = wintypes.BOOL
USER32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
USER32.IsClipboardFormatAvailable.restype = wintypes.BOOL
USER32.GetClipboardData.argtypes = [wintypes.UINT]
USER32.GetClipboardData.restype = wintypes.HANDLE
USER32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
USER32.SetClipboardData.restype = wintypes.HANDLE
USER32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
USER32.keybd_event.restype = None
USER32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
USER32.PostThreadMessageW.restype = wintypes.BOOL

KERNEL32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
KERNEL32.CreateMutexW.restype = wintypes.HANDLE
KERNEL32.GetLastError.argtypes = []
KERNEL32.GetLastError.restype = wintypes.DWORD
KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
KERNEL32.GlobalAlloc.restype = wintypes.HGLOBAL
KERNEL32.GlobalLock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalLock.restype = wintypes.LPVOID
KERNEL32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalUnlock.restype = wintypes.BOOL
KERNEL32.GlobalFree.argtypes = [wintypes.HGLOBAL]
KERNEL32.GlobalFree.restype = wintypes.HGLOBAL
KERNEL32.GetCurrentThreadId.argtypes = []
KERNEL32.GetCurrentThreadId.restype = wintypes.DWORD


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def app_data_dir() -> Path:
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "GPTTranscribe"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def startup_command() -> str:
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{executable}"'
    return f'"{executable}" "{Path(__file__).resolve()}"'


def set_launch_on_login(enabled: bool) -> None:
    if winreg is None:
        raise RuntimeError("Launch on login is available only on Windows.")
    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER,
        STARTUP_REGISTRY_PATH,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, STARTUP_VALUE_NAME)
            except FileNotFoundError:
                pass


def configure_logging() -> logging.Logger:
    directory = app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("gpt_transcribe")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(directory / "app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger


LOGGER = configure_logging()


def log_exception(message: str, exc: BaseException) -> None:
    LOGGER.error("%s: %s", message, exc)


class TranscriptionError(RuntimeError):
    pass


class Config:
    def __init__(self, values: dict[str, object] | None = None):
        values = values or {}
        self.launch_on_login = self._as_bool(values.get("launch_on_login", False))
        self.hotkey = str(values.get("hotkey", DEFAULT_HOTKEY)).strip().lower() or DEFAULT_HOTKEY
        self.language = str(values.get("language", "")).strip()
        self.max_recording_seconds = self._bounded_int(
            values.get("max_recording_seconds", DEFAULT_MAX_RECORDING_SECONDS),
            minimum=5,
            maximum=180,
            fallback=DEFAULT_MAX_RECORDING_SECONDS,
        )
        self.sample_rate = self._bounded_int(
            values.get("sample_rate", DEFAULT_SAMPLE_RATE),
            minimum=8_000,
            maximum=48_000,
            fallback=DEFAULT_SAMPLE_RATE,
        )
        device = values.get("audio_device")
        self.audio_device = device if isinstance(device, int) else None

    @staticmethod
    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _bounded_int(value: object, minimum: int, maximum: int, fallback: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(minimum, min(maximum, number))

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        if not path.exists():
            return cls()
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return cls(payload)
        except Exception as exc:  # pragma: no cover - depends on local file state
            log_exception("Could not load config", exc)
        return cls()

    def save(self) -> None:
        directory = config_path().parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "launch_on_login": self.launch_on_login,
            "hotkey": self.hotkey,
            "language": self.language,
            "max_recording_seconds": self.max_recording_seconds,
            "sample_rate": self.sample_rate,
            "audio_device": self.audio_device,
        }
        temporary = directory / "config.json.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        temporary.replace(config_path())


MODIFIER_CODES = {
    "alt": MOD_ALT,
    "control": MOD_CONTROL,
    "ctrl": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

KEY_CODES = {
    "space": VK_SPACE,
    "tab": 0x09,
    "enter": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}
KEY_CODES.update({f"f{i}": 0x6F + i for i in range(1, 13)})


def parse_hotkey(value: str) -> tuple[int, int]:
    parts = [part.strip().lower() for part in value.split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("A hotkey needs at least one modifier and one key.")
    modifiers = 0
    for part in parts[:-1]:
        if part not in MODIFIER_CODES:
            raise ValueError(f"Unknown hotkey modifier: {part}")
        modifiers |= MODIFIER_CODES[part]
    key_name = parts[-1]
    if key_name in KEY_CODES:
        virtual_key = KEY_CODES[key_name]
    elif len(key_name) == 1 and key_name.isalnum():
        virtual_key = ord(key_name.upper())
    else:
        raise ValueError(f"Unknown hotkey key: {key_name}")
    return modifiers | MOD_NOREPEAT, virtual_key


def make_wav(pcm_bytes: bytes, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm_bytes)
    return output.getvalue()


def build_multipart(fields: dict[str, str], filename: str, file_bytes: bytes, mime_type: str) -> tuple[bytes, str]:
    boundary = "----GPTTranscribe" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(chunks), boundary


def _api_error_message(response_bytes: bytes, status: int) -> str:
    try:
        payload = json.loads(response_bytes.decode("utf-8", errors="replace"))
        if isinstance(payload, dict):
            error = payload.get("error")
            message = None
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            elif payload.get("message"):
                message = str(payload["message"])
            if message:
                lowered = message.lower()
                if "api key" in lowered and any(word in lowered for word in ("incorrect", "invalid", "unauthorized", "rejected")):
                    return "OpenAI rejected the API key. Update OPENAI_API_KEY and restart the app."
                return message[:400]
    except (ValueError, UnicodeDecodeError):
        pass
    return f"Transcription request failed with HTTP {status}."


def transcribe_audio(audio_bytes: bytes, config: Config) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("OPENAI_API_KEY is not available to this app.")
    fields = {"model": MODEL, "response_format": "json"}
    if config.language:
        fields["language"] = config.language
    body, boundary = build_multipart(fields, "dictation.wav", audio_bytes, "audio/wav")
    request = urllib.request.Request(
        TRANSCRIPTION_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise TranscriptionError(_api_error_message(raw, exc.code)) from None
    except urllib.error.URLError as exc:
        raise TranscriptionError(f"Could not reach OpenAI: {exc.reason}") from None
    except TimeoutError:
        raise TranscriptionError("The transcription request timed out.") from None

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise TranscriptionError("OpenAI returned an unreadable transcription response.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise TranscriptionError("OpenAI returned no transcript text.")
    return payload["text"].strip()


def _open_clipboard(retries: int = 12) -> bool:
    for _ in range(retries):
        if USER32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def read_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        if not USER32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = USER32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = KERNEL32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.wstring_at(pointer)
        finally:
            KERNEL32.GlobalUnlock(handle)
    finally:
        USER32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    if not _open_clipboard():
        raise RuntimeError("Windows clipboard is busy.")
    allocated = None
    try:
        if not USER32.EmptyClipboard():
            raise RuntimeError("Could not clear the Windows clipboard.")
        buffer = ctypes.create_unicode_buffer(text)
        allocated = KERNEL32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(buffer))
        if not allocated:
            raise RuntimeError("Could not allocate clipboard memory.")
        pointer = KERNEL32.GlobalLock(allocated)
        if not pointer:
            raise RuntimeError("Could not lock clipboard memory.")
        try:
            ctypes.memmove(pointer, ctypes.addressof(buffer), ctypes.sizeof(buffer))
        finally:
            KERNEL32.GlobalUnlock(allocated)
        if not USER32.SetClipboardData(CF_UNICODETEXT, allocated):
            raise RuntimeError("Could not write to the Windows clipboard.")
        allocated = None  # Windows owns it after SetClipboardData.
    finally:
        if allocated:
            KERNEL32.GlobalFree(allocated)
        USER32.CloseClipboard()


def _restore_clipboard_if_unchanged(previous: str | None, inserted: str) -> None:
    try:
        if read_clipboard_text() != inserted:
            return
        if previous is not None:
            set_clipboard_text(previous)
    except Exception as exc:  # pragma: no cover - depends on other apps using clipboard
        log_exception("Could not restore clipboard", exc)


def paste_text(text: str, target_window: int | None) -> None:
    previous = read_clipboard_text()
    set_clipboard_text(text)
    if target_window and USER32.IsWindow(target_window):
        if USER32.IsIconic(target_window):
            USER32.ShowWindow(target_window, SW_RESTORE)
        if USER32.GetForegroundWindow() != target_window:
            USER32.SetForegroundWindow(target_window)
            USER32.BringWindowToTop(target_window)
            time.sleep(0.12)
    USER32.keybd_event(VK_CONTROL, 0, 0, 0)
    USER32.keybd_event(VK_V, 0, 0, 0)
    USER32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    USER32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    threading.Timer(1.0, _restore_clipboard_if_unchanged, args=(previous, text)).start()


def create_icon_image(recording: bool = False) -> "Image.Image":
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow is required to create the tray icon.")
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    background = "#BDEFE8" if recording else "#161A22"
    foreground = "#10141C" if recording else "#BDEFE8"
    draw.rounded_rectangle((2, 2, 62, 62), radius=16, fill=background)
    draw.rounded_rectangle((25, 12, 39, 39), radius=8, fill=foreground)
    draw.arc((17, 23, 47, 50), 0, 180, fill=foreground, width=4)
    draw.line((32, 49, 32, 55), fill=foreground, width=4)
    draw.line((24, 56, 40, 56), fill=foreground, width=4)
    return image


class SingleInstance:
    def __init__(self) -> None:
        self.handle = None

    def acquire(self) -> bool:
        self.handle = KERNEL32.CreateMutexW(None, False, "Local\\GPTTranscribe.SingleInstance")
        if not self.handle:
            return False
        return KERNEL32.GetLastError() != ERROR_ALREADY_EXISTS

    def close(self) -> None:
        if self.handle:
            KERNEL32.CloseHandle(self.handle)
            self.handle = None


class App:
    def __init__(self) -> None:
        self.config = Config.load()
        self.state_lock = threading.RLock()
        self.state = "idle"
        self.status = "Ready"
        self.stop_event = threading.Event()
        self.hotkey_thread: threading.Thread | None = None
        self.hotkey_thread_id: int | None = None
        self.icon = None
        self.stream = None
        self.recording_timer: threading.Timer | None = None
        self.audio_chunks: list[bytes] = []
        self.audio_sample_rate = self.config.sample_rate
        self.target_window: int | None = None

    def run(self) -> None:
        if sd is None or pystray is None or Image is None:
            missing = []
            if sd is None:
                missing.append("sounddevice")
            if pystray is None:
                missing.append("pystray")
            if Image is None:
                missing.append("Pillow")
            raise RuntimeError("Missing Python dependencies: " + ", ".join(missing))
        self.icon = pystray.Icon(
            APP_NAME,
            create_icon_image(),
            APP_NAME,
            menu=pystray.Menu(
                pystray.MenuItem(self._menu_record_label, self._menu_toggle),
                pystray.MenuItem("Settings…", self._menu_settings),
                pystray.MenuItem("Open log folder", self._menu_open_log_folder),
                pystray.MenuItem("Quit", self._menu_quit),
            ),
        )
        self.hotkey_thread = threading.Thread(target=self._hotkey_loop, name="GPTTranscribeHotkey", daemon=True)
        self.hotkey_thread.start()
        self.icon.run()
        self.shutdown()

    def shutdown(self) -> None:
        self.stop_event.set()
        with self.state_lock:
            is_recording = self.state == "recording"
        if is_recording:
            self.stop_recording()
        if self.hotkey_thread_id:
            USER32.PostThreadMessageW(self.hotkey_thread_id, WM_QUIT, 0, 0)
        if self.hotkey_thread and self.hotkey_thread.is_alive():
            self.hotkey_thread.join(timeout=1.5)

    def _hotkey_loop(self) -> None:
        self.hotkey_thread_id = KERNEL32.GetCurrentThreadId()
        try:
            modifiers, virtual_key = parse_hotkey(self.config.hotkey)
        except ValueError as exc:
            self._set_status(f"Invalid hotkey: {exc}", notify=True)
            return
        if not USER32.RegisterHotKey(None, 1, modifiers, virtual_key):
            self._set_status(
                f"Hotkey unavailable: {self.config.hotkey}. Change it in Settings.",
                notify=True,
            )
            return
        try:
            message = MSG()
            while not self.stop_event.is_set():
                result = ctypes.windll.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == 1:
                    self.toggle_recording()
        finally:
            USER32.UnregisterHotKey(None, 1)

    def toggle_recording(self) -> None:
        with self.state_lock:
            current_state = self.state
        if current_state == "idle":
            self.start_recording()
        elif current_state == "recording":
            self.stop_recording()
        elif current_state == "transcribing":
            self._notify("Still transcribing", "Please wait for the current dictation to finish.")

    def start_recording(self) -> None:
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            self._set_status("Missing OPENAI_API_KEY", notify=True)
            return
        with self.state_lock:
            if self.state != "idle":
                return
            self.state = "starting"
            self.audio_chunks = []
            self.target_window = int(USER32.GetForegroundWindow() or 0) or None
            self.audio_sample_rate = self.config.sample_rate
        stream = None
        try:
            stream = sd.RawInputStream(
                samplerate=self.config.sample_rate,
                blocksize=0,
                device=self.config.audio_device,
                channels=1,
                dtype="int16",
                callback=self._audio_callback,
            )
            stream.start()
        except Exception as first_error:
            log_exception("Configured microphone could not start", first_error)
            try:
                stream = sd.RawInputStream(
                    samplerate=None,
                    blocksize=0,
                    device=self.config.audio_device,
                    channels=1,
                    dtype="int16",
                    callback=self._audio_callback,
                )
                stream.start()
            except Exception as second_error:
                log_exception("Default microphone could not start", second_error)
                with self.state_lock:
                    self.state = "idle"
                self._set_status("Microphone unavailable", notify=True)
                return
        with self.state_lock:
            if self.stop_event.is_set():
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                self.state = "idle"
                return
            self.stream = stream
            self.audio_sample_rate = int(round(float(getattr(stream, "samplerate", self.config.sample_rate))))
            self.state = "recording"
            self.status = "Listening… press the hotkey to finish"
            self.icon.update_menu()
            self.icon.icon = create_icon_image(recording=True)
            self.icon.title = f"{APP_NAME} — Listening"
        self._beep(880)
        self.recording_timer = threading.Timer(self.config.max_recording_seconds, self.stop_recording)
        self.recording_timer.daemon = True
        self.recording_timer.start()

    def _audio_callback(self, indata, _frames, _time_info, status) -> None:
        if status:
            LOGGER.warning("Audio status: %s", status)
        with self.state_lock:
            if self.state == "recording":
                self.audio_chunks.append(bytes(indata))

    def stop_recording(self) -> None:
        with self.state_lock:
            if self.state != "recording":
                return
            self.state = "transcribing"
            stream = self.stream
            self.stream = None
            chunks = self.audio_chunks
            self.audio_chunks = []
            sample_rate = self.audio_sample_rate
            target_window = self.target_window
            if self.recording_timer:
                self.recording_timer.cancel()
                self.recording_timer = None
            self.status = "Transcribing…"
            self.icon.update_menu()
            self.icon.icon = create_icon_image(recording=False)
            self.icon.title = f"{APP_NAME} — Transcribing"
        try:
            if stream:
                stream.stop()
                stream.close()
        except Exception as exc:
            log_exception("Could not close microphone stream", exc)
        audio_bytes = make_wav(b"".join(chunks), sample_rate)
        if len(audio_bytes) < 1_000:
            self._finish_with_status("No audio captured", notify=True)
            return
        worker = threading.Thread(
            target=self._transcribe_and_paste,
            args=(audio_bytes, target_window),
            name="GPTTranscribeRequest",
            daemon=True,
        )
        worker.start()
        self._beep(660)

    def _transcribe_and_paste(self, audio_bytes: bytes, target_window: int | None) -> None:
        try:
            transcript = transcribe_audio(audio_bytes, self.config)
            if not transcript:
                self._finish_with_status("No speech detected", notify=True)
                return
            paste_text(transcript, target_window)
            self._finish_with_status("Inserted transcript", notify=False)
        except TranscriptionError as exc:
            log_exception("Transcription failed", exc)
            self._finish_with_status(str(exc), notify=True)
        except Exception as exc:
            log_exception("Could not insert transcript", exc)
            self._finish_with_status("Could not insert transcript", notify=True)

    def _finish_with_status(self, status: str, notify: bool) -> None:
        with self.state_lock:
            self.state = "idle"
            self.status = status
            if self.icon:
                self.icon.update_menu()
                self.icon.title = f"{APP_NAME} — {status}"
        if notify:
            self._notify(APP_NAME, status)

    def _set_status(self, status: str, notify: bool = False) -> None:
        with self.state_lock:
            self.status = status
            if self.icon:
                self.icon.update_menu()
                self.icon.title = f"{APP_NAME} — {status}"
        LOGGER.info(status)
        if notify:
            self._notify(APP_NAME, status)

    def _notify(self, title: str, message: str) -> None:
        LOGGER.info("%s: %s", title, message)
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception:
                pass

    def _beep(self, frequency: int) -> None:
        try:
            import winsound

            winsound.Beep(frequency, 90)
        except Exception:
            pass

    def _menu_record_label(self, _item) -> str:
        with self.state_lock:
            if self.state == "recording":
                return "Stop listening"
            if self.state == "transcribing":
                return "Transcribing…"
            return f"Start listening ({self.config.hotkey})"

    def _menu_toggle(self, _icon, _item) -> None:
        self.toggle_recording()

    def _menu_quit(self, icon, _item) -> None:
        icon.stop()

    def _menu_open_log_folder(self, _icon, _item) -> None:
        try:
            os.startfile(str(app_data_dir()))
        except Exception as exc:
            log_exception("Could not open log folder", exc)

    def _menu_settings(self, _icon, _item) -> None:
        threading.Thread(target=self._settings_window, name="GPTTranscribeSettings", daemon=True).start()

    def _settings_window(self) -> None:
        try:
            import tkinter as tk
            from tkinter import messagebox, ttk

            root = tk.Tk()
            root.title("GPT Transcribe settings")
            root.resizable(False, False)
            root.attributes("-topmost", True)
            frame = ttk.Frame(root, padding=18)
            frame.grid()

            ttk.Label(frame, text="GPT Transcribe", font=("Segoe UI", 15, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
            )
            ttk.Label(frame, text="Hotkey").grid(row=1, column=0, sticky="w", pady=4)
            hotkey = tk.StringVar(value=self.config.hotkey)
            ttk.Entry(frame, textvariable=hotkey, width=24).grid(row=1, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Language (optional)").grid(row=2, column=0, sticky="w", pady=4)
            language = tk.StringVar(value=self.config.language)
            ttk.Entry(frame, textvariable=language, width=24).grid(row=2, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Max seconds").grid(row=3, column=0, sticky="w", pady=4)
            max_seconds = tk.IntVar(value=self.config.max_recording_seconds)
            ttk.Spinbox(frame, from_=5, to=180, textvariable=max_seconds, width=22).grid(
                row=3, column=1, sticky="ew", pady=4
            )

            device_ids: list[int | None] = [None]
            device_labels = ["Default microphone"]
            if sd is not None:
                try:
                    for index, device_info in enumerate(sd.query_devices()):
                        if int(device_info.get("max_input_channels", 0)) > 0:
                            device_ids.append(index)
                            device_labels.append(f"{index}: {device_info.get('name', 'Microphone')}")
                except Exception as exc:
                    log_exception("Could not enumerate microphones", exc)
            ttk.Label(frame, text="Microphone").grid(row=4, column=0, sticky="w", pady=4)
            selected_index = device_ids.index(self.config.audio_device) if self.config.audio_device in device_ids else 0
            device = tk.StringVar(value=device_labels[selected_index])
            ttk.Combobox(frame, textvariable=device, values=device_labels, state="readonly", width=21).grid(
                row=4, column=1, sticky="ew", pady=4
            )

            launch_on_login = tk.BooleanVar(value=self.config.launch_on_login)
            ttk.Checkbutton(
                frame,
                text="Launch GPT Transcribe when I sign in",
                variable=launch_on_login,
            ).grid(row=5, column=0, columnspan=2, sticky="w", pady=4)

            ttk.Label(
                frame,
                text="Audio is sent to OpenAI only after you stop listening.\nThe API key is read from OPENAI_API_KEY and never saved here.",
                foreground="#555555",
            ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 12))

            buttons = ttk.Frame(frame)
            buttons.grid(row=7, column=0, columnspan=2, sticky="e")

            def save_and_close() -> None:
                try:
                    parse_hotkey(hotkey.get())
                    seconds = max(5, min(180, int(max_seconds.get())))
                except ValueError as exc:
                    messagebox.showerror("Invalid settings", str(exc), parent=root)
                    return
                self.config.hotkey = hotkey.get().strip().lower()
                self.config.language = language.get().strip()
                self.config.max_recording_seconds = seconds
                self.config.audio_device = device_ids[device_labels.index(device.get())]
                try:
                    set_launch_on_login(bool(launch_on_login.get()))
                except Exception as exc:
                    log_exception("Could not update launch-on-login setting", exc)
                    messagebox.showerror("Launch on login", str(exc), parent=root)
                    return
                self.config.launch_on_login = bool(launch_on_login.get())
                self.config.save()
                self._notify(APP_NAME, "Settings saved. Launch on login applies at your next sign-in.")
                root.destroy()

            ttk.Button(buttons, text="Cancel", command=root.destroy).pack(side="right", padx=(8, 0))
            ttk.Button(buttons, text="Save", command=save_and_close).pack(side="right")
            root.mainloop()
        except Exception as exc:
            log_exception("Settings window failed", exc)


def list_devices() -> int:
    if sd is None:
        print("sounddevice is not installed. Run the setup/build script first.")
        return 1
    try:
        print(sd.query_devices())
        return 0
    except Exception as exc:
        print(f"Could not enumerate audio devices: {exc}")
        return 1


def check_installation() -> int:
    checks = {
        "Windows": sys.platform == "win32",
        "OPENAI_API_KEY present": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "sounddevice installed": sd is not None,
        "pystray installed": pystray is not None,
        "Pillow installed": Image is not None,
    }
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'MISSING'}  {name}")
    return 0 if all(checks.values()) else 1


def show_error(message: str) -> None:
    try:
        USER32.MessageBoxW(None, message, APP_NAME, 0x10)
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    if sys.platform != "win32":
        print("GPT Transcribe is a Windows-only tray app.")
        return 1
    if "--list-devices" in sys.argv:
        return list_devices()
    if "--check" in sys.argv:
        return check_installation()
    if "--remove-launch-on-login" in sys.argv:
        try:
            set_launch_on_login(False)
            return 0
        except Exception as exc:
            log_exception("Could not remove launch-on-login setting", exc)
            return 1
    instance = SingleInstance()
    if not instance.acquire():
        show_error("GPT Transcribe is already running. Look for its microphone icon in the system tray.")
        return 1
    try:
        App().run()
        return 0
    except Exception as exc:
        log_exception("App failed to start", exc)
        show_error(str(exc))
        return 1
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
