"""Offline Windows audio only. No cloud speech calls or API keys."""
from __future__ import annotations
import base64
import io
import math
import struct
import subprocess
import sys
import wave

class SpeechError(RuntimeError):
    pass

def _ps_cmd(script: str) -> list[str]:
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    return ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded]

def _exec(script: str, timeout: int) -> str:
    if sys.platform != 'win32':
        raise SpeechError('Windows is required for the built-in speech engine.')
    try:
        completed = subprocess.run(_ps_cmd(script), capture_output=True, timeout=timeout,
                                   encoding='utf-8', errors='replace',
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise SpeechError(f'Windows voice timed out or failed: {exc}') from exc
    if completed.returncode:
        raise SpeechError('Windows voice failed: ' + (completed.stderr or completed.stdout).strip()[-350:])
    return completed.stdout

def installed_voices() -> list[tuple[str, str]]:
    script = '''$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.GetInstalledVoices() | ForEach-Object { if ($_.Enabled) { $_.VoiceInfo.Name } } }
finally { $s.Dispose() }
'''
    unique = dict.fromkeys(x.strip() for x in _exec(script, 20).splitlines() if x.strip())
    return [(v, v) for v in unique]

def speak(message: str, *, rate: int = 175, voice: str = '') -> None:
    if not message.strip():
        return
    rate_sapi = max(-8, min(8, round((rate - 175) / 15)))
    a = base64.b64encode(message.encode('utf-8')).decode('ascii')
    b = base64.b64encode(voice.encode('utf-8')).decode('ascii')
    script = f'''$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$msg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{a}'))
$voice = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b}'))
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {{
 $s.SetOutputToDefaultAudioDevice()
 $s.Rate = {rate_sapi}
 if ($voice) {{ $s.SelectVoice($voice) }}
 $s.Speak($msg)
}} finally {{ $s.Dispose() }}
'''
    timeout = min(100, max(20, math.ceil(len(message.split()) * 60 / max(85, rate) * 2.8 + 12)))
    _exec(script, timeout)

def _validate_wav(path: str) -> float:
    try:
        with wave.open(path, 'rb') as w:
            duration = w.getnframes() / w.getframerate()
            if w.getcomptype() != 'NONE' or w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise SpeechError('Unexpected WAV output format')
            if duration < 0.08 or w.getframerate() < 8000:
                raise SpeechError('Empty local speech audio')
            return duration
    except (OSError, EOFError, wave.Error) as exc:
        raise SpeechError(f'Invalid local WAV file: {exc}') from exc

def _play_wav_builtin(path: str) -> None:
    if sys.platform != 'win32':
        raise SpeechError('WAV playback is available on Windows only')
    import winsound
    _validate_wav(path)
    try:
        # PlaySound is synchronous unless SND_ASYNC is explicitly requested.
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
    except (RuntimeError, OSError) as exc:
        raise SpeechError(f'Windows audio output failed: {exc}') from exc

def play_test_tone() -> None:
    if sys.platform != 'win32':
        raise SpeechError('Windows audio required')
    import winsound
    hz, seconds = 22050, 0.4
    samples = bytearray()
    total = round(hz * seconds)
    for i in range(total):
        fade = min(1, i / 350, (total - i) / 350)
        samples += struct.pack('<h', int(8000 * max(0, fade) * math.sin(2 * math.pi * 780 * i / hz)))
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(hz)
        out.writeframes(samples)
    try:
        # No SND_SYNC constant exists in Python winsound; synchronous is the default.
        winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)
    except (RuntimeError, OSError) as exc:
        raise SpeechError(f'Windows audio test failed: {exc}') from exc

if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--play-wav':
        try:
            _play_wav_builtin(sys.argv[2])
        except Exception as exc:
            print('Audio player error: ' + str(exc), file=sys.stderr)
            sys.exit(1)
    else:
        sys.exit('Unsupported speech backend command')
