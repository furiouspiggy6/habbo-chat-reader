"""Segment Habbo browser speech bubbles and OCR each bubble individually.

No browser injection/network, screenshots saved or uploaded. Pure local processing.
Only fairly bright, rectangular Habbo speech bubbles pass the geometric filter;
extracted text must contain a valid `username: message` pattern.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from hashlib import blake2b
from time import monotonic
import re

import cv2
import numpy as np
from PIL import Image
import pytesseract

from reader_core import clean_lines


@dataclass(frozen=True)
class Bubble:
    line: str
    x: int
    y: int
    width: int
    height: int


def find_bubble_boxes(image: Image.Image) -> list[tuple[int, int, int, int]]:
    """Find separate flat-filled message boxes rather than one room-wide OCR block.

    The 3x3 erosion breaks small contacts between adjacent chat bubbles,
    then horizontal closing fills gaps through the text. Coordinates relative
    to the input image; clipping bubbles at the selection edge is avoided.
    """
    rgb = np.asarray(image.convert("RGB"))
    if rgb.size == 0:
        return []
    red, green, blue = [rgb[:, :, c].astype(np.int16) for c in range(3)]
    mx = np.maximum.reduce([red, green, blue])
    mn = np.minimum.reduce([red, green, blue])

    neutral = (mx - mn < 22) & (mn > 175)
    bright_green = (green > red + 32) & (green > blue + 29) & (green > 158) & (red > 65)
    # Other bright Habbo bubble themes; validate by geometry + username: text.
    bright_blue = (blue > red + 38) & (blue > green + 12) & (blue > 178) & (green > 95)
    bright_pink = (red > green + 38) & (blue > green + 15) & (red > 185) & (blue > 108)
    bright_yellow = (red > blue + 56) & (green > blue + 37) & (red > 182) & (green > 145)
    mask = (neutral | bright_green | bright_blue | bright_pink | bright_yellow).astype(np.uint8) * 255
    mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)), iterations=1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 1)))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    boxes = []
    for x, y, width, height, filled in stats[1:]:
        x, y, width, height, filled = map(int, (x, y, width, height, filled))
        if width < 80 or width > min(image.width, 1100):
            continue
        if not (13 <= height <= 78) or filled < 350:
            continue
        if filled / (width * height) < 0.22:
            continue
        # Partially cropped bubbles are unreliable and cannot have full text.
        if x < 3 or y < 3 or x + width > image.width - 3 or y + height > image.height - 3:
            continue
        boxes.append((x, y, width, height))
    # Habbo stacks chat from top to bottom; reading order follows screen order.
    return sorted(boxes, key=lambda box: (box[1], box[0]))


def bubble_text_image(image: Image.Image, box: tuple[int, int, int, int]) -> np.ndarray:
    """Exclude left avatar icon, bubble outline and tail; isolate dark pixel letters."""
    x, y, width, height = box
    if width < 45:
        return np.empty((0, 0), dtype=np.uint8)
    crop = np.asarray(image.convert("RGB").crop((x + 27, max(0, y - 2), x + width - 4, min(image.height, y + height + 3))))
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    enlarged = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    # Habbo uses extremely dark lettering on light/green speech bubbles.
    # A fixed threshold keeps the OCR input consistent across bubble colours.
    binary = cv2.threshold(enlarged, 105, 255, cv2.THRESH_BINARY)[1]
    if np.median(gray) < 100:  # Dark bubble: white lettering.
        binary = 255 - binary
    return binary


class BubbleReader:
    """Cache unchanged bubble images so each frame scans fast after first scan."""

    def __init__(self, max_cache: int = 600):
        self.max_cache = max_cache
        self.cache: OrderedDict[bytes, str] = OrderedDict()

    def read(self, image: Image.Image) -> tuple[list[Bubble], int]:
        bubbles = []
        ocr_calls = 0
        for box in find_bubble_boxes(image):
            binary = bubble_text_image(image, box)
            if binary.size == 0:
                continue
            signature = blake2b(binary.tobytes(), digest_size=12).digest()
            if signature in self.cache:
                line = self.cache[signature]
                self.cache.move_to_end(signature)
            else:
                try:
                    raw = pytesseract.image_to_string(binary, lang="eng", config=f"--oem 3 --psm {6 if box[3] > 42 else 7}", timeout=3).strip()
                    # Pixel font sometimes inserts a gap in numerical username endings.
                    # e.g. xhabbox-1 44: -> xhabbox-144:
                    raw = re.sub(r"(?<=\d) +(?=\d+:)", "", raw)
                except RuntimeError:
                    continue
                lines = clean_lines(raw)
                line = lines[0] if lines else ""
                self.cache[signature] = line
                ocr_calls += 1
                if len(self.cache) > self.max_cache:
                    self.cache.popitem(last=False)
            if line:
                bubbles.append(Bubble(line, *box))
        return bubbles, ocr_calls


class BubbleTracker:
    """Speech-bubble lifecycle tracker. Never replays the whole room.

    Baselines everything visible on Start. Subsequent newly visible messages
    must appear in two frames to confirm; duplicates currently on screen never
    replay, even if they shift as more bubbles arrive. An identical text can
    recur after the old instance has vanished for at least 6 seconds.
    """

    def __init__(self, repeat_cooldown: float = 6.0):
        self.repeat_cooldown = repeat_cooldown
        self.started = False
        self.visible: dict[str, tuple[float, float]] = {}
        self.pending: dict[str, int] = {}
        self.recently_spoken: dict[str, float] = {}

    def reset(self) -> None:
        self.started = False
        self.visible.clear()
        self.pending.clear()
        self.recently_spoken.clear()

    @staticmethod
    def _key(line: str) -> str:
        from reader_core import key_for
        return key_for(line)

    def update(self, bubbles: list[Bubble], now: float | None = None) -> tuple[list[str], bool]:
        now = monotonic() if now is None else now
        # If the same text occurs twice at the same moment, speak just one of
        # them; username is part of the key.
        present = {self._key(b.line): b.line for b in bubbles}
        keys = set(present)
        if not self.started:
            self.started = True
            self.visible = {key: (now, now) for key in keys}
            return [], True

        # Track continuously visible messages independent of their screen x/y.
        for key in list(self.visible):
            first, last = self.visible[key]
            if key in keys:
                self.visible[key] = (first, now)
            elif now - last > 1.0:
                del self.visible[key]
        for key, when in list(self.recently_spoken.items()):
            if now - when > 90:
                del self.recently_spoken[key]
        for key in list(self.pending):
            if key not in keys:
                del self.pending[key]

        emitted = []
        for bubble in bubbles:
            key = self._key(bubble.line)
            if key in self.visible:
                continue
            last = self.recently_spoken.get(key, -1e12)
            if now - last < self.repeat_cooldown:
                self.visible[key] = (now, now)
                continue
            count = self.pending.get(key, 0) + 1
            self.pending[key] = count
            if count >= 2:
                emitted.append(bubble.line)
                self.visible[key] = (now, now)
                self.recently_spoken[key] = now
                del self.pending[key]
        return emitted, False
