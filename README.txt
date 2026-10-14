HABBO CHAT READER v8.2  —  LOCAL AI + GITHUB UPDATE CHECKER
====================================================================

NO API KEY is needed for Kokoro. First-time AI voice model download is optional.
The GitHub updater downloads code ONLY from the public repo you configure.
The app asks for your permission before installing any update.

QUICK START
  1. Extract this ZIP to a permanent folder (e.g. Documents\Habbo Chat Reader).
  2. Run Setup_and_Run.bat ONCE to create the Python environment.
  3. Click Download local voices, then Test 3 speakers.
  4. Select speech bubble region and Start reading.
  5. To set up updates, click Set GitHub repo and enter USERNAME/habbo-chat-reader.

BEFORE GITHUB UPDATES CAN WORK
  See GITHUB_SETUP_GUIDE.txt (included). The GitHub repository must exist,
  be public, use main branch, and contain the app files at its root.
  Editing files locally will NOT automatically upload changes to GitHub.

HOW UPDATES WORK
  * On startup, if configured, checks Github main/VERSION (only code version).
  * When a newer version is found, the app offers to download & install it.
  * On approval, the app downloads a public GitHub source ZIP, checks its
    version and required files, then restarts via a standalone installer.
  * Only known program files are replaced; .venv, model files and user settings
    are preserved. Backups are created under %%LOCALAPPDATA%%\HabboChatReader\backups.
  * No account token/password is stored. Public repository access only.
  * GitHub updates require internet; speech recognition and local Kokoro speech
    do not require internet after voice assets are installed.

IMPORTANT
  The updater cannot obtain future versions until SOMEONE publishes changed
  source code AND bumps the VERSION file on your GitHub repository.
  This is not remote control of your PC. You choose the repo and approve updates.

v8.1 AUDIO FIX (14 October 2026)
- Fixes a Windows winsound AttributeError raised by the beep and WAV player.
- Corrects both audio paths; all existing v8 features are preserved.
- Windows synchronous playback is the default; no SND_SYNC flag is required.
- This update does not require downloading the speech model again if it was installed already.

HABBO CHAT READER v8 — LOCAL NEURAL VOICES FOR WINDOWS
=======================================================

What's new
----------
- Natural-sounding Kokoro 82M neural speech runs entirely on your computer.
- No Microsoft online speech, ElevenLabs account, API key, or subscription.
- The first 20 speakers get different voices; voices are reused if more than
  20 speakers appear. Assign any voice manually under WHO SOUNDS LIKE WHO.
- Preserves bubble detection, duplicate suppression and voice message queue.
- Uses local Windows audio WAV playback and reports synthesis/audio errors.

Quick start
-----------
1. Extract this whole ZIP to a new folder. Do not run the BAT inside the ZIP.
2. Double click Setup_and_Run.bat. Install Python 3.12 if prompted (you can
   have it alongside Python 3.14). The batch file uses 3.10–3.13 if installed.
3. In the app, leave 'Kokoro realistic voices (local)' selected.
4. Choose 'Quality' for clearer speech or 'Fast' for lower CPU usage.
5. Click Download local voices ONCE. This downloads Kokoro from the official
   github.com/thewh1teagle/kokoro-onnx release (~192 MB Quality / ~142 MB Fast).
   Model files are stored under %LOCALAPPDATA%\HabboChatReader\models.
6. Click Test sound (beep) to confirm your Windows speaker device works.
7. Click Test 3 speakers; the first test may take a few seconds to load Kokoro.
8. Open Habbo, select the speech bubble area, click Preview bubbles then
   Start reading. Set scan every 0.5 s as a starting point.
9. Later you can use Start_Habbo_Reader.bat (no reinstall required).

What stays private?
-------------------
- Habbo screenshots, extracted chat text and voice audio are processed locally.
- Only model files are downloaded once; no Habbo message text is uploaded.
- No login to Habbo or extraction of browser cookies needed.

Troubleshooting
---------------
- No beep: check Windows Volume mixer/output device.
- Beep but no Kokoro: inspect Speech FAILED in the app log. Verify downloaded
  model status and retry Setup_and_Run.bat if missing Python dependencies.
- First Kokoro sentence may be slow because the model loads into RAM.
- If speech falls behind, switch to the Fast local model, click Download, and
  use shorter detected messages; CPU speed varies by computer.
- Bad speech recognition: click Preview bubbles and tighten the capture area.
- Old usernames and region settings remain in your APPDATA settings file.

First-time setup requires an internet connection for pip, Tesseract (if missing)
and the model download. Offline after setup. If Windows shows a firewall prompt,
this app does not need network access for speaking after models are installed.

Based on Kokoro 82M (Apache-2.0) and kokoro-onnx (MIT).
Sources:
https://github.com/thewh1teagle/kokoro-onnx
https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1
https://huggingface.co/hexgrad/Kokoro-82M

Advanced sound test
-------------------
Test_Voices_Without_Habbo.bat checks audio outside the reader window. It tests
an audible beep, then three local neural voices if the Quality model is ready.
If you downloaded only the Fast model, test it using the app's Test 3 speakers.
