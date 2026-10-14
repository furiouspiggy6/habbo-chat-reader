"""Run: python test_reader_core.py  (doesn't require Habbo/Windows)."""
import unittest
from reader_core import clean_lines, spoken_line, ChatTracker, is_same_message


class CoreTests(unittest.TestCase):
    def test_filter_sample_noise_and_extract_speakers(self):
        sample = '''tine, chee ee ® auroral: ur back in the corner
hor _ : -G Kublhe wenlon nore ®* auroral: what brings u back zip
mat ABs | . SWAT-POLICE: Stepssisy goes to meet the homeless people and gives them a silver
eee =| coin
ag eh or As =S
Lal
3 .* a stepsissy: poor guy & auroral: aye maker
'''
        self.assertEqual(clean_lines(sample), [
            'auroral: ur back in the corner',
            'auroral: what brings u back zip',
            'SWAT-POLICE: Stepssisy goes to meet the homeless people and gives them a silver',
            'stepsissy: poor guy', 'auroral: aye maker'])

    def test_ignore_unrecognizable_graphics(self):
        self.assertEqual(clean_lines('''=. = a - BS\ncoin\n| ££ .---\n: hello\n'''), [])
        self.assertIsNone(spoken_line('Some nonsense', speak_names=True, muted_users=[]))

    def test_speak_and_mute(self):
        self.assertEqual(spoken_line('zip-line: hi!', speak_names=True, muted_users=[]), 'zip-line says, hi!')
        self.assertEqual(spoken_line('zip-line: hi!', speak_names=False, muted_users=[]), 'hi!')
        self.assertIsNone(spoken_line('zip-line: hi!', speak_names=True, muted_users=['ZIP-LINE']))
        self.assertIsNone(spoken_line('zip-line: hi!', speak_names=True, muted_users=[], own_name='zip-line', mute_own=True))

    def test_ocr_noise_matches_similar_message(self):
        self.assertTrue(is_same_message('auroral: ur back in the corner', 'auroral: ur back in corner'))
        self.assertFalse(is_same_message('auroral: bye', 'auroral: hey'))

    def test_prime_then_confirm_and_suppress(self):
        t = ChatTracker()
        old = 'auroral: aye zip\nzip-line: not for long'
        new = old + '\nstepsissy: what did he do'
        self.assertTrue(t.update(old, now=1).primed)
        self.assertEqual(t.update(old, now=2).new_lines, [])
        self.assertEqual(t.update(new, now=3).new_lines, [])
        self.assertEqual(t.update(new, now=4).new_lines, ['stepsissy: what did he do'])
        self.assertEqual(t.update(new, now=5).new_lines, [])
        self.assertEqual(t.update(new + '\nBAD -*&', now=6).new_lines, [])
        # Stale old chat must not replay after OCR glitches/jumps
        self.assertEqual(t.update('auroral: aye zip', now=7).new_lines, [])
        self.assertEqual(t.update(new, now=8).new_lines, [])

    def test_multiple_new_entries_spoken_in_order(self):
        t = ChatTracker()
        t.update('auroral: one message', 1)
        text='auroral: one message\nKubl: two words here\nstepsissy: hello world'
        self.assertEqual(t.update(text, 2).new_lines, [])
        self.assertEqual(t.update(text, 3).new_lines, ['Kubl: two words here', 'stepsissy: hello world'])

    def test_single_frame_artifact_never_spoken(self):
        t = ChatTracker()
        t.update('auroral: normal message', 1)
        t.update('auroral: normal message\nFakeName: temporary OCR ghost', 2)
        self.assertEqual(t.update('auroral: normal message', 3).new_lines, [])
        self.assertEqual(t.update('auroral: normal message', 4).new_lines, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
