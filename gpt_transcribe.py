from __future__ import annotations

import ctypes
from ctypes import wintypes
from importlib import util as importlib_util
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only dependency
    winreg = None

# These optional modules are loaded only when their functionality is needed.
# In particular, sounddevice imports CFFI and PortAudio, neither of which is
# needed while the tray app is idle.
sd = None
pystray = None
Image = None
ImageDraw = None
_SOUNDDEVICE_IMPORT_ERROR: ImportError | None = None
_TRAY_IMPORT_ERROR: ImportError | None = None


APP_NAME = "GPT Transcribe"
APP_VERSION = "0.3.7"
MODEL = "gpt-realtime-transcribe"
TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_HOTKEY = "ctrl+shift+space"
DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_MAX_RECORDING_SECONDS = 90
MIN_MAX_RECORDING_SECONDS = 5
MAX_MAX_RECORDING_SECONDS = 180
FAILED_RECORDING_FILENAME = "failed-recording.wav"
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


def _module_available(module_name: str) -> bool:
    try:
        return importlib_util.find_spec(module_name) is not None
    except (ImportError, AttributeError):
        return False


def _load_sounddevice():
    global sd, _SOUNDDEVICE_IMPORT_ERROR
    if sd is not None:
        return sd
    if _SOUNDDEVICE_IMPORT_ERROR is not None:
        raise RuntimeError("sounddevice is not installed. Run the setup/build script first.") from _SOUNDDEVICE_IMPORT_ERROR
    try:
        import sounddevice as sounddevice_module
    except (ImportError, OSError) as exc:  # pragma: no cover - dependency-specific
        _SOUNDDEVICE_IMPORT_ERROR = exc
        raise RuntimeError("sounddevice is not installed. Run the setup/build script first.") from exc
    sd = sounddevice_module
    return sd


def _load_tray_dependencies():
    global pystray, Image, ImageDraw, _TRAY_IMPORT_ERROR
    if pystray is not None and Image is not None and ImageDraw is not None:
        return pystray
    if _TRAY_IMPORT_ERROR is not None:
        raise RuntimeError("pystray and Pillow are required to create the tray app.") from _TRAY_IMPORT_ERROR
    try:
        import pystray as pystray_module
        from PIL import Image as image_module
        from PIL import ImageDraw as image_draw_module
    except (ImportError, OSError) as exc:  # pragma: no cover - dependency-specific
        _TRAY_IMPORT_ERROR = exc
        raise RuntimeError("pystray and Pillow are required to create the tray app.") from exc
    pystray = pystray_module
    Image = image_module
    ImageDraw = image_draw_module
    return pystray


def app_data_dir() -> Path:
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else Path.home() / "AppData" / "Roaming"
    return base / "GPTTranscribe"


def config_path() -> Path:
    return app_data_dir() / "config.json"


def failed_recording_path() -> Path:
    return app_data_dir() / FAILED_RECORDING_FILENAME


def save_failed_recording(audio_bytes: bytes) -> Path:
    path = failed_recording_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(audio_bytes)
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def delete_failed_recording(path: Path | None = None) -> None:
    target = path or failed_recording_path()
    try:
        target.unlink()
    except FileNotFoundError:
        pass


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


LOGGER = logging.getLogger("gpt_transcribe")
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False
LOGGER.addHandler(logging.NullHandler())


def configure_logging() -> logging.Logger:
    if any(getattr(handler, "_gpt_transcribe_file_handler", False) for handler in LOGGER.handlers):
        return LOGGER
    from logging.handlers import RotatingFileHandler

    directory = app_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "app.log",
        maxBytes=512 * 1024,
        backupCount=1,
        encoding="utf-8",
        delay=True,
    )
    handler._gpt_transcribe_file_handler = True
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOGGER.addHandler(handler)
    return LOGGER


def log_exception(message: str, exc: BaseException) -> None:
    LOGGER.error("%s: %s", message, exc)


class TranscriptionError(RuntimeError):
    pass


