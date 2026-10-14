import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch, Mock
import numpy as np
import local_tts as local
from speaker_voices import VoiceRoster

class LocalSpeechTests(unittest.TestCase):
    def test_twenty_distinct_speakers_and_saved_override(self):
        roster = VoiceRoster()
        choices = [v for _,v in local.LOCAL_VOICES]
        self.assertEqual(len(set(choices)), 20)
        assigned = [roster.choose('user'+str(i), 'local', choices) for i in range(20)]
        self.assertEqual(len(set(assigned)),20)
        roster.set_override('user0', 'local', choices[4], choices)
        loaded = VoiceRoster(roster.dump())
        self.assertEqual(loaded.choose('USER0', 'local', choices), choices[4])
        loaded.clear_override('user0', 'local')
        self.assertEqual(loaded.choose('user0', 'local', choices), assigned[0])

    def test_british_vs_american_language(self):
        self.assertEqual(local.voice_language('bf_emma'), 'en-gb')
        self.assertEqual(local.voice_language('bm_george'), 'en-gb')
        self.assertEqual(local.voice_language('am_adam'), 'en-us')

    def test_missing_model_does_not_access_network(self):
        with tempfile.TemporaryDirectory() as d, patch.object(local, 'model_folder', return_value=Path(d)):
            self.assertFalse(local.has_model(local.DEFAULT_MODEL))
            self.assertEqual(len(local.missing_assets(local.DEFAULT_MODEL)), 2)

    def test_download_fails_without_marking_partial_file_ready(self):
        with tempfile.TemporaryDirectory() as d, patch.object(local, 'model_folder', return_value=Path(d)):
            fake = Mock()
            fake.headers = {'Content-Length': '5000'}
            fake.__enter__ = Mock(return_value=fake)
            fake.__exit__ = Mock(return_value=None)
            with patch.object(local.urllib.request, 'urlopen', return_value=fake):
                with self.assertRaises(local.SpeechError):
                    local._download_file('testfile.onnx', 9000, lambda _: None)
            self.assertFalse((Path(d) / 'testfile.onnx').exists())

    def test_pcm_wav_has_nonempty_speech(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'test.wav'
            local._write_pcm(target, np.sin(np.linspace(0, 30, 24000)), 24000)
            self.assertGreater(local._validate_wav(str(target)), 0.9)

    def test_local_voice_generates_pcm_then_calls_windows_player(self):
        class FakeEngine:
            def create(self, text, *, voice, speed, lang):
                self.params = (text, voice, speed, lang)
                return np.ones(10000, dtype=np.float32)*0.05, 24000
        fake = FakeEngine()
        finished = Mock(returncode=0, stdout='', stderr='')
        with patch.object(local.sys, 'platform', 'win32'), \
             patch.object(local, '_engine', return_value=fake), \
             patch.object(local.subprocess, 'run', return_value=finished) as run:
            log = []
            local.speak_local('hello from Habbo!', voice='bf_emma', notify=log.append)
        self.assertEqual(fake.params[0], 'hello from Habbo!')
        self.assertEqual(fake.params[-1], 'en-gb')
        self.assertEqual(run.call_args.args[0][2], '--play-wav')
        self.assertTrue(any('Playing local voice' in note for note in log))

    def test_offline_only_no_cloud_dependencies(self):
        contents = Path(__file__).with_name('habbo_reader.py').read_text()
        self.assertNotIn('speak_natural(', contents)
        self.assertNotIn('speak_premium(', contents)
        self.assertNotIn('api_key', contents)

if __name__ == '__main__':
    unittest.main()
