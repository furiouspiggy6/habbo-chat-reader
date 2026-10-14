"""Cross-platform regression coverage for v8.1 Windows audio flag handling."""
import io
import math
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import wave

import speech_backend as backend


class FakeWinSound(types.ModuleType):
    SND_FILENAME = 0x00020000
    SND_MEMORY = 0x0004
    SND_NODEFAULT = 0x0002
    SND_ASYNC = 0x0001

    def __init__(self):
        super().__init__('winsound')
        self.calls = []

    def PlaySound(self, sound, flags):
        self.calls.append((sound, flags))


class WindowsAudioRegressionTests(unittest.TestCase):
    def test_beep_is_played_synchronously_and_has_valid_wave_header(self):
        fake = FakeWinSound()
        with patch.object(backend.sys, 'platform', 'win32'), patch.dict(sys.modules, {'winsound': fake}):
            backend.play_test_tone()
        self.assertEqual(len(fake.calls), 1)
        sound, flags = fake.calls[0]
        self.assertIsInstance(sound, bytes)
        self.assertTrue(flags & fake.SND_MEMORY)
        self.assertTrue(flags & fake.SND_NODEFAULT)
        self.assertFalse(flags & fake.SND_ASYNC)
        with wave.open(io.BytesIO(sound), 'rb') as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertGreater(w.getnframes(), 1000)

    def test_local_wav_player_uses_documented_flags(self):
        fake = FakeWinSound()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'voice.wav'
            with wave.open(str(p), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(struct.pack('<h', 1000) * 4800)
            with patch.object(backend.sys, 'platform', 'win32'), patch.dict(sys.modules, {'winsound': fake}):
                backend._play_wav_builtin(str(p))
        self.assertEqual(len(fake.calls), 1)
        sound, flags = fake.calls[0]
        self.assertEqual(sound, str(p))
        self.assertTrue(flags & fake.SND_FILENAME)
        self.assertTrue(flags & fake.SND_NODEFAULT)
        self.assertFalse(flags & fake.SND_ASYNC)


if __name__ == '__main__':
    unittest.main()
