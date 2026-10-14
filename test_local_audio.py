"""Optional separate local speech diagnostic without starting Habbo OCR."""
from local_tts import LOCAL_VOICES, has_model, DEFAULT_MODEL, speak_local
from speech_backend import SpeechError, play_test_tone
print('Testing beep...')
try:
    play_test_tone()
    print('Beep complete.')
except SpeechError as exc:
    print('Beep failed:', exc)
if not has_model(DEFAULT_MODEL):
    print('Quality model not installed yet. Download voices in the app first.')
else:
    for name, voice in LOCAL_VOICES[:3]:
        try:
            print('Testing', name)
            speak_local('Hello from Habbo. This is a local, natural sounding voice.', voice=voice,
                        notify=print)
        except SpeechError as exc:
            print('FAILED:', exc)
