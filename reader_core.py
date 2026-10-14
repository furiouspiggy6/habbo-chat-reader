"""Chat-only OCR filtering and cautious incremental tracking for Habbo.

This module is intentionally standard-library-only so it can be tested without
Windows, Habbo, OCR, or a speech engine.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

# OCR frequently picks up random bits of avatars/furniture to the left of chat:
#     "ce 7 m ® auroral: ur back in the corner"
# Find the sender immediately before its colon, then DISCARD everything before it.
# Habbo usernames can include hyphens, underscores, periods, and exclamation marks.
NAME = r"[A-Za-z0-9][A-Za-z0-9_.!~-]{1,23}"
SPEAKER_RE = re.compile(rf"(?<![A-Za-z0-9_.!~-])(?P<name>{NAME})\s*:\s+(?=\S)")
TIME_RE = re.compile(r"^\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*")
UI_NAMES = {"chat", "chat history", "say", "whisper", "shout", "send", "search", "type here", "status"}
UI_LINES = UI_NAMES | {"settings", "friends", "navigator", "inventory", "messages"}


def _normalise(line: str) -> str:
    line = re.sub(r"[\u200b\u200e\u200f]", "", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip(" \t\r|¦•·_–—")


def _valid_text(message: str) -> bool:
    # Never speak standalone graphics, punctuation, or a 1-letter OCR speck.
    if not 2 <= len(message) <= 300:
        return False
    # A short numeric message like "5'9-10" is real chat, but OCR noise made
    # of random symbols shouldn't be read aloud.
    if re.fullmatch(r"[0-9 '+:.,/\-]+", message):
        return sum(ch.isdigit() for ch in message) >= 2
    return (sum(ch.isalnum() for ch in message) >= 2
            and sum(ch.isalpha() for ch in message) >= 1)


def clean_lines(raw: str) -> list[str]:
    """Extract only confidently *formatted* 'username: message' entries.

    Ignores bare OCR noise and chat continuation fragments. This may sometimes
    omit a message, but does not read a screenful of game graphics aloud.
    """
    result: list[str] = []
    for raw_line in raw.splitlines():
        line = _normalise(TIME_RE.sub("", raw_line))
        if not line or line.casefold() in UI_LINES:
            continue
        matches = list(SPEAKER_RE.finditer(line))
        if not matches:
            continue
        for index, hit in enumerate(matches):
            username = hit.group("name")
            if username.casefold() in UI_NAMES:
                continue
            end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
            body = _normalise(line[hit.end():end])
            # Stray icon glyphs and decorative separators sometimes intrude on
            # either side of speech but are not a reason to read the full line.
            body = body.strip(" \t|¦•·_–—®©&@*")
            if _valid_text(body):
                result.append(f"{username}: {body[:300]}")
    return result


def split_sender(line: str) -> tuple[str | None, str]:
    match = SPEAKER_RE.match(line)
    if match:
        return match.group("name"), line[match.end():].strip()
    return None, _normalise(TIME_RE.sub("", line))


def _fold(text: str) -> str:
    """Ignore OCR differences in punctuation, repeated spaces and capitals."""
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def key_for(line: str) -> str:
    user, message = split_sender(line)
    return (_fold(user) + ":" if user else "") + _fold(message)


def is_same_message(a: str, b: str) -> bool:
    """Be forgiving of a little OCR jitter without merging unlike short chats."""
    user_a, text_a = split_sender(a)
    user_b, text_b = split_sender(b)
    if user_a is None or user_b is None:
        return key_for(a) == key_for(b)
    name_a, name_b = _fold(user_a), _fold(user_b)
    if name_a != name_b:
        if min(len(name_a), len(name_b)) < 5:
            return False
        if SequenceMatcher(None, name_a, name_b).ratio() < 0.90:
            return False
    body_a, body_b = _fold(text_a), _fold(text_b)
    if body_a == body_b:
        return True
    if min(len(body_a), len(body_b)) < 5:
        return False
    ratio = SequenceMatcher(None, body_a, body_b).ratio()
    # Short, similar actual messages must not be merged accidentally.
    threshold = 0.94 if min(len(body_a), len(body_b)) < 17 else 0.86
    return ratio >= threshold


def overlap_start(previous: list[str], current: list[str]) -> int:
    """Index after latest identifiable old message; -1 if no reliable anchor.

    This is deliberately more conservative than treating every unfamiliar
    OCR line as a newly arrived message. Old chat is NEVER played on a jump.
    """
    if not previous or not current:
        return -1
    # Last visible old messages make the best tail anchors when chat moves up.
    for old_i in range(len(previous) - 1, max(-1, len(previous) - 4), -1):
        for new_i in range(len(current) - 1, -1, -1):
            if is_same_message(previous[old_i], current[new_i]):
                # If the two lines share an anchor, the only candidates to be
                # new are later in the CURRENT view.
                return new_i + 1
    return -1


def spoken_line(line: str, *, speak_names: bool, muted_users: Iterable[str],
                own_name: str = "", mute_own: bool = False) -> str | None:
    sender, message = split_sender(line)
    # For safety, never read OCR lines with no proper sender tag.
    if not sender or not _valid_text(message):
        return None
    muted = {x.strip().casefold() for x in muted_users if x.strip()}
    if sender.casefold() in muted:
        return None
    if mute_own and own_name.strip() and sender.casefold() == own_name.strip().casefold():
        return None
    if speak_names:
        return f"{sender} says, {message}"
    return message


@dataclass
class Update:
    visible: list[str]
    new_lines: list[str]
    primed: bool = False


@dataclass
class _Pending:
    line: str
    count: int
    last_frame: int


class ChatTracker:
    """Track additions rather than every changed line on the screen.

    - Baselines pre-existing chat on launch / after unreadable or jumped view.
    - Requires a new message to appear in TWO successive readable OCR frames.
    - Remembers already seen chat for ten minutes to survive OCR fluctuations.
    """

    def __init__(self, duplicate_window: float = 600.0):
        self.duplicate_window = duplicate_window
        self.previous: list[str] = []
        self.started = False
        self.recent: list[tuple[str, float]] = []
        self.pending: list[_Pending] = []
        self.frame = 0

    def reset(self) -> None:
        self.previous.clear()
        self.recent.clear()
        self.pending.clear()
        self.frame = 0
        self.started = False

    def _is_recent(self, line: str) -> bool:
        return any(is_same_message(old, line) for old, _ in self.recent)

    def _mark_seen(self, line: str, timestamp: float):
        if not self._is_recent(line):
            self.recent.append((line, timestamp))

    def update(self, raw: str, now: float | None = None) -> Update:
        timestamp = now if now is not None else time.monotonic()
        lines = clean_lines(raw)
        self.frame += 1
        self.recent = [(line, when) for line, when in self.recent
                       if timestamp - when <= self.duplicate_window][-350:]
        if not lines:
            # OCR sometimes sees nothing. Don't turn a brief failure into a
            # 'new chat' event on the next frame.
            return Update([], [])
        if not self.started:
            self.started = True
            self.previous = lines
            for line in lines:
                self._mark_seen(line, timestamp)
            return Update(lines, [], primed=True)

        anchor = overlap_start(self.previous, lines)
        candidates = lines[anchor:] if anchor >= 0 else []
        spoken: list[str] = []
        new_pending: list[_Pending] = []

        # First, advance candidates from a previous frame. The candidate may
        # already be in 'previous', so can't be found using new tail alone.
        for p in self.pending:
            observed = next((line for line in lines if is_same_message(line, p.line)), None)
            if observed is None or self._is_recent(observed):
                continue
            if p.last_frame == self.frame - 1:
                p.count += 1
            p.line = observed
            p.last_frame = self.frame
            if p.count >= 2:
                spoken.append(observed)
                self._mark_seen(observed, timestamp)
            else:
                new_pending.append(p)
        self.pending = new_pending

        for candidate in candidates:
            if self._is_recent(candidate):
                continue
            if any(is_same_message(p.line, candidate) for p in self.pending):
                continue
            self.pending.append(_Pending(candidate, 1, self.frame))

        # A changed/cropped/scroll-jumped view must be baselined, not read.
        if anchor == -1:
            for line in lines:
                if not any(is_same_message(p.line, line) for p in self.pending):
                    self._mark_seen(line, timestamp)
        self.previous = lines
        return Update(lines, spoken)
