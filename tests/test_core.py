import base64
import io
import json
import os
import queue
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gpt_transcribe as core  # noqa: E402
from gpt_transcribe import (  # noqa: E402
    App,
    Config,
    RealtimeTranscriptionSession,
    build_multipart,
    build_realtime_session_update,
    make_wav,
    parse_hotkey,
    startup_command,
)


class CoreTests(unittest.TestCase):
    def test_parse_hotkey(self):
        modifiers, key = parse_hotkey("ctrl+shift+space")
        self.assertNotEqual(modifiers, 0)
        self.assertEqual(key, 0x20)

    def test_parse_hotkey_rejects_modifier_only(self):
        with self.assertRaises(ValueError):
            parse_hotkey("ctrl")

    def test_make_wav_has_expected_format(self):
        payload = make_wav(b"\x00\x00" * 800, 16_000)
        self.assertTrue(payload.startswith(b"RIFF"))
        with wave.open(io.BytesIO(payload), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getsampwidth(), 2)
            self.assertEqual(audio.getframerate(), 16_000)
            self.assertEqual(audio.getnframes(), 800)

    def test_make_wav_accepts_mutable_audio_buffer(self):
        payload = make_wav(bytearray(b"\x00\x00" * 16), 16_000)
        self.assertTrue(payload.startswith(b"RIFF"))

    def test_recording_limit_accepts_unlimited_values(self):
        self.assertEqual(Config({"max_recording_seconds": 0}).max_recording_seconds, 0)
        self.assertEqual(Config({"max_recording_seconds": ""}).max_recording_seconds, 0)
        self.assertEqual(Config({"max_recording_seconds": 4}).max_recording_seconds, 5)
        self.assertEqual(Config({"max_recording_seconds": 999}).max_recording_seconds, 180)

    def test_transcription_settings_normalize_and_keep_legacy_language(self):
        config = Config(
            {
                "prompt": "  A product support call.  ",
                "keywords": "OpenAI\nGPT Transcribe, API key",
                "language": " en, fr ",
            }
        )
        self.assertEqual(config.prompt, "A product support call.")
        self.assertEqual(config.keywords, ["OpenAI", "GPT Transcribe", "API key"])
        self.assertEqual(config.languages, ["en", "fr"])
        self.assertEqual(config.language, "en")

    def test_realtime_setting_defaults_on_and_normalizes_values(self):
        self.assertTrue(Config().realtime_transcription)
        self.assertTrue(Config({"realtime_transcription": "yes"}).realtime_transcription)
        self.assertFalse(Config({"realtime_transcription": "off"}).realtime_transcription)

    def test_realtime_session_update_uses_documented_live_model_and_context(self):
        update = build_realtime_session_update(
            Config(
                {
                    "prompt": "A support call.",
                    "keywords": ["OpenAI", "AC-42"],
                    "languages": ["en", "fr"],
                }
            )
        )
        session = update["session"]
        audio_input = session["audio"]["input"]
        self.assertEqual(update["type"], "session.update")
        self.assertEqual(session["type"], "transcription")
        self.assertEqual(audio_input["format"], {"type": "audio/pcm", "rate": 24_000})
        self.assertIsNone(audio_input["turn_detection"])
        self.assertEqual(audio_input["transcription"]["model"], core.REALTIME_TRANSCRIPTION_MODEL)
        self.assertEqual(audio_input["transcription"]["delay"], "low")
        self.assertEqual(audio_input["transcription"]["languages"], ["en", "fr"])

    def test_realtime_session_streams_audio_and_commits(self):
        class FakeTimeout(Exception):
            pass

        class FakeSocket:
            def __init__(self):
                self.events = queue.Queue()
                self.sent = []
                self.url = None
                self.headers = None
                self.closed = False

            def settimeout(self, _timeout):
                return None

            def send(self, payload):
                event = json.loads(payload)
                self.sent.append(event)
                if event.get("type") == "input_audio_buffer.commit":
                    self.events.put(
                        json.dumps(
                            {
                                "type": "conversation.item.input_audio_transcription.completed",
                                "transcript": "hello from realtime",
                            }
                        )
                    )

            def recv(self):
                try:
                    return self.events.get(timeout=0.2)
                except queue.Empty as exc:
                    raise FakeTimeout() from exc

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()

        def create_connection(url, header, timeout):
            fake_socket.url = url
            fake_socket.headers = header
            self.assertEqual(timeout, 10)
            return fake_socket

        fake_websocket = types.SimpleNamespace(
            create_connection=create_connection,
            WebSocketTimeoutException=FakeTimeout,
        )
        config = Config()
        with patch.dict(sys.modules, {"websocket": fake_websocket}):
            session = RealtimeTranscriptionSession("test-key", config)
            session.start()
            pcm = b"\x01\x00" * 120
            session.append_audio(pcm, 24_000)
            self.assertEqual(session.finish(), "hello from realtime")

        self.assertEqual(fake_socket.url, core.REALTIME_URL)
        self.assertEqual(fake_socket.headers, ["Authorization: Bearer test-key"])
        self.assertEqual(fake_socket.sent[0]["type"], "session.update")
        self.assertEqual(fake_socket.sent[0]["session"]["audio"]["input"]["transcription"]["model"], core.REALTIME_TRANSCRIPTION_MODEL)
        append_events = [event for event in fake_socket.sent if event["type"] == "input_audio_buffer.append"]
        self.assertTrue(append_events)
        self.assertEqual(base64.b64decode(append_events[0]["audio"]), pcm)
        self.assertEqual(fake_socket.sent[-1]["type"], "input_audio_buffer.commit")

    def test_unlimited_recording_does_not_start_a_timer(self):
        app = App()
        app.config.max_recording_seconds = 0
        with patch.object(core.threading, "Timer") as timer:
            app._start_recording_timer()
        timer.assert_not_called()
        self.assertIsNone(app.recording_timer)

    def test_failed_recording_is_saved_as_the_latest_wav(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": directory}):
            path = core.save_failed_recording(b"first")
            self.assertEqual(path, core.failed_recording_path())
            self.assertEqual(path.read_bytes(), b"first")
            core.save_failed_recording(b"latest")
            self.assertEqual(path.read_bytes(), b"latest")

    def test_transcription_failure_keeps_audio_for_retry(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": directory}):
            app = App()
            audio = b"RIFF failed audio"
            with patch.object(core, "transcribe_audio", side_effect=core.TranscriptionError("temporary error")):
                app._transcribe_and_paste(audio, None)
            self.assertEqual(core.failed_recording_path().read_bytes(), audio)
            self.assertEqual(app.pending_audio_path, core.failed_recording_path())
            self.assertIn("Saved recording for retry", app.status)

    def test_successful_retry_deletes_saved_audio(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": directory}):
            path = core.save_failed_recording(b"RIFF failed audio")
            app = App()
            with patch.object(core, "transcribe_audio", return_value="hello"), patch.object(core, "paste_text"):
                app._transcribe_and_paste(path.read_bytes(), None, pending_path=path)
            self.assertFalse(path.exists())
            self.assertIsNone(app.pending_audio_path)
            self.assertEqual(app.status, "Inserted transcript")

    def test_optional_runtime_modules_are_loaded_lazily(self):
        self.assertIsNone(core.sd)
        self.assertIsNone(core.pystray)
        self.assertIsNone(core.Image)

    def test_audio_callback_appends_into_one_buffer(self):
        app = App()
        app.state = "recording"
        app._audio_callback(memoryview(b"1234"), 2, None, None)
        self.assertEqual(bytes(app.audio_buffer), b"1234")

    def test_audio_status_is_logged_once_per_recording(self):
        app = App()
        app.state = "recording"
        with patch.object(core.LOGGER, "warning") as warning:
            app._audio_callback(b"12", 1, None, "overflow")
            app._audio_callback(b"34", 1, None, "overflow")
        warning.assert_called_once()

    def test_transcription_response_is_parsed_without_network(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"text":"hello"}'

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "urllib.request.urlopen", return_value=Response()
        ) as urlopen:
            self.assertEqual(core.transcribe_audio(b"audio", Config()), "hello")
        request = urlopen.call_args.args[0]
        self.assertIn(b"audio", request.data)
        self.assertIn(core.FILE_TRANSCRIPTION_MODEL.encode(), request.data)

    def test_transcription_context_uses_documented_multipart_fields(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"text":"hello"}'

        config = Config(
            {
                "prompt": "A support call.",
                "keywords": ["OpenAI", "AC-42"],
                "languages": ["en", "fr"],
            }
        )
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch(
            "urllib.request.urlopen", return_value=Response()
        ) as urlopen:
            core.transcribe_audio(b"audio", config)
        body = urlopen.call_args.args[0].data
        self.assertIn(b'name="prompt"', body)
        self.assertEqual(body.count(b'name="keywords[]"'), 2)
        self.assertEqual(body.count(b'name="languages[]"'), 2)

    def test_multipart_contains_model_and_audio(self):
        body, boundary = build_multipart({"model": core.FILE_TRANSCRIPTION_MODEL}, "dictation.wav", b"abc", "audio/wav")
        self.assertIn(core.FILE_TRANSCRIPTION_MODEL.encode(), body)
        self.assertIn(b"dictation.wav", body)
        self.assertIn(b"abc", body)
        self.assertIn(boundary.encode(), body)

    def test_launch_on_login_setting_normalizes_values(self):
        self.assertFalse(Config().launch_on_login)
        self.assertTrue(Config({"launch_on_login": True}).launch_on_login)
        self.assertTrue(Config({"launch_on_login": "yes"}).launch_on_login)
        self.assertFalse(Config({"launch_on_login": "false"}).launch_on_login)

    def test_launch_on_login_setting_persists(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"APPDATA": directory}):
            Config({"launch_on_login": True}).save()
            with open(Path(directory) / "GPTTranscribe" / "config.json", encoding="utf-8") as handle:
                payload = json.load(handle)
        self.assertTrue(payload["launch_on_login"])

    def test_startup_command_targets_current_app(self):
        command = startup_command()
        self.assertIn("gpt_transcribe.py", command)


if __name__ == "__main__":
    unittest.main()
