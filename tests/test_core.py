import io
import sys
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpt_transcribe import build_multipart, make_wav, parse_hotkey  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