def parse_setting_list(value: object) -> list[str]:
    """Normalize comma- or newline-separated transcription settings."""
    if isinstance(value, (list, tuple)):
        values = value
    else:
        values = str(value or "").replace(",", "\n").splitlines()
    return [str(item).strip() for item in values if str(item).strip()]


class Config:
    def __init__(self, values: dict[str, object] | None = None):
        values = values or {}
        self.launch_on_login = self._as_bool(values.get("launch_on_login", False))
        self.hotkey = str(values.get("hotkey", DEFAULT_HOTKEY)).strip().lower() or DEFAULT_HOTKEY
        self.prompt = str(values.get("prompt") or "").strip()
        self.keywords = parse_setting_list(values.get("keywords", []))
        languages = values.get("languages")
        if languages is None:
            languages = values.get("language", "")
        self.languages = parse_setting_list(languages)
        self.max_recording_seconds = self._normalize_max_recording_seconds(
            values.get("max_recording_seconds", DEFAULT_MAX_RECORDING_SECONDS)
        )
        self.sample_rate = self._bounded_int(
            values.get("sample_rate", DEFAULT_SAMPLE_RATE),
            minimum=8_000,
            maximum=48_000,
            fallback=DEFAULT_SAMPLE_RATE,
        )
        device = values.get("audio_device")
        self.audio_device = device if isinstance(device, int) else None

    @property
    def language(self) -> str:
        """Keep the old singular accessor available to callers."""
        return self.languages[0] if self.languages else ""

    @language.setter
    def language(self, value: object) -> None:
        self.languages = parse_setting_list(value)

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

    @staticmethod
    def _normalize_max_recording_seconds(value: object) -> int:
        if isinstance(value, str) and not value.strip():
            return 0
        try:
            number = int(value)
        except (TypeError, ValueError):
            return DEFAULT_MAX_RECORDING_SECONDS
        if number == 0:
            return 0
        return max(MIN_MAX_RECORDING_SECONDS, min(MAX_MAX_RECORDING_SECONDS, number))

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
            "prompt": self.prompt,
            "keywords": self.keywords,
            "languages": self.languages,
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


def make_wav(pcm_bytes: bytes | bytearray | memoryview, sample_rate: int) -> bytes:
    import io
    import wave

    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(pcm_bytes)
    return output.getvalue()


