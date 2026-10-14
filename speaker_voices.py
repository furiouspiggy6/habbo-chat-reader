"""Deterministic, per-username voice assignments, without network dependencies."""
from __future__ import annotations

from dataclasses import dataclass

# A curated English-only palette of understandable Microsoft neural voices.
# Natural mode needs internet and the optional edge-tts package.
NATURAL_VOICES: list[tuple[str, str]] = [
    ("Natasha · Australian woman", "en-AU-NatashaNeural"),
    ("William · Australian man", "en-AU-WilliamNeural"),
    ("Sonia · British woman", "en-GB-SoniaNeural"),
    ("Ryan · British man", "en-GB-RyanNeural"),
    ("Jenny · American woman", "en-US-JennyNeural"),
    ("Guy · American man", "en-US-GuyNeural"),
    ("Aria · American woman", "en-US-AriaNeural"),
    ("Davis · American man", "en-US-DavisNeural"),
    ("Libby · British woman", "en-GB-LibbyNeural"),
    ("Thomas · British man", "en-GB-ThomasNeural"),
    ("Michelle · American woman", "en-US-MichelleNeural"),
    ("Christopher · American man", "en-US-ChristopherNeural"),
]

NATURAL_ENGINE = "Natural voices (online)"
OFFLINE_ENGINE = "Windows voices (offline)"


def normalized_speaker(name: str) -> str:
    return name.strip().casefold()


class VoiceRoster:
    """Tracks stable automatic voice choices and manual preferences per engine.

    The first N speakers get distinct voices, if N voices are available.
    With more speakers than voices, reuse them round-robin. Speech is kept
    stable when the speaker returns or when the reader restarts.
    """

    def __init__(self, saved: dict | None = None):
        self.allocations: dict[str, dict[str, str]] = {"natural": {}, "offline": {}, "premium": {}, "local": {}}
        self.overrides: dict[str, dict[str, str]] = {"natural": {}, "offline": {}, "premium": {}, "local": {}}
        if isinstance(saved, dict):
            for section, destination in (("allocations", self.allocations), ("overrides", self.overrides)):
                data = saved.get(section)
                if not isinstance(data, dict):
                    continue
                for engine in destination:
                    values = data.get(engine)
                    if isinstance(values, dict):
                        destination[engine] = {
                            normalized_speaker(k): v for k, v in values.items()
                            if isinstance(k, str) and isinstance(v, str) and k.strip() and v
                        }

    def choose(self, username: str, engine: str, available: list[str]) -> str:
        if not available:
            return ""
        if engine not in self.allocations:
            raise ValueError("Unknown voice engine")
        speaker = normalized_speaker(username)
        if not speaker:
            return available[0]
        manual = self.overrides[engine].get(speaker)
        if manual in available:
            return manual
        saved = self.allocations[engine].get(speaker)
        if saved in available:
            return saved
        # Allocate a currently unused voice first. Reuse only after every
        # available voice is assigned, preserving old saved choices.
        used = set(self.allocations[engine].values()) | set(self.overrides[engine].values())
        selected = next((voice for voice in available if voice not in used), None)
        if selected is None:
            selected = available[len(self.allocations[engine]) % len(available)]
        self.allocations[engine][speaker] = selected
        return selected

    def set_override(self, username: str, engine: str, voice: str, available: list[str]):
        if not normalized_speaker(username):
            raise ValueError("Enter a username")
        if engine not in self.overrides or voice not in available:
            raise ValueError("Choose a voice from the list")
        self.overrides[engine][normalized_speaker(username)] = voice

    def clear_override(self, username: str, engine: str):
        self.overrides[engine].pop(normalized_speaker(username), None)

    def dump(self) -> dict:
        return {"allocations": {k: dict(v) for k, v in self.allocations.items()},
                "overrides": {k: dict(v) for k, v in self.overrides.items()}}


def display_name(voice_id: str, voices: list[tuple[str, str]]) -> str:
    return next((label for label, id_ in voices if id_ == voice_id), voice_id)
