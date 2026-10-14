"""Runs without a Habbo client. Optional screenshot fixture from development."""
import unittest
from PIL import Image, ImageDraw
from bubble_ocr import Bubble, BubbleTracker, find_bubble_boxes


def fake_image():
    im = Image.new('RGB', (550, 250), (43, 63, 71))
    d = ImageDraw.Draw(im)
    d.rectangle((60, 34, 400, 57), fill=(219, 219, 219))
    d.rectangle((110, 76, 502, 99), fill=(118, 208, 122))
    d.rectangle((30, 116, 480, 141), fill=(151, 233, 155))
    return im


class BubblesTest(unittest.TestCase):
    def test_detect_three_rows(self):
        found = find_bubble_boxes(fake_image())
        self.assertEqual(len(found), 3)
    def test_no_old_chat(self):
        t = BubbleTracker()
        a = Bubble('auroral: hello there', 100, 10, 170, 30)
        self.assertEqual(t.update([a], 10)[0], [])
        b = Bubble('WheelieKid: hi auroral', 120, 55, 180, 30)
        self.assertEqual(t.update([a,b], 10.5)[0], [])
        self.assertEqual(t.update([a,b], 11)[0], ['WheelieKid: hi auroral'])
        self.assertEqual(t.update([a,b], 12)[0], [])
        # Bubbles moving vertically should not be replayed.
        c = Bubble(b.line, 150, 90, 180, 30)
        self.assertEqual(t.update([a,c], 13)[0], [])
    def test_repeat_after_disappears(self):
        t=BubbleTracker(repeat_cooldown=6)
        msg=Bubble('auroral: hello',120,10,180,30)
        t.update([],0)
        t.update([msg],1)
        self.assertEqual(t.update([msg],2)[0], ['auroral: hello'])
        t.update([],3)
        t.update([],5)
        t.update([msg],8)
        self.assertEqual(t.update([msg],9)[0], ['auroral: hello'])
    def test_ignores_one_frame_flicker(self):
        t=BubbleTracker()
        t.update([],0)
        msg=Bubble('auroral: random OCR',100,10,120,22)
        t.update([msg],1)
        t.update([],2)
        self.assertEqual(t.update([msg],3)[0], [])
        self.assertEqual(t.update([msg],4)[0], [msg.line])

if __name__ == '__main__': unittest.main()