def build_multipart(
    fields: dict[str, str | list[str]], filename: str, file_bytes: bytes, mime_type: str
) -> tuple[bytes, str]:
    import secrets

    boundary = "----GPTTranscribe" + secrets.token_hex(16)
    chunks: list[bytes] = []
    for name, value in fields.items():
        values = value if isinstance(value, list) else [value]
        for item in values:
            chunks.extend(
                [
                    f"--{boundary}\r\n".encode("utf-8"),
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                    str(item).encode("utf-8"),
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
    import urllib.error as urllib_error
    import urllib.request as urllib_request

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise TranscriptionError("OPENAI_API_KEY is not available to this app.")
    fields: dict[str, str | list[str]] = {"model": MODEL, "response_format": "json"}
    if config.prompt:
        fields["prompt"] = config.prompt
    if config.keywords:
        fields["keywords[]"] = config.keywords
    if config.languages:
        fields["languages[]"] = config.languages
    body, boundary = build_multipart(fields, "dictation.wav", audio_bytes, "audio/wav")
    # The multipart body owns its own copy of the WAV payload. Release the
    # larger temporary as soon as the body has been assembled.
    del audio_bytes
    request = urllib_request.Request(
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
        try:
            with urllib_request.urlopen(request, timeout=180) as response:
                raw = response.read()
        except urllib_error.HTTPError as exc:
            raw = exc.read()
            raise TranscriptionError(_api_error_message(raw, exc.code)) from None
        except urllib_error.URLError as exc:
            raise TranscriptionError(f"Could not reach OpenAI: {exc.reason}") from None
        except TimeoutError:
            raise TranscriptionError("The transcription request timed out.") from None
    finally:
        # Do not retain the request body while parsing the small JSON response.
        del request, body

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
    restore_timer = threading.Timer(1.0, _restore_clipboard_if_unchanged, args=(previous, text))
    restore_timer.daemon = True
    restore_timer.start()


def create_icon_image(recording: bool = False) -> "Image.Image":
    if Image is None or ImageDraw is None:
        _load_tray_dependencies()
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
        self.audio_buffer = bytearray()
        self.audio_status_logged = False
        self.audio_sample_rate = self.config.sample_rate
        self.target_window: int | None = None
        saved_recording = failed_recording_path()
        self.pending_audio_path: Path | None = saved_recording if saved_recording.is_file() else None
        self.pending_target_window: int | None = None

    def run(self) -> None:
        tray = _load_tray_dependencies()
        if not _module_available("sounddevice"):
            raise RuntimeError("Missing Python dependency: sounddevice")
        self.icon = tray.Icon(
            APP_NAME,
            create_icon_image(),
            APP_NAME,
            menu=tray.Menu(
                tray.MenuItem(self._menu_record_label, self._menu_toggle),
                tray.MenuItem(
                    self._menu_retry_label,
                    self._menu_retry,
                    enabled=self._can_retry,
                ),
                tray.MenuItem(
                    "Delete saved recording",
                    self._menu_delete_saved_recording,
                    enabled=self._can_retry,
                ),
                tray.MenuItem("Settings…", self._menu_settings),
                tray.MenuItem("Open log folder", self._menu_open_log_folder),
                tray.MenuItem("Quit", self._menu_quit),
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
            self.audio_buffer = bytearray()
            self.audio_status_logged = False
            self.target_window = int(USER32.GetForegroundWindow() or 0) or None
            self.audio_sample_rate = self.config.sample_rate
        try:
            sounddevice = _load_sounddevice()
        except RuntimeError as exc:
            with self.state_lock:
                self.state = "idle"
            self._set_status(str(exc), notify=True)
            return
        stream = None
        try:
            stream = sounddevice.RawInputStream(
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
                stream = sounddevice.RawInputStream(
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
        self._start_recording_timer()

    def _start_recording_timer(self) -> None:
        if self.config.max_recording_seconds <= 0:
            self.recording_timer = None
            return
        self.recording_timer = threading.Timer(self.config.max_recording_seconds, self.stop_recording)
        self.recording_timer.daemon = True
        self.recording_timer.start()

    def _audio_callback(self, indata, _frames, _time_info, status) -> None:
        log_status = False
        with self.state_lock:
            if self.state == "recording":
                self.audio_buffer.extend(indata)
                if status and not self.audio_status_logged:
                    self.audio_status_logged = True
                    log_status = True
        if log_status:
            LOGGER.warning("Audio status: %s", status)

    def stop_recording(self) -> None:
        with self.state_lock:
            if self.state != "recording":
                return
            self.state = "transcribing"
            stream = self.stream
            self.stream = None
            pcm_buffer = self.audio_buffer
            self.audio_buffer = bytearray()
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
        audio_bytes = make_wav(pcm_buffer, sample_rate)
        del pcm_buffer
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

    def _save_for_retry(self, audio_bytes: bytes, target_window: int | None) -> bool:
        try:
            path = save_failed_recording(audio_bytes)
        except Exception as exc:
            log_exception("Could not save failed recording", exc)
            return False
        with self.state_lock:
            self.pending_audio_path = path
            self.pending_target_window = target_window
            if self.icon:
                self.icon.update_menu()
        return True

    def _remove_saved_recording(self, path: Path) -> bool:
        try:
            delete_failed_recording(path)
        except Exception as exc:
            log_exception("Could not delete saved recording", exc)
            return False
        with self.state_lock:
            if self.pending_audio_path == path:
                self.pending_audio_path = None
                self.pending_target_window = None
        return True

    @staticmethod
    def _failure_status(reason: str, saved: bool) -> str:
        message = reason.rstrip(".") + "."
        if saved:
            return message + " Saved recording for retry."
        return message + " Could not save recording for retry."

    def _transcribe_and_paste(
        self,
        audio_bytes: bytes,
        target_window: int | None,
        pending_path: Path | None = None,
    ) -> None:
        try:
            transcript = transcribe_audio(audio_bytes, self.config)
            if not transcript:
                saved = self._save_for_retry(audio_bytes, target_window)
                self._finish_with_status(self._failure_status("No speech detected", saved), notify=True)
                return
            paste_text(transcript, target_window)
            removed = pending_path is None or self._remove_saved_recording(pending_path)
            self._finish_with_status(
                "Inserted transcript" if removed else "Inserted transcript; saved recording retained",
                notify=not removed,
            )
        except TranscriptionError as exc:
            log_exception("Transcription failed", exc)
            saved = self._save_for_retry(audio_bytes, target_window)
            self._finish_with_status(self._failure_status(str(exc), saved), notify=True)
        except Exception as exc:
            log_exception("Could not insert transcript", exc)
            saved = self._save_for_retry(audio_bytes, target_window)
            self._finish_with_status(self._failure_status("Could not insert transcript", saved), notify=True)

    def _retry_failed_recording(self, path: Path, target_window: int | None) -> None:
        try:
            audio_bytes = path.read_bytes()
        except Exception as exc:
            log_exception("Could not read saved recording", exc)
            self._finish_with_status("Could not read saved recording", notify=True)
            return
        if len(audio_bytes) < 1_000:
            self._finish_with_status("Saved recording is empty", notify=True)
            return
        self._transcribe_and_paste(audio_bytes, target_window, pending_path=path)

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

    def _menu_retry_label(self, _item) -> str:
        return "Retry failed recording"

    def _can_retry(self, _item) -> bool:
        with self.state_lock:
            return (
                self.state == "idle"
                and self.pending_audio_path is not None
                and self.pending_audio_path.is_file()
            )

    def _menu_toggle(self, _icon, _item) -> None:
        self.toggle_recording()

    def _menu_retry(self, _icon, _item) -> None:
        missing = False
        with self.state_lock:
            if self.state != "idle":
                return
            path = self.pending_audio_path
            if path is None or not path.is_file():
                missing = path is not None
                path = None
                self.pending_audio_path = None
                self.pending_target_window = None
            else:
                target_window = self.pending_target_window
                if target_window is None or not USER32.IsWindow(target_window):
                    target_window = int(USER32.GetForegroundWindow() or 0) or None
                self.state = "transcribing"
                self.status = "Retrying saved recording…"
                if self.icon:
                    self.icon.update_menu()
                    self.icon.title = f"{APP_NAME} — Retrying saved recording"
        if missing:
            self._set_status("Saved recording is unavailable", notify=True)
            return
        if path is None:
            return
        worker = threading.Thread(
            target=self._retry_failed_recording,
            args=(path, target_window),
            name="GPTTranscribeRetry",
            daemon=True,
        )
        worker.start()

    def _menu_delete_saved_recording(self, _icon, _item) -> None:
        with self.state_lock:
            if self.state != "idle" or self.pending_audio_path is None:
                return
            path = self.pending_audio_path
        if self._remove_saved_recording(path):
            self._set_status("Saved recording deleted", notify=True)

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
            frame.columnconfigure(1, weight=1)

            ttk.Label(frame, text="GPT Transcribe", font=("Segoe UI", 15, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
            )
            ttk.Label(frame, text="Hotkey").grid(row=1, column=0, sticky="w", pady=4)
            hotkey = tk.StringVar(value=self.config.hotkey)
            ttk.Entry(frame, textvariable=hotkey, width=24).grid(row=1, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Languages (optional)").grid(row=2, column=0, sticky="w", pady=4)
            languages = tk.StringVar(value=", ".join(self.config.languages))
            ttk.Entry(frame, textvariable=languages, width=24).grid(row=2, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Prompt (optional)").grid(row=3, column=0, sticky="nw", pady=4)
            prompt = tk.Text(frame, width=32, height=3, wrap="word")
            prompt.insert("1.0", self.config.prompt)
            prompt.grid(row=3, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Keywords (optional)").grid(row=4, column=0, sticky="nw", pady=4)
            keywords = tk.Text(frame, width=32, height=3, wrap="word")
            keywords.insert("1.0", "\n".join(self.config.keywords))
            keywords.grid(row=4, column=1, sticky="ew", pady=4)

            ttk.Label(frame, text="Max seconds (0 = unlimited)").grid(row=5, column=0, sticky="w", pady=4)
            max_seconds = tk.StringVar(value=str(self.config.max_recording_seconds) if self.config.max_recording_seconds else "")
            ttk.Spinbox(frame, from_=0, to=180, textvariable=max_seconds, width=22).grid(
                row=5, column=1, sticky="ew", pady=4
            )

            device_ids: list[int | None] = [None]
            device_labels = ["Default microphone"]
            try:
                sounddevice = _load_sounddevice()
            except RuntimeError:
                sounddevice = None
            if sounddevice is not None:
                try:
                    for index, device_info in enumerate(sounddevice.query_devices()):
                        if int(device_info.get("max_input_channels", 0)) > 0:
                            device_ids.append(index)
                            device_labels.append(f"{index}: {device_info.get('name', 'Microphone')}")
                except Exception as exc:
                    log_exception("Could not enumerate microphones", exc)
            ttk.Label(frame, text="Microphone").grid(row=6, column=0, sticky="w", pady=4)
            selected_index = device_ids.index(self.config.audio_device) if self.config.audio_device in device_ids else 0
            device = tk.StringVar(value=device_labels[selected_index])
            ttk.Combobox(frame, textvariable=device, values=device_labels, state="readonly", width=21).grid(
                row=6, column=1, sticky="ew", pady=4
            )

            launch_on_login = tk.BooleanVar(value=self.config.launch_on_login)
            ttk.Checkbutton(
                frame,
                text="Launch GPT Transcribe when I sign in",
                variable=launch_on_login,
            ).grid(row=7, column=0, columnspan=2, sticky="w", pady=4)

            ttk.Label(
                frame,
                text="Audio is sent to OpenAI after you stop listening. Prompt, keywords, and language hints are optional.\nThe API key is read from OPENAI_API_KEY and never saved here.",
                foreground="#555555",
            ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 12))

            buttons = ttk.Frame(frame)
            buttons.grid(row=9, column=0, columnspan=2, sticky="e")

            def save_and_close() -> None:
                try:
                    parse_hotkey(hotkey.get())
                except ValueError as exc:
                    messagebox.showerror("Invalid settings", str(exc), parent=root)
                    return
                raw_seconds = max_seconds.get().strip()
                try:
                    seconds = 0 if not raw_seconds else max(
                        MIN_MAX_RECORDING_SECONDS,
                        min(MAX_MAX_RECORDING_SECONDS, int(raw_seconds)),
                    )
                except ValueError:
                    messagebox.showerror(
                        "Invalid settings",
                        f"Max seconds must be blank, 0, or a whole number from {MIN_MAX_RECORDING_SECONDS} to {MAX_MAX_RECORDING_SECONDS}.",
                        parent=root,
                    )
                    return
                self.config.hotkey = hotkey.get().strip().lower()
                self.config.languages = parse_setting_list(languages.get())
                self.config.prompt = prompt.get("1.0", "end-1c").strip()
                self.config.keywords = parse_setting_list(keywords.get("1.0", "end-1c"))
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
    try:
        sounddevice = _load_sounddevice()
    except RuntimeError as exc:
        print(exc)
        return 1
    try:
        print(sounddevice.query_devices())
        return 0
    except Exception as exc:
        print(f"Could not enumerate audio devices: {exc}")
        return 1


def check_installation() -> int:
    checks = {
        "Windows": sys.platform == "win32",
        "OPENAI_API_KEY present": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        "sounddevice installed": _module_available("sounddevice"),
        "pystray installed": _module_available("pystray"),
        "Pillow installed": _module_available("PIL"),
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
        configure_logging()
        try:
            set_launch_on_login(False)
            return 0
        except Exception as exc:
            log_exception("Could not remove launch-on-login setting", exc)
            return 1
    configure_logging()
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
