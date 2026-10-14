"""Completely local Kokoro 82M neural speech for Windows.

No external speech endpoints, accounts, API keys or chat transmission.
Only the initial ONNX model and reusable voice embeddings are downloaded.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
from typing import Callable

from speech_backend import SpeechError, _validate_wav

LOCAL_ENGINE = "Kokoro realistic voices (local)"
WINDOWS_ENGINE = "Windows voices (offline)"

# Verified assets published by the kokoro-onnx project.
ASSET_ROOT = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
MODELS = {
    "Quality (recommended · 164 MB)": ("kokoro-v1.0.fp16.onnx", 145_000_000),
    "Fast (lighter · 114 MB)": ("kokoro-v1.0.int8.onnx", 98_000_000),
}
DEFAULT_MODEL = next(iter(MODELS))
VOICES_FILE = "voices-v1.0.bin"
VOICES_MIN_BYTES = 24_000_000

# Stable selection order: 20 distinctive voices, with British and American accents.
LOCAL_VOICES = [
    ("Heart · American female", "af_heart"),
    ("Adam · American male", "am_adam"),
    ("Emma · British female", "bf_emma"),
    ("George · British male", "bm_george"),
    ("Bella · American female", "af_bella"),
    ("Michael · American male", "am_michael"),
    ("Alice · British female", "bf_alice"),
    ("Lewis · British male", "bm_lewis"),
    ("Nicole · American female", "af_nicole"),
    ("Eric · American male", "am_eric"),
    ("Lily · British female", "bf_lily"),
    ("Daniel · British male", "bm_daniel"),
    ("Nova · American female", "af_nova"),
    ("Liam · American male", "am_liam"),
    ("Isabella · British female", "bf_isabella"),
    ("Fable · British male", "bm_fable"),
    ("Sarah · American female", "af_sarah"),
    ("Echo · American male", "am_echo"),
    ("Jessica · American female", "af_jessica"),
    ("Puck · American male", "am_puck"),
]


def model_folder() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".cache"))
    return base / "HabboChatReader" / "models"


def model_path(model: str) -> Path:
    if model not in MODELS:
        raise SpeechError(f"Unknown local model: {model}")
    return model_folder() / MODELS[model][0]


def voices_path() -> Path:
    return model_folder() / VOICES_FILE


def has_model(model: str = DEFAULT_MODEL) -> bool:
    try:
        filename, size = MODELS[model]
        return (model_folder() / filename).stat().st_size >= size and voices_path().stat().st_size >= VOICES_MIN_BYTES
    except (OSError, KeyError):
        return False


def missing_assets(model: str = DEFAULT_MODEL) -> list[tuple[str, int]]:
    if model not in MODELS:
        raise SpeechError(f"Unknown local model: {model}")
    file, minimum = MODELS[model]
    missing = []
    for name, cutoff in ((file, minimum), (VOICES_FILE, VOICES_MIN_BYTES)):
        try:
            good = (model_folder() / name).stat().st_size >= cutoff
        except OSError:
            good = False
        if not good:
            missing.append((name, cutoff))
    return missing


def _download_file(name: str, minimum: int, notify: Callable[[str], None]) -> None:
    folder = model_folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    partial = folder / (name + ".part")
    url = f"{ASSET_ROOT}/{name}"
    notify(f"Downloading {name} (one-time setup)...")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "HabboReaderLocal/8.0"})
        with urllib.request.urlopen(request, timeout=35) as response, partial.open("wb") as out:
            expected = int(response.headers.get("Content-Length", 0) or 0)
            if expected and expected < minimum:
                raise SpeechError(f"Download server returned a too-small asset for {name}.")
            count = 0
            checkpoint = -1
            while True:
                block = response.read(1024 * 512)
                if not block:
                    break
                out.write(block)
                count += len(block)
                step = count // (20 * 1024 * 1024)
                if step > checkpoint:
                    checkpoint = step
                    if expected:
                        notify(f"{name}: {count / 1e6:.0f} / {expected / 1e6:.0f} MB")
                    else:
                        notify(f"{name}: {count / 1e6:.0f} MB downloaded")
        if partial.stat().st_size < minimum:
            raise SpeechError(f"Downloaded {name} is incomplete. Check the internet connection and retry.")
        # Rename only a verified complete download, so a failed setup can't look ready.
        partial.replace(target)
        notify(f"Saved {name}.")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise SpeechError(f"Unable to download {name}: {exc}") from exc
    finally:
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass


def download_assets(model: str = DEFAULT_MODEL, notify: Callable[[str], None] | None = None) -> None:
    """Explicit opt-in download. Never called when merely speaking."""
    notify = notify or (lambda _: None)
    for name, minimum in missing_assets(model):
        _download_file(name, minimum, notify)
    if not has_model(model):
        raise SpeechError("Local model files are missing or incomplete after setup.")


_engine_lock = threading.Lock()
_cached_engine = None
_cached_model_path = None


def _engine(model: str, announce: Callable[[str], None]):
    global _cached_engine, _cached_model_path
    if not has_model(model):
        raise SpeechError("Local model not installed. Click 'Download local voices' first.")
    path = model_path(model)
    if _cached_engine is None or _cached_model_path != path:
        announce("Loading local Kokoro model (first message can take a few seconds)...")
        try:
            from kokoro_onnx import Kokoro
            _cached_engine = Kokoro(str(path), str(voices_path()))
            _cached_model_path = path
        except Exception as exc:
            raise SpeechError(f"Could not load Kokoro model: {type(exc).__name__}: {exc}") from exc
    return _cached_engine


def voice_language(voice_id: str) -> str:
    return "en-gb" if voice_id.startswith(("bf_", "bm_")) else "en-us"


def _write_pcm(path: Path, samples, sample_rate: int) -> None:
    import numpy as np
    a = np.asarray(samples).flatten()
    if a.size < 200 or not (8000 <= sample_rate <= 48000):
        raise SpeechError("Local voice engine returned no usable audio.")
    if not np.isfinite(a).all():
        raise SpeechError("Local voice engine returned invalid audio samples.")
    pcm = (np.clip(a.astype(np.float32), -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(pcm.tobytes())
    _validate_wav(str(path))


def speak_local(text: str, *, voice: str, model: str = DEFAULT_MODEL, rate: int = 175,
                notify: Callable[[str], None] | None = None) -> None:
    """Generate + play one message. Blocking, serialized by the speech worker."""
    if not text.strip():
        return
    notify = notify or (lambda _: None)
    if voice not in {value for _, value in LOCAL_VOICES}:
        raise SpeechError(f"Unknown local speaker voice: {voice}")
    if sys.platform != "win32":
        raise SpeechError("This packaged chat reader plays audio on Windows 10/11.")
    # Gentle pace adjustment retains intelligibility; Kokoro expects 0.5..2.0.
    speed = max(0.77, min(1.3, rate / 175))
    # Use a lock in case a test and a queued message reach here together.
    with _engine_lock:
        engine = _engine(model, notify)
        notify(f"Generating locally: {voice}...")
        try:
            samples, sample_rate = engine.create(text, voice=voice, speed=speed, lang=voice_language(voice))
        except Exception as exc:
            raise SpeechError(f"Local speech generation failed: {type(exc).__name__}: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="habbo_local_") as directory:
        wav = Path(directory) / "speech.wav"
        _write_pcm(wav, samples, sample_rate)
        duration = _validate_wav(str(wav))
        notify(f"Playing local voice ({duration:.1f}s)")
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("speech_backend.py")), "--play-wav", str(wav)],
                capture_output=True, timeout=min(120, max(12, math.ceil(duration * 2 + 8))),
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise SpeechError(f"Local audio playback failed: {exc}") from exc
        if result.returncode:
            raise SpeechError("Local audio playback failed: " + (result.stderr or result.stdout).strip()[-350:])
        notify("Local playback finished")
