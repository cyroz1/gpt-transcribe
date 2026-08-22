import io
import json
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpt_transcribe import Config, build_multipart, make_wav, parse_hotkey, startup_command  # noqa: E402


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

    def test_multipart_contains_model_and_audio(self):
        body, boundary = build_multipart({"model": "gpt-transcribe"}, "dictation.wav", b"abc", "audio/wav")
        self.assertIn(b"gpt-transcribe", body)
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
