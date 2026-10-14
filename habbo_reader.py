"""Habbo Chat Reader v8: offline browser OCR and local Kokoro neural voices.

Screen capture, OCR, speech generation and audio playback stay on this PC.
First-time model files download only when the user explicitly requests it.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

if sys.platform == "win32":
    try:
        # Keep tkinter selection coordinates and MSS pixel coordinates consistent.
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import mss
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat
import pytesseract

from reader_core import ChatTracker, clean_lines, spoken_line, split_sender
from bubble_ocr import BubbleReader, BubbleTracker
from speech_backend import SpeechError, installed_voices, speak, play_test_tone
from local_tts import (LOCAL_ENGINE, WINDOWS_ENGINE, DEFAULT_MODEL, MODELS, LOCAL_VOICES,
                       has_model, download_assets, speak_local)
from speaker_voices import (VoiceRoster, display_name, normalized_speaker)
from update_manager import (VERSION as APP_VERSION, UpdateError, load_repo, save_repo,
                            normalize_repo, check_update, download_update)
from updater import version_files


APP_TITLE = "Habbo Chat Reader"
CONFIG_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "HabboChatReader" / "settings.json"
BG = "#101722"
PANEL = "#1c2636"
INK = "#f1f5fa"
SUBTLE = "#a9b7c9"
ACCENT = "#52d6b0"
CONFIG_DEFAULTS = {
    "monitor_index": 1, "region": None, "speech_rate": 175,
    "speak_names": False, "mute_own": False, "own_name": "",
    "muted_users": "", "interval": 0.5, "voice_id": "", "minimise_on_start": True,
    "ocr_mode": "Bubbles (recommended)", "voice_engine": LOCAL_ENGINE,
    "speaker_voices": {}, "local_model": DEFAULT_MODEL,
}


def detect_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Tesseract-OCR" / "tesseract.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def extract_text(rect: dict[str, int], mode: str) -> str:
    """Capture only the user-selected rectangle; do not save/upload screenshots."""
    with mss.mss() as capture:
        screenshot = capture.grab(rect)
    image = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
    gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
    if mode == "Light text on dark":
        gray = ImageOps.invert(gray)
    elif mode in ("Auto (recommended)", "Pixel text (high contrast)") and ImageStat.Stat(gray).mean[0] < 126:
        gray = ImageOps.invert(gray)
    scale = 3 if image.width <= 850 else 2
    resized = gray.resize((gray.width * scale, gray.height * scale), Image.Resampling.LANCZOS)
    if mode == "Pixel text (high contrast)":
        # Optional for sharp, pixelated fonts over a mostly uniform chat pane.
        # The normal mode remains preferable on colourful background imagery.
        resized = ImageEnhance.Contrast(resized).enhance(2.2)
        resized = resized.point(lambda p: 255 if p >= 157 else 0)
    # psm 6: treat the chat history as an ordered text block.
    text = pytesseract.image_to_string(
        resized, lang="eng", config="--oem 3 --psm 6 -c preserve_interword_spaces=1", timeout=7
    )
    return text.strip()


class SelectionOverlay:
    def __init__(self, parent: tk.Tk, monitor: dict, done):
        self.parent = parent
        self.monitor = monitor
        self.done = done
        self.x0 = self.y0 = None
        self.box_id = None
        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.56)
        w, h = monitor["width"], monitor["height"]
        x, y = monitor["left"], monitor["top"]
        self.window.geometry(f"{w}x{h}{x:+d}{y:+d}")
        # Tk's -x geometry syntax can mean right-edge anchoring. Windows' own
        # positioning API uses absolute virtual-desktop coordinates instead,
        # so monitors to the left/above the primary screen are handled properly.
        if sys.platform == "win32":
            self.window.update_idletasks()
            ctypes.windll.user32.SetWindowPos(
                self.window.winfo_id(), -1, x, y, w, h, 0x0040
            )
        self.canvas = tk.Canvas(self.window, bg="#000000", bd=0, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            28, 25, anchor="nw", text="DRAG around Habbo speech bubbles  •  Esc to cancel",
            font=("Segoe UI", 17, "bold"), fill="white"
        )
        self.canvas.bind("<Button-1>", self.mouse_down)
        self.canvas.bind("<B1-Motion>", self.mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.mouse_up)
        self.window.bind("<Escape>", lambda _: self.finish(None))
        self.window.focus_force()
        self.window.grab_set()

    def mouse_down(self, event):
        self.x0, self.y0 = event.x, event.y
        if self.box_id:
            self.canvas.delete(self.box_id)
        self.box_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=ACCENT, width=4, fill=""
        )

    def mouse_move(self, event):
        if self.box_id:
            self.canvas.coords(self.box_id, self.x0, self.y0, event.x, event.y)

    def mouse_up(self, event):
        if self.x0 is None:
            return
        x1, y1 = min(self.x0, event.x), min(self.y0, event.y)
        x2, y2 = max(self.x0, event.x), max(self.y0, event.y)
        if x2 - x1 < 80 or y2 - y1 < 35:
            self.finish(None)
            return
        mon = self.monitor
        rect = {
            "left": mon["left"] + x1, "top": mon["top"] + y1,
            "width": x2 - x1, "height": y2 - y1,
        }
        self.finish(rect)

    def finish(self, rect):
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self.done(rect)


class HabboReader:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = CONFIG_DEFAULTS | self.load_settings()
        if self.settings["ocr_mode"] not in ("Bubbles (recommended)", "Legacy full-screen OCR"):
            self.settings["ocr_mode"] = "Bubbles (recommended)"
        self.events: queue.Queue = queue.Queue()
        self.speech_queue: queue.Queue = queue.Queue(maxsize=8)
        self.exit_event = threading.Event()
        self.scan_active = threading.Event()
        self._start_pending = False
        self.tracker = ChatTracker()
        self.bubble_tracker = BubbleTracker()
        self.bubble_reader = BubbleReader()
        self.config_lock = threading.Lock()
        self.speech_opts = {"rate": int(self.settings["speech_rate"]), "voice": "", "engine": LOCAL_ENGINE,
                            "model": self.settings.get("local_model", DEFAULT_MODEL)}
        self.voice_roster = VoiceRoster(self.settings.get("speaker_voices"))
        self.known_speakers = set(
            list(self.voice_roster.allocations["local"]) + list(self.voice_roster.allocations["offline"])
            + list(self.voice_roster.overrides["local"]) + list(self.voice_roster.overrides["offline"])
        )
        self.monitor_rects = self.get_monitors()
        self.voices = []
        self.root.title(APP_TITLE)
        self.root.geometry("940x965")
        self.root.minsize(750, 825)
        self.root.configure(bg=BG)
        self.update_repo = load_repo()
        self.update_busy = False
        self.make_widgets()
        self.show_region()
        self.refresh_tesseract_warning()
        threading.Thread(target=self.speech_worker, name="Speech", daemon=True).start()
        threading.Thread(target=self.scan_worker, name="OCR", daemon=True).start()
        self.root.after(100, self.process_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if self.update_repo:
            self.root.after(1800, lambda: self.check_for_updates(silent=True))

    @staticmethod
    def load_settings() -> dict:
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self):
        data = {
            "monitor_index": self.monitor_var.get() + 1,
            "region": self.settings.get("region"),
            "speech_rate": int(self.rate_var.get()),
            "speak_names": bool(self.speak_names_var.get()),
            "mute_own": bool(self.mute_own_var.get()),
            "own_name": self.own_var.get().strip(),
            "muted_users": self.muted_var.get().strip(),
            "interval": float(self.interval_var.get()),
            "voice_id": self.selected_voice_id(),
            "ocr_mode": self.ocr_mode_var.get(),
            "minimise_on_start": bool(self.minimise_var.get()),
            "voice_engine": self.engine_var.get(),
            "speaker_voices": self.voice_roster.dump(),
            "local_model": self.model_var.get(),
        }
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.settings = CONFIG_DEFAULTS | data
        except OSError as exc:
            self.append(f"Could not save settings: {exc}")

    def get_monitors(self):
        try:
            with mss.mss() as capture:
                return [dict(m) for m in capture.monitors[1:]]
        except Exception:
            return [{"left": 0, "top": 0, "width": 1920, "height": 1080}]

    def make_widgets(self):
        sty = ttk.Style(self.root)
        sty.theme_use("clam")
        sty.configure("Dark.TCombobox", fieldbackground=PANEL, background=PANEL,
                      foreground=INK, arrowcolor=INK, bordercolor="#43546c")
        sty.map("Dark.TCombobox", fieldbackground=[("readonly", PANEL)],
                foreground=[("readonly", INK)])
        sty.configure("Dark.TCheckbutton", background=PANEL, foreground=INK,
                      font=("Segoe UI", 10))
        sty.map("Dark.TCheckbutton", background=[("active", PANEL)])
        sty.configure("Dark.TEntry", fieldbackground="#253247", foreground=INK)
        title = tk.Frame(self.root, bg=BG)
        title.pack(fill="x", padx=22, pady=(15, 10))
        tk.Label(title, text=f"HABBO  /  CHAT READER  v{APP_VERSION}", fg=ACCENT, bg=BG,
                 font=("Segoe UI", 19, "bold")).pack(anchor="w")
        buttons = tk.Frame(title, bg=BG)
        buttons.pack(anchor="w", pady=(5, 0))
        self.button(buttons, "Set GitHub repo", self.configure_update_repo).pack(side="left")
        self.button(buttons, "Check for updates", self.check_for_updates).pack(side="left", padx=(10, 0))
        self.update_label = tk.Label(buttons, text="Updates not configured" if not self.update_repo else f"GitHub: {self.update_repo}",
                                     bg=BG, fg=SUBTLE, font=("Segoe UI", 9))
        self.update_label.pack(side="left", padx=(12, 0))
        tk.Label(title, text="Reads individual chat bubbles, not room graphics. Speaks new messages once.",
                 fg=SUBTLE, bg=BG, font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))

        region = self.panel("1  •  SELECT WHAT TO READ")
        row1 = tk.Frame(region, bg=PANEL)
        row1.pack(fill="x", pady=(7, 5))
        self.monitor_var = tk.IntVar(value=min(max(0, int(self.settings["monitor_index"]) - 1),
                                              len(self.monitor_rects) - 1))
        values = [f"Monitor {i + 1}  ({m['width']} x {m['height']})"
                  for i, m in enumerate(self.monitor_rects)]
        tk.Label(row1, text="Screen", fg=SUBTLE, bg=PANEL).pack(side="left", padx=(0, 10))
        monbox = ttk.Combobox(row1, state="readonly", values=values, width=26, style="Dark.TCombobox")
        monbox.current(self.monitor_var.get())
        monbox.pack(side="left")
        monbox.bind("<<ComboboxSelected>>", lambda _: self.on_monitor_change(monbox.current()))
        self.button(row1, "Select bubble area", self.select_area, main=True).pack(side="left", padx=12)
        self.region_label = tk.Label(region, text="", bg=PANEL, fg=SUBTLE,
                                     font=("Segoe UI", 10), anchor="w")
        self.region_label.pack(fill="x", pady=(4, 0))

        controls = self.panel("2  •  LISTEN")
        controls_row = tk.Frame(controls, bg=PANEL)
        controls_row.pack(fill="x", pady=(7, 0))
        self.start_button = self.button(controls_row, "▶  Start reading", self.toggle_scan, main=True)
        self.start_button.pack(side="left")
        self.button(controls_row, "Preview bubbles", self.test_ocr).pack(side="left", padx=(9, 0))
        self.button(controls_row, "Test 3 speakers", self.test_voice).pack(side="left", padx=(9, 0))
        self.button(controls_row, "Test sound (beep)", self.test_audio).pack(side="left", padx=(9, 0))
        self.minimise_var = tk.BooleanVar(value=bool(self.settings.get("minimise_on_start", True)))
        ttk.Checkbutton(controls, text="Minimise reader while listening (prevents covering Habbo)", variable=self.minimise_var,
                        style="Dark.TCheckbutton").pack(anchor="w", pady=(7, 0))
        self.status_label = tk.Label(controls_row, text="●  Paused", bg=PANEL, fg=SUBTLE,
                                     font=("Segoe UI", 10, "bold"))
        self.status_label.pack(side="right")
        self.tts_warning = tk.Label(controls, text="", bg=PANEL, fg="#ffb566",
                                    justify="left", anchor="w", wraplength=690)
        self.tts_warning.pack(fill="x", pady=(6, 0))

        settings = self.panel("3  •  CUSTOMISE")
        top = tk.Frame(settings, bg=PANEL)
        top.pack(fill="x", pady=(8, 4))
        tk.Label(top, text="Voice engine", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        # Old v7 settings may point to online voices, but v8 intentionally
        # defaults to private/offline local speech and never calls their services.
        saved_engine = self.settings.get("voice_engine", LOCAL_ENGINE)
        if saved_engine not in (LOCAL_ENGINE, WINDOWS_ENGINE):
            saved_engine = LOCAL_ENGINE
        self.engine_var = tk.StringVar(value=saved_engine)
        self.engine_box = ttk.Combobox(top, textvariable=self.engine_var, state="readonly", width=33,
                                       values=[LOCAL_ENGINE, WINDOWS_ENGINE], style="Dark.TCombobox")
        self.engine_box.pack(side="left")
        self.engine_box.bind("<<ComboboxSelected>>", lambda _: self.on_engine_changed())
        fallback = tk.Frame(settings, bg=PANEL)
        fallback.pack(fill="x", pady=(0, 1))
        tk.Label(fallback, text="Windows voice", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        self.voice_var = tk.StringVar(value="Loading installed Windows voices...")
        self.voice_box = ttk.Combobox(fallback, textvariable=self.voice_var, state="readonly",
                                      width=37, style="Dark.TCombobox")
        self.voice_box.pack(side="left", fill="x", expand=True)
        self.voice_box.bind("<<ComboboxSelected>>", lambda _: self.change_tts_options())
        model_row = tk.Frame(settings, bg=PANEL)
        model_row.pack(fill="x", pady=(8, 4))
        tk.Label(model_row, text="Local model", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        prior_model = self.settings.get("local_model", DEFAULT_MODEL)
        self.model_var = tk.StringVar(value=prior_model if prior_model in MODELS else DEFAULT_MODEL)
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var,
                                     values=list(MODELS), width=32, state="readonly", style="Dark.TCombobox")
        self.model_box.pack(side="left")
        self.model_box.bind("<<ComboboxSelected>>", lambda _: self.on_model_changed())
        self.download_button = self.button(model_row, "Download local voices", self.download_local_model)
        self.download_button.pack(side="left", padx=(8, 0))
        self.model_status = tk.Label(settings, text="", bg=PANEL, fg=SUBTLE, anchor="w", justify="left",
                                     font=("Segoe UI", 9), wraplength=760)
        self.model_status.pack(fill="x", pady=(2, 4))
        self.update_model_status()
        tk.Label(settings, text="No API key or subscription. One-time model download; all chat audio stays on your PC.",
                 bg=PANEL, fg=SUBTLE, font=("Segoe UI", 9)).pack(anchor="w")
        mid = tk.Frame(settings, bg=PANEL)
        mid.pack(fill="x", pady=(6, 4))
        self.rate_var = tk.IntVar(value=int(self.settings["speech_rate"]))
        tk.Label(mid, text="Speech speed", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        self.rate_scale = tk.Scale(mid, from_=105, to=260, orient="horizontal", resolution=5,
                                   variable=self.rate_var, bg=PANEL, fg=INK, troughcolor="#36455b",
                                   activebackground=ACCENT, bd=0, highlightthickness=0, length=300,
                                   command=lambda _: self.change_tts_options())
        self.rate_scale.pack(side="left")
        tk.Label(mid, text="words / minute", bg=PANEL, fg=SUBTLE).pack(side="left", padx=7)
        checks = tk.Frame(settings, bg=PANEL)
        checks.pack(fill="x", pady=(7, 5))
        self.speak_names_var = tk.BooleanVar(value=bool(self.settings["speak_names"]))
        self.mute_own_var = tk.BooleanVar(value=bool(self.settings["mute_own"]))
        ttk.Checkbutton(checks, text="Speak usernames", variable=self.speak_names_var,
                        style="Dark.TCheckbutton").pack(side="left")
        ttk.Checkbutton(checks, text="Ignore my messages", variable=self.mute_own_var,
                        style="Dark.TCheckbutton").pack(side="left", padx=20)
        entryrow = tk.Frame(settings, bg=PANEL)
        entryrow.pack(fill="x", pady=5)
        tk.Label(entryrow, text="My username", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        self.own_var = tk.StringVar(value=self.settings["own_name"])
        tk.Entry(entryrow, textvariable=self.own_var, width=24, bg="#253247", fg=INK,
                 insertbackground=INK, relief="flat").pack(side="left", ipady=4)
        tk.Label(entryrow, text="Mute users", bg=PANEL, fg=SUBTLE,
                 padx=12).pack(side="left")
        self.muted_var = tk.StringVar(value=self.settings["muted_users"])
        tk.Entry(entryrow, textvariable=self.muted_var, bg="#253247", fg=INK,
                 insertbackground=INK, relief="flat").pack(side="left", fill="x", expand=True, ipady=4)
        tk.Label(settings, text="Mute users: separate usernames with commas. Names are read from individual bubbles.",
                 bg=PANEL, fg=SUBTLE, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))

        ocrrow = tk.Frame(settings, bg=PANEL)
        ocrrow.pack(fill="x", pady=(9, 0))
        tk.Label(ocrrow, text="Reading mode", bg=PANEL, fg=SUBTLE, width=13, anchor="w").pack(side="left")
        self.ocr_mode_var = tk.StringVar(value=self.settings["ocr_mode"])
        self.ocr_mode_box = ttk.Combobox(ocrrow, textvariable=self.ocr_mode_var,
                                          values=["Bubbles (recommended)", "Legacy full-screen OCR"],
                                          state="readonly", width=23, style="Dark.TCombobox")
        self.ocr_mode_box.pack(side="left")
        tk.Label(ocrrow, text="Scan every", bg=PANEL, fg=SUBTLE, padx=12).pack(side="left")
        self.interval_var = tk.StringVar(value=str(self.settings["interval"]))
        ttk.Combobox(ocrrow, textvariable=self.interval_var, state="readonly",
                     values=["0.35", "0.5", "0.85", "1.2", "1.8"], width=6,
                     style="Dark.TCombobox").pack(side="left")
        tk.Label(ocrrow, text="seconds", bg=PANEL, fg=SUBTLE).pack(side="left", padx=5)

        speaker_panel = self.panel("4  •  WHO SOUNDS LIKE WHO")
        tk.Label(speaker_panel, text="Each username gets a stable Kokoro voice. Choose a voice manually at any time.",
                 bg=PANEL, fg=SUBTLE, font=("Segoe UI", 9)).pack(anchor="w", pady=(4, 6))
        voice_edit = tk.Frame(speaker_panel, bg=PANEL)
        voice_edit.pack(fill="x", pady=(2, 4))
        tk.Label(voice_edit, text="Username", bg=PANEL, fg=SUBTLE).pack(side="left", padx=(0, 7))
        self.speaker_var = tk.StringVar()
        self.speaker_box = ttk.Combobox(voice_edit, textvariable=self.speaker_var, width=21,
                                        style="Dark.TCombobox")
        self.speaker_box.pack(side="left", padx=(0, 10))
        self.speaker_box.bind("<<ComboboxSelected>>", lambda _: self.update_speaker_choice())
        self.speaker_box.bind("<FocusOut>", lambda _: self.update_speaker_choice())
        tk.Label(voice_edit, text="Voice", bg=PANEL, fg=SUBTLE).pack(side="left", padx=(0, 7))
        self.speaker_voice_var = tk.StringVar(value="Automatic")
        self.speaker_voice_box = ttk.Combobox(voice_edit, textvariable=self.speaker_voice_var,
                                               state="readonly", width=29, style="Dark.TCombobox")
        self.speaker_voice_box.pack(side="left", fill="x", expand=True)
        actions = tk.Frame(speaker_panel, bg=PANEL)
        actions.pack(fill="x", pady=(2, 0))
        self.button(actions, "Assign voice", self.assign_speaker_voice).pack(side="left")
        self.button(actions, "Use automatic", self.clear_speaker_voice).pack(side="left", padx=(8, 0))
        self.button(actions, "Test this speaker", self.test_speaker_voice).pack(side="left", padx=(8, 0))
        self.speaker_status = tk.Label(speaker_panel, text="People appear here as Habbo messages are recognised.",
                                       bg=PANEL, fg=SUBTLE, anchor="w", wraplength=780, justify="left")
        self.speaker_status.pack(fill="x", pady=(5, 0))
        self.update_voice_choices()

        logpanel = self.panel("5  •  MESSAGES DETECTED")
        self.log = tk.Text(logpanel, height=7, bg="#111a27", fg="#dbe8f3", relief="flat",
                           font=("Consolas", 10), state="disabled", padx=9, pady=8,
                           wrap="word", insertbackground=INK)
        self.log.pack(fill="both", expand=True, pady=(7, 0))
        self.append("Choose a monitor, select the speech bubble area, and include the WHOLE bubble stack.")
        self.append("Tip: minimise this reader when listening; overlapping windows hide bubble text.")
        self.append("Bubble mode scans speech boxes individually; text is confirmed twice before speaking.")
        self.append("v8.2: Kokoro voices + optional GitHub in-app updater. GitHub access is only for code updates.")
        self.append("No cloud speech services. No automatic switch to robotic Windows voices.")
        self.append("Voice status reports SPEAKING / SPOKEN / errors so you can see if sound is stuck.")
        self.root.bind("<Control-Shift-r>", lambda _: self.toggle_scan())
        tk.Label(self.root, text="Ctrl+Shift+R: start / pause     •     Chat text, speech and screenshots stay on your PC",
                 fg=SUBTLE, bg=BG, font=("Segoe UI", 9)).pack(anchor="w", padx=22, pady=(4, 8))

    def panel(self, heading):
        p = tk.Frame(self.root, bg=PANEL, highlightbackground="#314055", highlightthickness=1)
        p.pack(fill="both", expand=(heading.startswith("4")), padx=22, pady=5, ipadx=15, ipady=10)
        tk.Label(p, text=heading, bg=PANEL, fg=INK,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        return p

    def button(self, parent, name, callback, main=False):
        return tk.Button(parent, text=name, command=callback, bg=ACCENT if main else "#37475e",
                         fg="#10231c" if main else INK, activebackground="#75e7c8" if main else "#465b76",
                         activeforeground=BG if main else INK, relief="flat", bd=0, cursor="hand2",
                         font=("Segoe UI", 10, "bold" if main else "normal"), padx=13, pady=7)

    def append(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        # Prevent unbounded UI memory use.
        if int(self.log.index("end-1c").split(".")[0]) > 500:
            self.log.delete("1.0", "90.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_monitor_change(self, idx):
        if self.scan_active.is_set():
            self.toggle_scan()
        self.monitor_var.set(idx)
        self.settings["region"] = None
        self.show_region()

    def show_region(self):
        r = self.settings.get("region")
        if not r:
            self.region_label.configure(text="No area selected. Drag across the area where speech bubbles appear.")
        else:
            self.region_label.configure(
                text=f"Selected area: {r['width']} x {r['height']} pixels  •  x={r['left']}, y={r['top']}"
            )

    def select_area(self):
        if self.scan_active.is_set():
            self.toggle_scan()
        self.root.withdraw()
        mon = self.monitor_rects[self.monitor_var.get()]
        self.root.after(250, lambda: SelectionOverlay(self.root, mon, self.area_selected))

    def area_selected(self, rect):
        self.root.deiconify()
        self.root.lift()
        if rect:
            self.settings["region"] = rect
            self.append("Bubble area selected. Click Preview bubbles to check what the app can read.")
            self.save_settings()
        self.show_region()

    def refresh_tesseract_warning(self):
        exe = detect_tesseract()
        if exe:
            pytesseract.pytesseract.tesseract_cmd = exe
            self.tts_warning.configure(text="")
        else:
            self.tts_warning.configure(
                text="OCR engine missing. Run Setup_and_Run.bat, or install Tesseract with: "
                     "winget install --id UB-Mannheim.TesseractOCR -e"
            )

    def settings_snapshot(self):
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 0.5
        return {
            "region": dict(self.settings.get("region") or {}),
            "mode": self.ocr_mode_var.get(),
            "interval": max(0.4, interval),
            "speak_names": self.speak_names_var.get(),
            "mute_own": self.mute_own_var.get(),
            "own_name": self.own_var.get(),
            "muted": self.muted_var.get().split(","),
        }

    def toggle_scan(self):
        if self.scan_active.is_set() or self._start_pending:
            self._start_pending = False
            self.scan_active.clear()
            self.clear_speech_queue()
            self.start_button.configure(text="▶  Start reading")
            self.status_label.configure(text="●  Paused", fg=SUBTLE)
            self.append("Paused. The current spoken sentence may finish.")
            self.save_settings()
            return
        if not self.settings.get("region"):
            messagebox.showinfo(APP_TITLE, "Select the Habbo chat area first.")
            return
        if self.engine_var.get() == LOCAL_ENGINE and not has_model(self.model_var.get()):
            messagebox.showinfo(APP_TITLE, "Kokoro model not installed yet. Click Download local voices once, then try again.")
            return
        self.refresh_tesseract_warning()
        if not detect_tesseract():
            messagebox.showerror(APP_TITLE, "Install Tesseract OCR first using Setup_and_Run.bat.")
            return
        self._start_pending = True
        self.tracker.reset()
        self.bubble_tracker.reset()
        self.clear_speech_queue()
        self.save_settings()
        if self.minimise_var.get():
            # Do not baseline a screen hidden behind our own window.
            self.root.after(600, lambda: self._finish_start())
        else:
            self._finish_start()
        self.start_button.configure(text="Ⅱ  Pause reading")
        self.status_label.configure(text="●  Reading", fg=ACCENT)
        self.append("Reading started. Existing bubbles are skipped; new bubbles must be seen twice.")
        self.append("Tip: DETECTED = queued; SPEAKING = started; SPOKEN = the sound call finished.")
        if self.minimise_var.get():
            self.root.after(100, self.root.iconify)

    def _finish_start(self):
        if self._start_pending and not self.exit_event.is_set():
            self._start_pending = False
            self.scan_active.set()

    def test_ocr(self):
        if not self.settings.get("region"):
            messagebox.showinfo(APP_TITLE, "Select a chat area first.")
            return
        self.refresh_tesseract_warning()
        if not detect_tesseract():
            messagebox.showerror(APP_TITLE, "Tesseract OCR is not installed yet.")
            return
        snap = self.settings_snapshot()
        self.append("Checking the selected chat area...")
        threading.Thread(target=self.test_worker, args=(snap,), daemon=True).start()

    def test_worker(self, snap):
        try:
            if snap["mode"] == "Bubbles (recommended)":
                with mss.mss() as capture:
                    shot = capture.grab(snap["region"])
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                bubbles, calls = BubbleReader().read(image)
                self.events.put(("bubble_preview", (bubbles, calls)))
            else:
                raw = extract_text(snap["region"], "Auto (recommended)")
                self.events.put(("ocr_test", raw))
        except Exception as exc:
            self.events.put(("error", f"Test OCR error: {exc}"))

    def scan_worker(self):
        reported_speakers: set[str] = set()
        while not self.exit_event.is_set():
            if not self.scan_active.wait(timeout=0.15):
                continue
            # Collect UI-derived settings on the main thread so Tk calls remain safe.
            reply = queue.Queue(maxsize=1)
            self.events.put(("settings_request", reply))
            try:
                snap = reply.get(timeout=2.0)
            except queue.Empty:
                continue
            began = time.monotonic()
            try:
                if snap["mode"] == "Bubbles (recommended)":
                    with mss.mss() as capture:
                        shot = capture.grab(snap["region"])
                    image = Image.frombytes("RGB", shot.size, shot.rgb)
                    bubbles, ocr_calls = self.bubble_reader.read(image)
                    if not self.scan_active.is_set():
                        continue
                    # Learn existing usernames as well as newly arriving ones.
                    for item in bubbles:
                        sender, _ = split_sender(item.line)
                        if sender and normalized_speaker(sender) not in reported_speakers:
                            reported_speakers.add(normalized_speaker(sender))
                            self.events.put(("speaker_seen", sender))
                    found_lines, primed = self.bubble_tracker.update(bubbles)
                    if primed:
                        self.events.put(("status", f"Primed {len(bubbles)} existing bubbles. Listening for new messages..."))
                    elif ocr_calls:
                        self.events.put(("status", f"Checked {len(bubbles)} bubbles ({ocr_calls} new OCR scans)."))
                else:
                    raw = extract_text(snap["region"], "Auto (recommended)")
                    if not self.scan_active.is_set():
                        continue
                    result = self.tracker.update(raw)
                    found_lines = result.new_lines
                    if result.primed:
                        self.events.put(("status", "Baseline captured. Waiting for new lines..."))
                for new_line in found_lines:
                    sender, _ = split_sender(new_line)
                    if sender and normalized_speaker(sender) not in reported_speakers:
                        reported_speakers.add(normalized_speaker(sender))
                        self.events.put(("speaker_seen", sender))
                    speech = spoken_line(
                        new_line, speak_names=snap["speak_names"], muted_users=snap["muted"],
                        own_name=snap["own_name"], mute_own=snap["mute_own"]
                    )
                    if speech:
                        sender, _ = split_sender(new_line)
                        self.enqueue_speech(speech, speaker=sender or "")
                        self.events.put(("message", new_line))
            except Exception as exc:
                self.events.put(("error", f"OCR error: {exc}"))
                # Avoid a busy error loop if screenshot capture is denied.
                time.sleep(1.0)
            self.exit_event.wait(max(0.05, snap["interval"] - (time.monotonic() - began)))

    def enqueue_speech(self, text, speaker="", *, engine=None, voice=None):
        # Pre-select the voice at detection time so that speech from two
        # speakers cannot accidentally inherit the same voice.
        with self.config_lock:
            engine = engine or self.speech_opts["engine"]
            allowed = self.available_voices(engine)
            choice = voice if voice is not None else self.voice_roster.choose(speaker, self.engine_key(engine), allowed)
        job = (speaker, text, engine, choice)
        try:
            self.speech_queue.put_nowait(job)
        except queue.Full:
            # Drop the oldest item, not the newest message, when chat is very active.
            try:
                self.speech_queue.get_nowait()
                self.events.put(("speech_notice", "Speech is falling behind; skipped an older queued message."))
            except queue.Empty:
                pass
            try:
                self.speech_queue.put_nowait(job)
            except queue.Full:
                pass

    def clear_speech_queue(self):
        while True:
            try:
                self.speech_queue.get_nowait()
            except queue.Empty:
                return

    def speech_worker(self):
        # Speech runs in its own worker, so OCR can keep scanning while audio plays.
        # pyttsx3's SAPI5 runAndWait() could read ONE sentence and silently stop
        # on some Windows PCs while OCR kept producing messages.  A timeout and
        # new subprocess for each sentence make subsequent speech recoverable.
        try:
            self.events.put(("voices", installed_voices()))
        except SpeechError as exc:
            self.events.put(("error", f"Could not list Windows voices: {exc}"))
            self.events.put(("voices", []))
        while not self.exit_event.is_set():
            try:
                job = self.speech_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if not job:
                continue
            speaker, utterance, engine, voice = job
            with self.config_lock:
                opts = dict(self.speech_opts)
            preview = f"{speaker} [{voice or 'default'}]: {utterance[:75]}"
            self.events.put(("speech_started", preview))
            try:
                if engine == LOCAL_ENGINE:
                    speak_local(utterance, voice=voice or LOCAL_VOICES[0][1],
                                model=opts["model"], rate=opts["rate"],
                                notify=lambda stage: self.events.put(("speech_notice", stage)))
                else:
                    speak(utterance, rate=opts["rate"], voice=voice or opts["voice"])
                self.events.put(("speech_done", preview))
            except SpeechError as exc:
                self.events.put(("error", f"Speech FAILED for '{preview}': {exc}"))
            except Exception as exc:
                self.events.put(("error", f"Unexpected speech error: {exc}"))

    @staticmethod
    def engine_key(engine):
        return "local" if engine == LOCAL_ENGINE else "offline"

    def available_voice_pairs(self, engine):
        return LOCAL_VOICES if engine == LOCAL_ENGINE else self.voices

    def available_voices(self, engine):
        return [voice_id for _, voice_id in self.available_voice_pairs(engine)]

    def register_speaker(self, username):
        key = normalized_speaker(username)
        if not key or key in self.known_speakers:
            return
        self.known_speakers.add(key)
        self.speaker_box["values"] = sorted(self.known_speakers)
        with self.config_lock:
            self.voice_roster.choose(username, "local", self.available_voices(LOCAL_ENGINE))
            if self.voices:
                self.voice_roster.choose(username, "offline", self.available_voices(WINDOWS_ENGINE))
        if not self.speaker_var.get().strip():
            self.speaker_var.set(username)
            self.update_speaker_choice()
        self.save_settings()

    def on_model_changed(self):
        self.change_tts_options()
        self.update_model_status()
        self.save_settings()

    def update_model_status(self):
        selected = self.model_var.get()
        if has_model(selected):
            self.model_status.configure(text="● Local Kokoro voices are ready. No internet required to speak.", fg=ACCENT)
        else:
            self.model_status.configure(text="○ Model not installed. Click Download local voices (one-time download, approximately 140–200 MB including voices).",
                                        fg="#ffb566")

    def download_local_model(self):
        selected = self.model_var.get()
        self.download_button.configure(state="disabled")
        self.model_status.configure(text="Downloading model files...", fg=SUBTLE)
        def download():
            try:
                download_assets(selected, notify=lambda msg: self.events.put(("model_progress", msg)))
                self.events.put(("model_ready", selected))
            except SpeechError as exc:
                self.events.put(("model_error", str(exc)))
            except Exception as exc:
                self.events.put(("model_error", f"{type(exc).__name__}: {exc}"))
        threading.Thread(target=download, name="KokoroModelDownload", daemon=True).start()

    def on_engine_changed(self):
        self.change_tts_options()
        self.update_voice_choices()
        self.update_model_status()
        self.save_settings()

    def update_voice_choices(self):
        pairs = self.available_voice_pairs(self.engine_var.get())
        self.speaker_voice_box["values"] = ["Automatic"] + [label for label, _ in pairs]
        self.speaker_box["values"] = sorted(self.known_speakers)
        self.update_speaker_choice()

    def update_speaker_choice(self):
        username = self.speaker_var.get().strip()
        engine = self.engine_key(self.engine_var.get())
        pairs = self.available_voice_pairs(self.engine_var.get())
        if not username:
            self.speaker_voice_var.set("Automatic")
            self.speaker_status.configure(text="Enter a Habbo username or select a detected person.")
            return
        with self.config_lock:
            actual = self.voice_roster.choose(username, engine, [v for _, v in pairs])
            manual = self.voice_roster.overrides[engine].get(normalized_speaker(username))
        name = display_name(actual, pairs) if actual else "Load voices for this engine first"
        self.speaker_voice_var.set(display_name(manual, pairs) if manual else "Automatic")
        extra = " (manually assigned)" if manual else " (automatic)"
        self.speaker_status.configure(text=f"{username} → {name}{extra}")

    def assign_speaker_voice(self):
        username = self.speaker_var.get().strip()
        pairs = self.available_voice_pairs(self.engine_var.get())
        label = self.speaker_voice_var.get()
        if label == "Automatic":
            self.clear_speaker_voice()
            return
        match = next((vid for name, vid in pairs if name == label), None)
        if not username or not match:
            messagebox.showinfo(APP_TITLE, "Enter a username and choose a voice.")
            return
        with self.config_lock:
            self.voice_roster.set_override(username, self.engine_key(self.engine_var.get()),
                                           match, [vid for _, vid in pairs])
        self.known_speakers.add(normalized_speaker(username))
        self.speaker_box["values"] = sorted(self.known_speakers)
        self.update_speaker_choice()
        self.save_settings()
        self.append(f"Voice assigned: {username} → {label}")

    def clear_speaker_voice(self):
        username = self.speaker_var.get().strip()
        if username:
            with self.config_lock:
                self.voice_roster.clear_override(username, self.engine_key(self.engine_var.get()))
            self.update_speaker_choice()
            self.save_settings()

    def test_speaker_voice(self):
        username = self.speaker_var.get().strip()
        if not username:
            messagebox.showinfo(APP_TITLE, "Choose a username first.")
            return
        engine = self.engine_var.get()
        pairs = self.available_voice_pairs(engine)
        label = self.speaker_voice_var.get()
        manual = next((vid for name, vid in pairs if name == label), None)
        with self.config_lock:
            assigned = self.voice_roster.choose(username, self.engine_key(engine), [v for _, v in pairs])
        if engine == LOCAL_ENGINE and not has_model(self.model_var.get()):
            self.append("Install Kokoro first using Download local voices.")
            return
        self.enqueue_speech(f"Hello, I am {username}. This is my chat voice.",
                            speaker=username, engine=engine, voice=manual or assigned)
        self.append(f"Testing {username}'s voice...")

    def selected_voice_id(self):
        display = self.voice_var.get()
        for name, id_ in self.voices:
            if name == display:
                return id_
        # Old v2 pyttsx3 registry IDs are not SAPI's voice names. Use the
        # Windows default instead of accidentally selecting a nonexistent ID.
        return ""

    def change_tts_options(self):
        with self.config_lock:
            self.speech_opts = {"rate": int(self.rate_var.get()), "voice": self.selected_voice_id(),
                                "engine": self.engine_var.get(), "model": self.model_var.get()}

    def test_voice(self):
        self.change_tts_options()
        # Preview three different voices without adding fake usernames to the roster.
        engine = self.engine_var.get()
        palette = self.available_voices(engine)
        if engine == LOCAL_ENGINE and not has_model(self.model_var.get()):
            self.append("⚠  Download Kokoro model files first using Download local voices.")
            return
        for index, name in enumerate(("Alex", "Sam", "Charlie")):
            choice = palette[index % len(palette)] if palette else ""
            self.enqueue_speech(f"Hello, this is {name}. Each person has a separate voice.",
                                speaker=name, engine=engine, voice=choice)
        self.append("Queued 3 local speech tests. Watch for Generating locally / Playing local / SPOKEN below.")

    def test_audio(self):
        """Test Windows audio without the OCR or online speech service."""
        def worker():
            self.events.put(("speech_notice", "Testing Windows output with a beep…"))
            try:
                play_test_tone()
                self.events.put(("speech_notice", "Test beep finished. If silent, check Windows output device and Volume mixer."))
            except SpeechError as exc:
                self.events.put(("error", f"Windows audio test FAILED: {exc}"))
        threading.Thread(target=worker, name="AudioTest", daemon=True).start()

    def configure_update_repo(self):
        proposed = simpledialog.askstring(
            "GitHub updates",
            "Paste your PUBLIC GitHub repository URL or USERNAME/habbo-chat-reader.\n"
            "Upload app files to the repository root on the main branch first.",
            initialvalue=self.update_repo or "",
            parent=self.root,
        )
        if proposed is None:
            return
        try:
            repo = normalize_repo(proposed)
            save_repo(repo)
        except (UpdateError, OSError) as exc:
            messagebox.showerror("GitHub updater", str(exc), parent=self.root)
            return
        self.update_repo = repo
        self.update_label.configure(text=f"GitHub: {repo}")
        self.append(f"GitHub update repository: {repo}")
        self.check_for_updates(silent=True)

    def check_for_updates(self, silent=False):
        if not self.update_repo:
            if not silent:
                self.configure_update_repo()
            return
        if self.update_busy:
            return
        self.update_busy = True
        self.update_label.configure(text="Checking GitHub...")
        def worker():
            try:
                available = check_update(self.update_repo)
                self.events.put(("update_checked", (available, silent)))
            except Exception as exc:
                self.events.put(("update_error", str(exc)))
        threading.Thread(target=worker, name="UpdateCheck", daemon=True).start()

    def offer_update(self, remote):
        approved = messagebox.askyesno(
            "Habbo Reader update",
            f"Habbo Chat Reader v{remote} is available on {self.update_repo}.\n\n"
            "Download, install and restart now?\n\n"
            "Your local voice models and settings will be preserved.",
            parent=self.root,
        )
        if not approved:
            return
        self.update_busy = True
        self.update_label.configure(text=f"Downloading v{remote}...")
        self.append(f"Downloading update v{remote} from GitHub...")
        def worker():
            try:
                archive = download_update(self.update_repo, remote)
                # Validate the downloaded snapshot fully before closing the app.
                version_files(archive, remote)
                self.events.put(("update_ready", (str(archive), remote)))
            except Exception as exc:
                self.events.put(("update_error", str(exc)))
        threading.Thread(target=worker, name="UpdateDownload", daemon=True).start()

    def apply_update(self, archive, remote):
        try:
            from pathlib import Path
            updater_source = Path(__file__).resolve().parent / "updater.py"
            temp_runner = Path(archive).parent / "habbo-updater.py"
            shutil.copy2(updater_source, temp_runner)
            args = [sys.executable, str(temp_runner), '--apply', '--pid', str(os.getpid()),
                    '--archive', archive, '--destination', str(Path(__file__).resolve().parent),
                    '--version', remote]
            flags = subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
            subprocess.Popen(args, cwd=str(Path(__file__).resolve().parent), creationflags=flags,
                             close_fds=True)
            self.close()
        except Exception as exc:
            self.append(f"Updater could not start: {exc}")
            self.update_busy = False
            messagebox.showerror("Update error", str(exc), parent=self.root)

    def process_events(self):
        for _ in range(60):
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "update_checked":
                self.update_busy = False
                remote, silent = value
                self.update_label.configure(text=f"GitHub: {self.update_repo}")
                if remote:
                    self.append(f"Update available: v{remote}")
                    self.offer_update(remote)
                elif not silent:
                    self.append(f"Already up to date (v{APP_VERSION}).")
                    messagebox.showinfo("Updates", f"You are running the latest version: v{APP_VERSION}", parent=self.root)
            elif event == "update_ready":
                archive, remote = value
                self.append(f"Validated update v{remote}. Restarting to install...")
                self.apply_update(archive, remote)
                return
            elif event == "update_error":
                self.update_busy = False
                self.update_label.configure(text="GitHub update check failed")
                self.append("GitHub update error: " + value)
                messagebox.showwarning("GitHub updater", value, parent=self.root)
            elif event == "settings_request":
                value.put(self.settings_snapshot())
            elif event == "speaker_seen":
                self.register_speaker(value)
            elif event == "message":
                self.append("📝  Detected / queued: " + value)
            elif event == "status":
                self.append(value)
            elif event == "speech_started":
                self.append("🔊  SPEAKING: " + value)
            elif event == "speech_done":
                self.append("✓  SPOKEN: " + value)
            elif event == "speech_notice":
                self.append("⚠  " + value)
            elif event == "error":
                self.append("⚠  " + value)
            elif event == "bubble_preview":
                bubbles, calls = value
                self.append(f"Found {len(bubbles)} readable speech bubbles ({calls} new OCR scans):")
                for b in bubbles[-18:]:
                    self.append(f"  {b.line}")
                if not bubbles:
                    self.append("No bubbles recognised. Select a tightly cropped stack of fully visible bubbles.")
            elif event == "ocr_test":
                lines = clean_lines(value)
                if lines:
                    self.append("Filtered chat preview (" + str(len(lines)) + " messages; other screen text ignored):")
                    for line in lines[-15:]:
                        self.append("  " + line)
                else:
                    self.append("No 'username: message' lines found. Crop the chat history more tightly, try Pixel text (high contrast), or adjust Habbo zoom.")
            elif event == "model_progress":
                self.model_status.configure(text=value, fg=SUBTLE)
                self.append("Model setup: " + value)
            elif event == "model_ready":
                self.download_button.configure(state="normal")
                self.update_model_status()
                self.append("Local Kokoro voices installed. Click Test 3 speakers!")
            elif event == "model_error":
                self.download_button.configure(state="normal")
                self.update_model_status()
                self.append("⚠  Model setup failed: " + value)
                messagebox.showerror(APP_TITLE, "Local model download failed:\n\n" + value)
            elif event == "voices":
                self.voices = value
                self.voice_box["values"] = [name for name, _ in value]
                preselected = next((name for name, id_ in value
                                    if id_ == self.settings.get("voice_id")), None)
                if preselected or value:
                    self.voice_var.set(preselected or value[0][0])
                else:
                    self.voice_var.set("Windows default voice")
                    self.tts_warning.configure(text="No Windows voices found. Install an English Windows speech voice in Settings > Time & Language > Speech.")
                self.change_tts_options()
                self.update_voice_choices()
        if not self.exit_event.is_set():
            self.root.after(110, self.process_events)

    def close(self):
        self._start_pending = False
        self.scan_active.clear()
        self.exit_event.set()
        self.save_settings()
        self.root.destroy()


def main():
    try:
        root = tk.Tk()
        HabboReader(root)
        root.mainloop()
    except Exception as exc:
        traceback.print_exc()
        try:
            tk.Tk().withdraw()
            messagebox.showerror("Habbo Chat Reader", f"Unexpected startup error:\n\n{exc}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
