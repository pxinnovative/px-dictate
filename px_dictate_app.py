#!/usr/bin/env python3
"""
PX Dictate — Free macOS voice-to-text powered by local Whisper AI.

Hotkeys (configurable via menu):
- fn short press (0.5-1.5s + release): Toggle start/stop recording
- fn long hold (1.5s+ without releasing): Hold-to-record mode (release = stop)
- Tap Control (solo): Pause → process segment, tap again to resume
- ESC: Stop recording
- Click mini pill: Expand hint → click again to start
- Click widget during recording: Stop recording

Prerequisites:
- System Settings → Keyboard → "Press fn key to" → "Do Nothing"
- System Settings → Privacy & Security → Accessibility → Terminal ON
"""
from __future__ import annotations

import collections
import datetime
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
import urllib.request
import webbrowser
from xml.sax.saxutils import escape as xml_escape

import objc
import pyaudio
import rumps

import AppKit
import Quartz
from Foundation import NSUserDefaults

import logging

# Debug log for diagnosing issues in .app bundle
os.makedirs(os.path.expanduser("~/Library/Application Support/PX Dictate"), exist_ok=True)
_LOG_FILE = os.path.join(os.path.expanduser("~/Library/Application Support/PX Dictate"), "debug.log")
logging.basicConfig(
    filename=_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("pxdictate")
_log.info("=== PX Dictate starting ===")
_log.info("WHISPER_CLI will be resolved after config section")

# ── App Info ────────────────────────────────────────────────────────────
APP_NAME = "PX Dictate"
APP_VERSION = "1.1.0"
APP_BUNDLE_ID = "com.pxinnovative.pxdictate"
APP_AUTHOR = "Victor Kerber"
APP_COMPANY = "PX Innovative Solutions Inc."
APP_GITHUB = "https://github.com/pxinnovative/px-dictate"
APP_DONATE = "https://buymeacoffee.com/pxinnovative"

# ── Config ──────────────────────────────────────────────────────────────
_MODELS_DIR = os.path.expanduser("~/.px-dictate/models")
_MODEL_SIZES = ["tiny", "base", "small", "medium", "large-v3"]
_MODEL_LABELS = {
    "tiny": "Tiny (75 MB — fast, lower quality)",
    "base": "Base (142 MB — fast, fair quality)",
    "small": "Small (466 MB — balanced)",
    "medium": "Medium (1.5 GB — slow, very good)",
    "large-v3": "Large v3 (3.1 GB — slowest, best quality)",
}

def _model_path_for(name: str) -> str:
    return os.path.join(_MODELS_DIR, f"ggml-{name}.bin")

def _available_models() -> list[str]:
    return [m for m in _MODEL_SIZES if os.path.exists(_model_path_for(m))]

# Default model — resolved after PrefsManager loads (see PXDictateApp.__init__)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "")
if not WHISPER_MODEL:
    _default = _model_path_for("small")
    if os.path.exists(_default):
        WHISPER_MODEL = _default
    else:
        WHISPER_MODEL = _default  # will trigger missing-model alert later

# Prompt hint for better punctuation and bilingual code-switching.
# Whisper uses this as "previous context" — well-punctuated text conditions
# the model to produce punctuated output. Bilingual text helps code-switching.
WHISPER_PROMPT = (
    "Hello, how are you? I'm doing great, thanks for asking. "
    "Hola, ¿cómo estás? Muy bien, gracias por preguntar. "
    "The meeting is at 3 p.m., and we'll discuss the project updates. "
    "La reunión es a las 3, y vamos a revisar los avances del proyecto."
)
_CLI_SEARCH = [
    "/opt/homebrew/bin/whisper-cli",
    "/usr/local/bin/whisper-cli",
    shutil.which("whisper-cli") or "",
]
WHISPER_CLI = os.environ.get("WHISPER_CLI", "")
if not WHISPER_CLI:
    for _c in _CLI_SEARCH:
        if _c and os.path.exists(_c):
            WHISPER_CLI = _c
            break
    else:
        WHISPER_CLI = "whisper-cli"

# .app bundles have minimal PATH — ensure Homebrew paths are available
_BREW_PATHS = ["/opt/homebrew/bin", "/usr/local/bin"]
_env_path = os.environ.get("PATH", "/usr/bin:/bin")
_missing = [p for p in _BREW_PATHS if p not in _env_path]
if _missing:
    os.environ["PATH"] = ":".join(_missing) + ":" + _env_path

_FFMPEG_SEARCH = [
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    shutil.which("ffmpeg") or "",
]
FFMPEG_BIN = ""
for _f in _FFMPEG_SEARCH:
    if _f and os.path.exists(_f):
        FFMPEG_BIN = _f
        break
if not FFMPEG_BIN:
    FFMPEG_BIN = "ffmpeg"

# Log resolved paths for debugging
_log.info("WHISPER_CLI=%s (exists=%s)", WHISPER_CLI, os.path.exists(WHISPER_CLI))
_log.info("WHISPER_MODEL=%s (exists=%s)", WHISPER_MODEL, os.path.exists(WHISPER_MODEL))
_log.info("FFMPEG_BIN=%s (exists=%s)", FFMPEG_BIN, os.path.exists(FFMPEG_BIN))


SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 1024
SENSITIVITY = float(os.environ.get("PX_DICTATE_SENSITIVITY", "8.0"))
DEFAULT_LANG = os.environ.get("PX_DICTATE_LANG", "auto")
HISTORY_MAX = 10

MIN_RECORDING_SECS = 2.0  # discard recordings shorter than this to avoid Whisper hallucinations

SILENCE_TIMEOUT = 20  # seconds of silence before auto-cancel
SILENCE_COUNTDOWN = 3  # countdown seconds before cancel
SILENCE_THRESHOLD = 0.02  # audio level below this = silence

FN_FLAG = 0x800000
FN_HOLD_THRESHOLD = 0.5
FN_LONG_HOLD = 1.5
CTRL_FLAG = 0x40000
OPT_FLAG = 0x80000
ESC_KEYCODE = 53
Q_KEYCODE = 12
R_KEYCODE = 15
CMD_FLAG = 0x100000
V_KEYCODE = 9
F5_KEYCODE = 96
DOUBLE_TAP_THRESHOLD = 0.4  # seconds for double-tap detection
CTRL_TAP_THRESHOLD = 1.0

WHISPER_THREADS = 8
WHISPER_TIMEOUT = 180
HINT_COLLAPSE_DELAY = 4
MSG_ALTERNATE_DELAY = 3.5
FINALIZE_DELAY = 1.2
PASTE_DELAY = 0.2
FFMPEG_TIMEOUT = 60

LAUNCHAGENT_LOG_DIR = os.path.expanduser("~/Library/Logs/PX Dictate")
LAUNCHAGENT_LOG_PATH = os.path.join(LAUNCHAGENT_LOG_DIR, "launchagent.log")

MENUBAR_H = 37
WIDGET_OFFSET = 3
PILL_W = 180
PILL_H = 40
HINT_W = 210
HINT_H = 46
MINI_W = 56
MINI_H = 22
MINI_HOVER_W = 62
MINI_HOVER_H = 25
REC_PILL_W = 220
REC_PILL_H = 44

BAR_INSET = 12
CORNER_RADIUS_PILL = 11.0
CORNER_RADIUS_WIDGET = 14.0
CORNER_RADIUS_PANEL = 16.0
CORNER_RADIUS_SMALL = 4.0
CORNER_RADIUS_BUTTON = 7.0
MIN_MENUBAR_H = 24

# ── VU Meter Thresholds ──────────────────────────────────────────────────
VU_THRESHOLD_LOW = 0.4
VU_THRESHOLD_HIGH = 0.7

ALPHA_MINI = 0.45
ALPHA_HOVER = 0.75
ALPHA_EXPANDED = 1.0

# ── Pill Themes ─────────────────────────────────────────────────────────
THEMES = {
    "classic": {
        "name": "Classic",
        "material": "HUDWindow",
        "alpha_mini": 0.7,
        "alpha_hover": 0.9,
        "alpha_expanded": 1.0,
        "corner_radius_pill": 8.0,
        "corner_radius_panel": 10.0,
        "button_corner": 4.0,
        "dot_dark": (0.9, 0.9, 0.9),
        "dot_light": (0.15, 0.15, 0.15),
        "dot_hover_dark": (1.0, 1.0, 1.0),
        "dot_hover_light": (0.0, 0.0, 0.0),
        "text_color": (1.0, 1.0, 1.0),
        "hint_text_color": (0.95, 0.95, 0.95),
        "text_color_light": (0.0, 0.0, 0.0),
        "hint_text_color_light": (0.15, 0.15, 0.15),
        "button_bg": (0.25, 0.25, 0.25, 0.85),
        "button_bg_hover": (0.25, 0.25, 0.25, 0.95),
        "stop_bg": (1.0, 0.15, 0.15, 0.9),
        "rec_bg": (0.95, 0.05, 0.05, 0.95),
        "pause_resume_bg": (0.1, 0.6, 0.2, 0.85),
        "bar_bg": (0.1, 0.1, 0.1, 0.75),
        "vu_color_low": (0.2, 0.9, 0.3),
        "vu_color_mid": (1.0, 0.85, 0.0),
        "vu_color_high": (1.0, 0.15, 0.15),
        "key_bg": (0.3, 0.3, 0.3, 0.9),
        "key_bg_light": (0.75, 0.75, 0.75, 0.6),
        "border_width": 1,
        "border_color": (0.4, 0.4, 0.4, 0.5),
        "shadow_radius": 0,
    },
    "glass": {
        "name": "Glass",
        "material": "UnderWindowBackground",
        "alpha_mini": 0.25,
        "alpha_hover": 0.55,
        "alpha_expanded": 0.85,
        "corner_radius_pill": 16.0,
        "corner_radius_panel": 22.0,
        "button_corner": 8.0,
        "dot_dark": (0.9, 0.9, 0.95),
        "dot_light": (0.3, 0.3, 0.35),
        "dot_hover_dark": (1.0, 1.0, 1.0),
        "dot_hover_light": (0.1, 0.1, 0.15),
        "text_color": (1.0, 1.0, 1.0),
        "hint_text_color": (0.92, 0.92, 0.95),
        "text_color_light": (0.0, 0.0, 0.05),
        "hint_text_color_light": (0.1, 0.1, 0.15),
        "button_bg": (0.6, 0.6, 0.65, 0.35),
        "button_bg_hover": (0.6, 0.6, 0.65, 0.55),
        "stop_bg": (0.85, 0.35, 0.35, 0.5),
        "rec_bg": (0.8, 0.25, 0.25, 0.55),
        "pause_resume_bg": (0.25, 0.7, 0.4, 0.5),
        "bar_bg": (0.4, 0.4, 0.45, 0.25),
        "vu_color_low": (0.45, 0.88, 0.55),
        "vu_color_mid": (0.95, 0.88, 0.45),
        "vu_color_high": (0.95, 0.5, 0.45),
        "key_bg": (0.6, 0.6, 0.65, 0.4),
        "key_bg_light": (0.8, 0.8, 0.85, 0.5),
        "border_width": 1,
        "border_color": (0.8, 0.8, 0.85, 0.3),
        "shadow_radius": 0,
    },
    "minimal": {
        "name": "Minimal",
        "material": "Popover",
        "alpha_mini": 0.15,
        "alpha_hover": 0.4,
        "alpha_expanded": 0.6,
        "corner_radius_pill": 20.0,
        "corner_radius_panel": 26.0,
        "button_corner": 10.0,
        "dot_dark": (0.5, 0.5, 0.5),
        "dot_light": (0.4, 0.4, 0.4),
        "dot_hover_dark": (0.8, 0.8, 0.8),
        "dot_hover_light": (0.2, 0.2, 0.2),
        "text_color": (0.85, 0.85, 0.85),
        "hint_text_color": (0.7, 0.7, 0.7),
        "text_color_light": (0.1, 0.1, 0.1),
        "hint_text_color_light": (0.25, 0.25, 0.25),
        "button_bg": (0.4, 0.4, 0.4, 0.25),
        "button_bg_hover": (0.4, 0.4, 0.4, 0.45),
        "stop_bg": (0.6, 0.25, 0.25, 0.3),
        "rec_bg": (0.55, 0.15, 0.15, 0.35),
        "pause_resume_bg": (0.2, 0.4, 0.25, 0.3),
        "bar_bg": (0.25, 0.25, 0.25, 0.15),
        "vu_color_low": (0.6, 0.6, 0.6),
        "vu_color_mid": (0.75, 0.75, 0.75),
        "vu_color_high": (0.9, 0.9, 0.9),
        "key_bg": (0.25, 0.25, 0.25, 0.6),
        "key_bg_light": (0.7, 0.7, 0.7, 0.5),
        "border_width": 0,
        "border_color": (0.5, 0.5, 0.5, 0.0),
        "shadow_radius": 8.0,
    },
}

SETUP_DONE_KEY = "setup_completed_v1"

DEFAULT_SAVE_DIR = os.path.expanduser("~/Downloads")
DICTATIONS_FOLDER = "Dictations"

LAUNCH_AGENT_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{APP_BUNDLE_ID}.plist"
)

APP_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/PX Dictate")
PREFS_FILE = os.path.join(APP_SUPPORT_DIR, "preferences.json")
HISTORY_FILE = os.path.join(APP_SUPPORT_DIR, "history.json")

LANGUAGE_NAMES = {
    "auto": "Auto-detect", "en": "English", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian",
    "pt": "Portuguese", "zh": "Chinese", "ja": "Japanese",
    "ko": "Korean", "ar": "Arabic", "hi": "Hindi",
    "ru": "Russian", "nl": "Dutch", "pl": "Polish",
    "tr": "Turkish",
}


# ── Preferences Persistence ───────────────────────────────────────────

class PrefsManager:
    """Load/save user preferences to ~/Library/Application Support/PX Dictate/."""

    DEFAULTS = {
        "lang": "auto",
        "model": "small",
        "auto_paste": True,
        "sounds_enabled": True,
        "save_audio": False,
        "save_transcripts": False,
        "save_dir": DEFAULT_SAVE_DIR,
        "hotkey": "fn",
        "record_system_sounds": True,
        "theme": "glass",
    }

    def __init__(self):
        os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
        self._prefs = dict(self.DEFAULTS)
        self._load()

    def _load(self):
        try:
            with open(PREFS_FILE, "r") as f:
                saved = json.load(f)
            for k in self.DEFAULTS:
                if k in saved:
                    self._prefs[k] = saved[k]
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def save(self):
        try:
            with open(PREFS_FILE, "w") as f:
                json.dump(self._prefs, f, indent=2)
        except OSError:
            pass

    def get(self, key):
        return self._prefs.get(key, self.DEFAULTS.get(key))

    def set(self, key, value):
        self._prefs[key] = value
        self.save()

    @staticmethod
    def load_history():
        try:
            with open(HISTORY_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    @staticmethod
    def save_history(entries):
        try:
            os.makedirs(APP_SUPPORT_DIR, exist_ok=True)
            with open(HISTORY_FILE, "w") as f:
                json.dump(entries[-HISTORY_MAX:], f, indent=2)
        except OSError:
            pass

_SYS_SOUNDS = "/System/Library/Components/CoreAudio.component/Contents/SharedSupport/SystemSounds/system"
SOUND_FILES = {
    "start": f"{_SYS_SOUNDS}/begin_record.caf",
    "stop": f"{_SYS_SOUNDS}/end_record.caf",
    "pause": f"{_SYS_SOUNDS}/media_paused.caf",
    "unpause": f"{_SYS_SOUNDS}/begin_record.caf",
    "pasted": f"{_SYS_SOUNDS}/SentMessage.caf",
    "error": None,
}

_WHISPER_ARTIFACTS = re.compile(
    r"\[BLANK_AUDIO\]|\[silence\]|\(silence\)|\[inaudible\]|\(inaudible\)",
    re.IGNORECASE,
)


def _add_punctuation(text: str) -> str:
    """Add basic punctuation if whisper omitted it.

    Only adds a period at the very end if no terminal punctuation exists.
    Does NOT try to insert commas or periods mid-sentence — that risks
    breaking correctly transcribed text. The --prompt hint handles most
    mid-sentence punctuation; this is just a safety net for the final char.
    """
    text = text.strip()
    if not text:
        return text
    # Capitalize first letter
    text = text[0].upper() + text[1:]
    # Ensure terminal punctuation
    if text[-1] not in '.!?…':
        text += '.'
    return text


# ── Audio helpers ───────────────────────────────────────────────────────

def rms_level(data: bytes) -> float:
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", data)
    sum_sq = sum(s * s for s in samples)
    return min(math.sqrt(sum_sq / count) / 32768.0, 1.0)


_sound_cache = {}
_sounds_enabled = True
_recording_active_ref = [False]
_record_sys_sounds_ref = [True]


def _init_sounds():
    for name, path in SOUND_FILES.items():
        if path:
            s = AppKit.NSSound.alloc().initWithContentsOfFile_byReference_(path, True)
            if s:
                _sound_cache[name] = s
    s = AppKit.NSSound.soundNamed_("Funk")
    if s:
        _sound_cache["error"] = s


def play_sound(name: str):
    if not _sounds_enabled:
        return
    # If recording and user doesn't want system sounds in recording, skip
    if _recording_active_ref[0] and not _record_sys_sounds_ref[0]:
        return
    s = _sound_cache.get(name)
    if s:
        def _play():
            s.stop()
            s.play()
        _on_main(_play)


def _check_dependencies():
    """Check if whisper-cli and model are available. Returns (ok, message)."""
    if not os.path.exists(WHISPER_CLI) and not shutil.which(WHISPER_CLI):
        return False, (
            "whisper-cli not found.\n\n"
            "Install it with:\n"
            "  brew install whisper-cpp\n\n"
            "Then restart PX Dictate."
        )
    if not os.path.exists(WHISPER_MODEL):
        return False, (
            f"Whisper model not found.\n\n"
            "Download it with:\n"
            "  mkdir -p ~/.px-dictate/models\n"
            "  curl -L -o ~/.px-dictate/models/ggml-small.bin \\\n"
            "    https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin\n\n"
            "Then restart PX Dictate."
        )
    return True, ""


def _is_dark_mode():
    """Detect if macOS is in dark mode."""
    try:
        appearance = AppKit.NSApp.effectiveAppearance()
        name = appearance.bestMatchFromAppearancesWithNames_([
            AppKit.NSAppearanceNameDarkAqua,
            AppKit.NSAppearanceNameAqua,
        ])
        return name == AppKit.NSAppearanceNameDarkAqua
    except Exception:
        return True  # default to dark


def _is_accessibility_granted():
    """Check if Accessibility permission is granted."""
    try:
        return Quartz.AXIsProcessTrusted()
    except Exception:
        return False


def _is_setup_done():
    """Check if the first-run setup has been completed."""
    defaults = NSUserDefaults.standardUserDefaults()
    return bool(defaults.boolForKey_(SETUP_DONE_KEY))


def _mark_setup_done():
    """Mark the first-run setup as completed."""
    defaults = NSUserDefaults.standardUserDefaults()
    defaults.setBool_forKey_(True, SETUP_DONE_KEY)
    defaults.synchronize()


def _show_setup_window():
    """Show the onboarding wizard."""
    _show_wizard()


# ── Onboarding Wizard ──────────────────────────────────────────────────

WIZARD_W = 680
WIZARD_H = 620
WIZARD_DONT_SHOW_KEY = "wizard_dont_show_v1"


class _WizardHandler(AppKit.NSObject):
    """Module-level ObjC class for onboarding wizard button actions."""

    def initWithController_(self, controller):
        self = objc.super(_WizardHandler, self).init()
        if self is None:
            return None
        self.controller = controller
        return self

    def nextClicked_(self, sender):
        self.controller.next_page()

    def prevClicked_(self, sender):
        self.controller.prev_page()

    def endClicked_(self, sender):
        self.controller.close_wizard()

    def checkboxClicked_(self, sender):
        self.controller.toggle_dont_show(sender.state() == AppKit.NSControlStateValueOn)

    def actionClicked_(self, sender):
        tag = sender.tag()
        self.controller.handle_action(tag)


class OnboardingWizard:
    """Multi-page onboarding wizard like Amphetamine's welcome screen."""

    PAGES = [
        {
            "title": f"Welcome to {APP_NAME}",
            "subtitle": "Created with \u2764\ufe0f by PX Innovative.",
            "body": (
                f"{APP_NAME} is a free, open-source voice-to-text app for macOS.\n\n"
                "Powered by Whisper AI, it runs 100% locally on your Mac.\n\n"
                "NO CLOUD. NO SUBSCRIPTION. NO DATA LEAVES YOUR MAC.\n\n"
                f"Unlike Apple Dictation, {APP_NAME}:\n\n"
                "\u2022 Auto-detects language for each segment\n"
                "\u2022 Records full sessions with timestamps\n"
                "\u2022 Works everywhere, not just text fields\n"
                "\u2022 Saves audio (MP3) and transcripts automatically\n"
                "\u2022 Is completely open-source and verifiable\n\n"
                "It only takes a few short steps to set up.\nClick Next to get started."
            ),
            "emoji": "\U0001f399\ufe0f",
            "show_icon": True,
        },
        {
            "title": f"Where is {APP_NAME}?",
            "subtitle": f"{APP_NAME} lives in the menu bar and as a floating pill.",
            "body": (
                f"You'll find {APP_NAME}'s microphone icon (\U0001f399\ufe0f) in your menu bar\n"
                "at the top right corner of your screen. Click it to access\n"
                "all settings, history, and controls.\n\n"
                "FLOATING PILL:\n\n"
                "There's a floating pill (\u00b7 \u00b7 \u00b7) near the top center of your screen.\n"
                "This is your quick-access recording widget:\n\n"
                "\u2022 Hover to see it brighten\n"
                "\u2022 Click to expand and see the REC button\n"
                "\u2022 During recording, use \u23f8 to pause and \u23f9 to stop\n"
                "\u2022 The color bar shows your microphone level in real-time\n\n"
                f"{APP_NAME} is highly customizable. You can change the hotkey,\n"
                "language, save options, sounds, and more from the menu."
            ),
            "emoji": "\U0001f4cd",
        },
        {
            "title": "Quick Setup",
            "subtitle": "Two simple steps to get PX Dictate working perfectly.",
            "body": (
                "STEP 1 \u2014 Accessibility Permission\n\n"
                "Open System Settings \u2192 Privacy & Security \u2192 Accessibility.\n"
                "Click '+' and add PX Dictate. This allows the fn key to work\n"
                "as a global hotkey.\n\n"
                "If hotkeys stop working after an update, remove PX Dictate\n"
                "from the list and add it again.\n\n"
                "STEP 2 \u2014 Keyboard Setting\n\n"
                "Open System Settings \u2192 Keyboard.\n"
                "Set 'Press fn key to' \u2192 'Do Nothing'.\n"
                "This frees the fn key for PX Dictate to use.\n\n"
                "After both steps, restart PX Dictate and you're ready!\n"
                "Microphone permission is requested automatically."
            ),
            "emoji": "\U0001f527",
            "actions": [
                ("Open Accessibility Settings", 1),
                ("Open Keyboard Settings", 2),
            ],
        },
        {
            "title": "How to Use",
            "subtitle": "Recording, pausing, and keyboard shortcuts.",
            "body": (
                "RECORDING:\n\n"
                "\u2022 Tap fn to start/stop recording\n"
                "\u2022 Hold fn for 1.5s+ for hold-to-record (release = stop)\n"
                "\u2022 Or click the floating pill \u2192 click REC\n\n"
                "PAUSE & SEGMENTS:\n\n"
                "\u2022 Tap Control to pause \u2014 audio is transcribed instantly\n"
                "\u2022 Tap Control again to resume a new segment\n"
                "\u2022 Each segment gets its own timestamp\n\n"
                "AUTO-PASTE:\n\n"
                "\u2022 Transcribed text is automatically pasted into your active app\n"
                "\u2022 Disable this in the menu if you prefer manual pasting\n\n"
                "KEYBOARD SHORTCUTS:\n\n"
                "fn (tap)        \u2014 Toggle recording\n"
                "fn (hold 1.5s)  \u2014 Hold-to-record\n"
                "Control (tap)   \u2014 Pause & process segment\n"
                "ESC             \u2014 Stop recording\n"
                "\u2318Q              \u2014 Quit PX Dictate"
            ),
            "emoji": "\u2328\ufe0f",
        },
        {
            "title": "Pro Tip: Voice Isolation",
            "subtitle": "Dramatically improve transcription accuracy.",
            "body": (
                "macOS has a built-in feature called Voice Isolation that uses\n"
                "Apple's Neural Engine to filter background noise.\n\n"
                "This makes a HUGE difference for Whisper accuracy — especially\n"
                "with keyboard clicks, fans, music, or other people talking.\n\n"
                "HOW TO ENABLE:\n\n"
                "\u2022 Click the mic icon in your menu bar (appears during recording)\n"
                "\u2022 Or open Control Center \u2192 Mic Mode\n"
                "\u2022 Select 'Voice Isolation'\n\n"
                "REQUIREMENTS:\n\n"
                "\u2022 Apple Silicon Mac (M1 or later)\n"
                "\u2022 macOS 12 Monterey or later\n\n"
                "This is a system-wide setting — once enabled, it stays on\n"
                "for all apps until you change it. We highly recommend it!"
            ),
            "emoji": "\U0001f3a4",
        },
        {
            "title": f"You're all set!",
            "subtitle": f"Thanks for choosing {APP_NAME}.",
            "body": (
                f"We built {APP_NAME} because we needed it ourselves — and we\n"
                "use it every day. We hope it's just as useful for you.\n\n"
                f"{APP_NAME} is free, open-source, and community-driven.\n"
                "If you find it helpful, please consider:\n"
                "  \u2b50  Starring us on GitHub \u2014 helps others discover us\n"
                "  \u2615  Buying us a coffee \u2014 supports development\n\n"
                "Questions or issues? Visit our GitHub page \u2014 we read\n"
                "every issue and appreciate all feedback.\n\n"
                f"__{APP_AUTHOR}__\n"
                "**PX Innovative.**"
            ),
            "emoji": "\u2764\ufe0f",
            "actions": [
                ("\u2b50 Star on GitHub", 3),
                ("\u2615 Buy Us a Coffee", 4),
            ],
        },
    ]

    def __init__(self):
        self._page = 0
        self._window = None
        self._handler = None
        self._dont_show = False
        self._content_view = None
        self._title_field = None
        self._subtitle_field = None
        self._body_field = None
        self._emoji_field = None
        self._icon_view = None
        self._prev_btn = None
        self._next_btn = None
        self._checkbox = None
        self._action_btns = []

    def show(self):
        """Show the wizard on the main thread."""
        def _do():
            self._create_window()
            self._update_page()
            self._window.makeKeyAndOrderFront_(None)
            AppKit.NSApp.activateIgnoringOtherApps_(True)
        _on_main(_do)

    def _create_window(self):
        screen = AppKit.NSScreen.mainScreen()
        sx = screen.frame().origin.x + (screen.frame().size.width - WIZARD_W) / 2
        sy = screen.frame().origin.y + (screen.frame().size.height - WIZARD_H) / 2

        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((sx, sy), (WIZARD_W, WIZARD_H)),
            AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self._window.setTitle_(f"{APP_NAME} \u2014 Welcome")
        self._window.setLevel_(AppKit.NSFloatingWindowLevel)
        self._window.setReleasedWhenClosed_(False)

        self._handler = _WizardHandler.alloc().initWithController_(self)

        content = self._window.contentView()
        content.setWantsLayer_(True)

        # Layout constants for 680x620 window
        _TOP_Y = 230       # gray section starts lower — more room for body
        _EMOJI_Y = 520     # emoji near top
        _ICON_Y = 510      # icon near top
        _TITLE_Y = 440     # title tight under icon
        _SUBTITLE_Y = 418  # subtitle tight under title
        _SEP_Y = 410       # separator
        _BODY_Y = 58       # body text area
        _BODY_H = 348      # very tall body
        _ACTION_Y = 58     # action buttons
        _BOTTOM_H = 48     # bottom bar

        # Top area background (adapts to light/dark mode)
        top_bg = AppKit.NSView.alloc().initWithFrame_(((0, _TOP_Y), (WIZARD_W, WIZARD_H - _TOP_Y)))
        top_bg.setWantsLayer_(True)
        top_bg.layer().setBackgroundColor_(
            AppKit.NSColor.windowBackgroundColor().CGColor()
        )
        content.addSubview_(top_bg)

        # Emoji / icon area (compact — 32pt emoji, 80px icon)
        self._emoji_field = AppKit.NSTextField.alloc().initWithFrame_(((0, _EMOJI_Y), (WIZARD_W, 50)))
        self._emoji_field.setBezeled_(False)
        self._emoji_field.setDrawsBackground_(False)
        self._emoji_field.setEditable_(False)
        self._emoji_field.setSelectable_(False)
        self._emoji_field.setAlignment_(AppKit.NSTextAlignmentCenter)
        self._emoji_field.setFont_(AppKit.NSFont.systemFontOfSize_(32))
        content.addSubview_(self._emoji_field)

        # App icon (shown only on page 1)
        self._icon_view = AppKit.NSImageView.alloc().initWithFrame_(
            ((WIZARD_W / 2 - 40, _ICON_Y), (80, 80))
        )
        icon = AppKit.NSImage.imageNamed_("AppIcon")
        if not icon:
            bundle = AppKit.NSBundle.mainBundle()
            icon_path = bundle.pathForResource_ofType_("PXDictate", "icns")
            if icon_path:
                icon = AppKit.NSImage.alloc().initWithContentsOfFile_(icon_path)
        if icon:
            self._icon_view.setImage_(icon)
            self._icon_view.setImageScaling_(AppKit.NSImageScaleProportionallyUpOrDown)
        content.addSubview_(self._icon_view)

        # Separator line
        self._sep = AppKit.NSBox.alloc().initWithFrame_(((40, _SEP_Y), (WIZARD_W - 80, 1)))
        self._sep.setBoxType_(AppKit.NSBoxSeparator)
        content.addSubview_(self._sep)

        # Title
        self._title_field = AppKit.NSTextField.alloc().initWithFrame_(((40, _TITLE_Y), (WIZARD_W - 80, 28)))
        self._title_field.setBezeled_(False)
        self._title_field.setDrawsBackground_(False)
        self._title_field.setEditable_(False)
        self._title_field.setSelectable_(False)
        self._title_field.setAlignment_(AppKit.NSTextAlignmentCenter)
        self._title_field.setFont_(AppKit.NSFont.systemFontOfSize_weight_(24, AppKit.NSFontWeightLight))
        self._title_field.setTextColor_(AppKit.NSColor.labelColor())
        content.addSubview_(self._title_field)

        # Subtitle
        self._subtitle_field = AppKit.NSTextField.alloc().initWithFrame_(((40, _SUBTITLE_Y), (WIZARD_W - 80, 18)))
        self._subtitle_field.setBezeled_(False)
        self._subtitle_field.setDrawsBackground_(False)
        self._subtitle_field.setEditable_(False)
        self._subtitle_field.setSelectable_(False)
        self._subtitle_field.setAlignment_(AppKit.NSTextAlignmentCenter)
        self._subtitle_field.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        self._subtitle_field.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        content.addSubview_(self._subtitle_field)

        # Body text (scrollable — generous height)
        self._scroll = AppKit.NSScrollView.alloc().initWithFrame_(((80, _BODY_Y), (WIZARD_W - 160, _BODY_H)))
        self._scroll.setHasVerticalScroller_(True)
        self._scroll.setHasHorizontalScroller_(False)
        self._scroll.setBorderType_(AppKit.NSNoBorder)
        self._scroll.setDrawsBackground_(False)

        self._body_field = AppKit.NSTextView.alloc().initWithFrame_(((0, 0), (WIZARD_W - 180, _BODY_H)))
        self._body_field.setEditable_(False)
        self._body_field.setSelectable_(True)
        self._body_field.setDrawsBackground_(False)
        self._body_field.setFont_(AppKit.NSFont.systemFontOfSize_(13.5))
        self._body_field.setTextContainerInset_(AppKit.NSMakeSize(10, 5))
        self._body_field.textContainer().setLineFragmentPadding_(5)
        self._body_field.setAlignment_(AppKit.NSTextAlignmentLeft)

        self._scroll.setDocumentView_(self._body_field)
        content.addSubview_(self._scroll)

        # Action buttons area (below body, for setup/thank you pages)
        for i in range(2):
            btn = AppKit.NSButton.alloc().initWithFrame_(((WIZARD_W/2 - 200 + i*210, _ACTION_Y), (200, 28)))
            btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
            btn.setTarget_(self._handler)
            btn.setAction_(objc.selector(self._handler.actionClicked_, signature=b'v@:@'))
            btn.setHidden_(True)
            content.addSubview_(btn)
            self._action_btns.append(btn)

        # Bottom bar background (adapts to light/dark mode)
        bottom_bg = AppKit.NSView.alloc().initWithFrame_(((0, 0), (WIZARD_W, _BOTTOM_H)))
        bottom_bg.setWantsLayer_(True)
        bottom_bg.layer().setBackgroundColor_(
            AppKit.NSColor.separatorColor().CGColor()
        )
        content.addSubview_(bottom_bg)

        # Bottom separator
        sep2 = AppKit.NSBox.alloc().initWithFrame_(((0, _BOTTOM_H - 1), (WIZARD_W, 1)))
        sep2.setBoxType_(AppKit.NSBoxSeparator)
        content.addSubview_(sep2)

        # "Don't show this window again" checkbox
        self._checkbox = AppKit.NSButton.alloc().initWithFrame_(((20, 12), (250, 25)))
        self._checkbox.setButtonType_(AppKit.NSButtonTypeSwitch)
        self._checkbox.setTitle_("Don't show this window again")
        self._checkbox.setFont_(AppKit.NSFont.systemFontOfSize_(12))
        self._checkbox.setState_(AppKit.NSControlStateValueOff)
        self._checkbox.setTarget_(self._handler)
        self._checkbox.setAction_(objc.selector(self._handler.checkboxClicked_, signature=b'v@:@'))
        content.addSubview_(self._checkbox)

        # Previous button
        self._prev_btn = AppKit.NSButton.alloc().initWithFrame_(((WIZARD_W - 220, 10), (90, 30)))
        self._prev_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        self._prev_btn.setTitle_("Previous")
        self._prev_btn.setTarget_(self._handler)
        self._prev_btn.setAction_(objc.selector(self._handler.prevClicked_, signature=b'v@:@'))
        content.addSubview_(self._prev_btn)

        # Next/End button
        self._next_btn = AppKit.NSButton.alloc().initWithFrame_(((WIZARD_W - 120, 10), (90, 30)))
        self._next_btn.setBezelStyle_(AppKit.NSBezelStyleRounded)
        self._next_btn.setTitle_("Next")
        self._next_btn.setTarget_(self._handler)
        self._next_btn.setAction_(objc.selector(self._handler.nextClicked_, signature=b'v@:@'))
        self._next_btn.setKeyEquivalent_("\r")  # Enter key
        content.addSubview_(self._next_btn)

    def _set_body_text(self, page):
        """Set body text with rich formatting — bold headers, bold keys."""
        body = page.get("body", "")
        attr = AppKit.NSMutableAttributedString.alloc().init()
        normal = AppKit.NSFont.systemFontOfSize_(13.5)
        bold = AppKit.NSFont.systemFontOfSize_weight_(13.5, AppKit.NSFontWeightBold)
        semi = AppKit.NSFont.systemFontOfSize_weight_(13.5, AppKit.NSFontWeightSemibold)
        black = AppKit.NSColor.labelColor()
        gray = AppKit.NSColor.secondaryLabelColor()

        for line in body.split("\n"):
            stripped = line.strip()
            if not stripped:
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "\n", {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(8)}
                    )
                )
                continue

            # Section headers (ALL CAPS or lines ending with colon that aren't bullets)
            if stripped.isupper() or (stripped.endswith(":") and not stripped.startswith("\u2022")):
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        stripped + "\n",
                        {AppKit.NSFontAttributeName: bold, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
            # STEP lines
            elif stripped.startswith("STEP"):
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        stripped + "\n",
                        {AppKit.NSFontAttributeName: bold, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
            # Bullet points
            elif stripped.startswith("\u2022"):
                # Remove leading bullet and space
                content = stripped[1:].strip()
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "  \u2022  ",
                        {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        content + "\n",
                        {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
            # Keyboard shortcut lines (contain em-dash after key name)
            elif "\u2014" in stripped and not stripped.startswith("STEP") and any(k in stripped for k in ["fn", "Control", "ESC", "\u2318"]):
                parts = stripped.split("\u2014", 1)
                key_part = parts[0].strip()
                desc_part = parts[1].strip() if len(parts) > 1 else ""
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "  " + key_part + "  ",
                        {AppKit.NSFontAttributeName: bold, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "\u2014 " + desc_part + "\n",
                        {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: gray}
                    )
                )
            # Italic text (__text__)
            elif stripped.startswith("__") and stripped.endswith("__"):
                text = stripped[2:-2]
                italic = AppKit.NSFontManager.sharedFontManager().convertFont_toHaveTrait_(normal, AppKit.NSItalicFontMask)
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        text + "\n",
                        {AppKit.NSFontAttributeName: italic, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
            # Bold text (**text**)
            elif stripped.startswith("**") and stripped.endswith("**"):
                text = stripped[2:-2]
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        text + "\n",
                        {AppKit.NSFontAttributeName: bold, AppKit.NSForegroundColorAttributeName: black}
                    )
                )
            # Normal text
            else:
                attr.appendAttributedString_(
                    AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        stripped + "\n",
                        {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: black}
                    )
                )

        self._body_field.textStorage().setAttributedString_(attr)

    def _update_page(self):
        if not self._window:
            return
        page = self.PAGES[self._page]

        # Update emoji/icon
        show_icon = page.get("show_icon", False)
        self._icon_view.setHidden_(not show_icon)
        if show_icon:
            self._emoji_field.setHidden_(True)
        else:
            self._emoji_field.setHidden_(False)
            self._emoji_field.setStringValue_(page.get("emoji", ""))
            # Use larger font for keyboard emoji
            if page.get("emoji") == "\u2328\ufe0f":
                self._emoji_field.setFont_(AppKit.NSFont.systemFontOfSize_(60))
            else:
                self._emoji_field.setFont_(AppKit.NSFont.systemFontOfSize_(32))

        # Adjust layout: pages with icon use original Y, pages with emoji shift up
        if show_icon:
            title_y, subtitle_y, sep_y, body_h = 440, 418, 410, 348
        else:
            title_y, subtitle_y, sep_y, body_h = 490, 468, 460, 398
        self._title_field.setFrame_(((40, title_y), (WIZARD_W - 80, 28)))
        self._subtitle_field.setFrame_(((40, subtitle_y), (WIZARD_W - 80, 18)))
        self._sep.setFrame_(((40, sep_y), (WIZARD_W - 80, 1)))
        self._scroll.setFrame_(((80, 58), (WIZARD_W - 160, body_h)))
        self._body_field.setFrame_(((0, 0), (WIZARD_W - 180, body_h)))

        # Update text
        self._title_field.setStringValue_(page["title"])
        self._subtitle_field.setStringValue_(page["subtitle"])
        self._set_body_text(page)
        # Reset scroll to top
        self._body_field.scrollRangeToVisible_(AppKit.NSMakeRange(0, 0))

        # Action buttons
        actions = page.get("actions", [])
        for i, btn in enumerate(self._action_btns):
            if i < len(actions):
                btn.setTitle_(actions[i][0])
                btn.setTag_(actions[i][1])
                btn.setHidden_(False)
            else:
                btn.setHidden_(True)

        # Navigation buttons
        self._prev_btn.setHidden_(self._page == 0)
        is_last = self._page == len(self.PAGES) - 1
        self._next_btn.setTitle_("End" if is_last else "Next")
        if is_last:
            self._next_btn.setAction_(objc.selector(self._handler.endClicked_, signature=b'v@:@'))
        else:
            self._next_btn.setAction_(objc.selector(self._handler.nextClicked_, signature=b'v@:@'))

    def next_page(self):
        if self._page < len(self.PAGES) - 1:
            self._page += 1
            def _do():
                self._update_page()
            _on_main(_do)

    def prev_page(self):
        if self._page > 0:
            self._page -= 1
            def _do():
                self._update_page()
            _on_main(_do)

    def close_wizard(self):
        if self._dont_show:
            defaults = NSUserDefaults.standardUserDefaults()
            defaults.setBool_forKey_(True, WIZARD_DONT_SHOW_KEY)
            defaults.synchronize()
        if _is_accessibility_granted():
            _mark_setup_done()
        if self._window:
            self._window.close()

    def toggle_dont_show(self, value):
        self._dont_show = value

    def handle_action(self, tag):
        if tag == 1:
            subprocess.run([
                "open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
            ], check=False)
        elif tag == 2:
            subprocess.run([
                "open", "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"
            ], check=False)
        elif tag == 3:
            webbrowser.open(APP_GITHUB)
        elif tag == 4:
            webbrowser.open(APP_DONATE)
        # Refresh accessibility status after opening settings
        if tag in (1, 2):
            def _refresh():
                time.sleep(1)
                def _do():
                    self._update_page()
                _on_main(_do)
            threading.Thread(target=_refresh, daemon=True).start()


def _should_show_wizard():
    """Check if the onboarding wizard should be shown."""
    defaults = NSUserDefaults.standardUserDefaults()
    if defaults.boolForKey_(WIZARD_DONT_SHOW_KEY):
        return False
    # Show if Accessibility not granted OR setup not done
    if not _is_accessibility_granted() or not _is_setup_done():
        return True
    return False


def _show_wizard():
    """Show the onboarding wizard."""
    wizard = OnboardingWizard()
    wizard.show()


def transcribe(audio_path: str, lang: str = "auto") -> str:
    # NOTE: This does NOT use transcriber.py's Transcriber class because the app
    # needs custom prompt support (WHISPER_PROMPT), artifact cleaning
    # (_WHISPER_ARTIFACTS), and UTF-8/Latin-1 fallback decoding that the
    # standalone module does not provide.
    cmd = [
        WHISPER_CLI, "-m", WHISPER_MODEL, "-f", audio_path,
        "--no-timestamps", "-t", str(WHISPER_THREADS), "-l", lang,
        "--prompt", WHISPER_PROMPT,
    ]
    _log.info("Transcribe cmd: %s", " ".join(cmd))
    _log.info("Audio file size: %d bytes", os.path.getsize(audio_path) if os.path.exists(audio_path) else 0)
    try:
        env = dict(os.environ, LANG='en_US.UTF-8', LC_ALL='en_US.UTF-8')
        result = subprocess.run(cmd, capture_output=True, timeout=WHISPER_TIMEOUT, env=env)
    except FileNotFoundError:
        _log.error("whisper-cli not found at: %s", WHISPER_CLI)
        return ""
    except Exception as e:
        _log.error("Transcribe exception: %s", e)
        return ""
    _log.info("whisper-cli returncode=%d", result.returncode)
    # Decode output — try UTF-8 first, fall back to Latin-1
    try:
        stdout = result.stdout.decode('utf-8')
    except UnicodeDecodeError:
        stdout = result.stdout.decode('latin-1')
    try:
        stderr = result.stderr.decode('utf-8')
    except UnicodeDecodeError:
        stderr = result.stderr.decode('latin-1')

    if stdout:
        _log.info("stdout length: %d chars", len(stdout))
    if stderr:
        _log.info("stderr length: %d chars", len(stderr))

    output = stdout

    if result.returncode != 0:
        _log.warning("Non-zero returncode, returning empty")
        return ""
    lines = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        cleaned = _WHISPER_ARTIFACTS.sub("", line).strip()
        if cleaned:
            lines.append(cleaned)
    text = " ".join(lines)
    text = _add_punctuation(text)
    _log.info("Transcribed text: %d chars", len(text))
    return text


def wav_to_mp3(wav_path: str, mp3_path: str):
    try:
        result = subprocess.run(
            [FFMPEG_BIN, "-y", "-i", wav_path, "-codec:a", "libmp3lame",
             "-qscale:a", "4", "-ar", "16000", "-ac", "1", mp3_path],
            capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError("ffmpeg failed")
    except Exception:
        shutil.copy2(wav_path, mp3_path.replace(".mp3", ".wav"))


def paste_to_active_app(text: str):
    def _do_paste():
        pb = AppKit.NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text + " ", AppKit.NSPasteboardTypeString)
    _on_main(_do_paste)
    time.sleep(PASTE_DELAY)
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down'
    ], check=False)


# ── Audio Manager (ALWAYS-ON stream) ──────────────────────────────────

class AudioManager:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = None
        self._sample_size = self.pa.get_sample_size(FORMAT)
        self._ready = threading.Event()

    def start(self):
        try:
            self.stream = self.pa.open(
                format=FORMAT, channels=CHANNELS, rate=SAMPLE_RATE,
                input=True, frames_per_buffer=CHUNK,
            )
        except Exception as e:
            self._error = str(e)
            self._ready.set()
            return
        self._error = None
        self._ready.set()

    def wait_ready(self):
        self._ready.wait()

    def read_chunk(self):
        if not self.stream:
            return None
        try:
            if self.stream.get_read_available() >= CHUNK:
                return self.stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            pass
        return None

    def drain(self):
        if not self.stream:
            return
        try:
            while self.stream.get_read_available() >= CHUNK:
                self.stream.read(CHUNK, exception_on_overflow=False)
        except Exception:
            pass

    def get_sample_size(self):
        return self._sample_size

    def shutdown(self):
        if self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        self.pa.terminate()


# ── Recording Session ───────────────────────────────────────────────────

class RecordingSession:
    """Tracks a full recording session with pauses/resumes."""

    def __init__(self):
        self.start_time = datetime.datetime.now()
        self.end_time = None
        self.all_frames = []
        self.segments = []         # [(timestamp, text), ...]
        self.events = []           # [("start"|"pause"|"resume"|"stop", ts)]
        self.events.append(("start", self.start_time))

    def add_frames(self, frames):
        self.all_frames.extend(frames)

    def add_segment(self, text, timestamp=None):
        ts = timestamp or datetime.datetime.now()
        self.segments.append((ts, text))

    def pause(self):
        self.events.append(("pause", datetime.datetime.now()))

    def resume(self):
        self.events.append(("resume", datetime.datetime.now()))

    def stop(self):
        self.end_time = datetime.datetime.now()
        self.events.append(("stop", self.end_time))

    @property
    def full_text(self):
        return " ".join(text for _, text in self.segments if text)

    @property
    def duration_str(self):
        if not self.end_time:
            return "ongoing"
        d = round((self.end_time - self.start_time).total_seconds())
        m, s = divmod(d, 60)
        return f"{m}m {s}s" if m else f"{s}s"

    def format_transcript(self):
        lines = []
        lines.append(f"{APP_NAME} — Dictation Session")
        lines.append(f"Date: {self.start_time.strftime('%Y-%m-%d')}")
        lines.append(f"Started: {self.start_time.strftime('%I:%M:%S %p')}")
        if self.end_time:
            lines.append(f"Ended: {self.end_time.strftime('%I:%M:%S %p')}")
            lines.append(f"Duration: {self.duration_str}")
        lines.append("=" * 50)
        lines.append("")

        # Merge events and segments into one sorted timeline
        timeline = []
        for event_type, ts in self.events:
            if event_type == "start":
                continue
            timeline.append((ts, event_type, None))

        for seg_ts, seg_text in self.segments:
            if seg_text:
                timeline.append((seg_ts, "segment", seg_text))

        # Sort: by timestamp. For ties, segment BEFORE pause (so text appears first)
        order = {"segment": 0, "pause": 1, "resume": 2, "stop": 3}
        timeline.sort(key=lambda x: (x[0], order.get(x[1], 9)))

        for ts, kind, data in timeline:
            if kind == "segment":
                lines.append(f"[{ts.strftime('%I:%M:%S %p')}] {data}")
                lines.append("")
            elif kind == "pause":
                lines.append(f"--- Paused at {ts.strftime('%I:%M:%S %p')} ---")
                lines.append("")
            elif kind == "resume":
                lines.append(f"--- Resumed at {ts.strftime('%I:%M:%S %p')} ---")
                lines.append("")

        return "\n".join(lines)


# ── Hotkey Manager ──────────────────────────────────────────────────────

class HotkeyManager:
    def __init__(self, on_toggle, on_pause, on_hold_start, on_hold_stop, on_hold_msg, on_stop, on_cancel=None, on_quit=None, on_restart=None):
        self.on_toggle = on_toggle
        self.on_pause = on_pause
        self.on_hold_start = on_hold_start
        self.on_hold_stop = on_hold_stop
        self.on_hold_msg = on_hold_msg
        self.on_stop = on_stop
        self.on_cancel = on_cancel or on_stop  # fallback to stop if no cancel handler
        self.on_quit = on_quit
        self.on_restart = on_restart
        self._fn_down_time = None
        self._fn_hold_mode = False
        self._fn_hold_paused = False
        self._ctrl_down_time = None
        self._ctrl_had_keydown = False
        self.toggle_key = "fn"
        self.recording_active = False
        self._last_opt_up_time = 0  # for double-tap Option detection
        self._opt_was_down = False
        self._is_transcribing = lambda: False  # overridden by app

    def set_hold_paused(self, paused):
        self._fn_hold_paused = paused

    def start(self):
        self._tap_active = False
        self._create_tap()

    def _create_tap(self):
        """Create the CGEventTap. Returns True if successful."""
        event_mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) |
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            event_mask, self._handler, None,
        )
        if tap is None:
            _log.error("Event tap failed. Grant Accessibility to PX Dictate.")
            self._tap_active = False
            return False
        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(tap, True)
        self._tap_active = True
        _log.info("Event tap created successfully — hotkeys active")
        return True

    def retry(self):
        """Retry event tap creation (call after Accessibility is granted)."""
        if self._tap_active:
            _log.info("Event tap already active, skip retry")
            return True
        _log.info("Retrying event tap creation...")
        return self._create_tap()

    def _start_hold_timer(self):
        def _check():
            time.sleep(FN_LONG_HOLD)
            if self._fn_down_time is not None and not self._fn_hold_mode:
                self._fn_hold_mode = True
                self._fn_hold_paused = False
                self.on_hold_start()
                self.on_hold_msg()
        threading.Thread(target=_check, daemon=True).start()

    def _handler(self, proxy, event_type, event, refcon):
        flags = Quartz.CGEventGetFlags(event)

        if event_type == Quartz.kCGEventKeyDown:
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)

            if keycode == ESC_KEYCODE and (self.recording_active or self._is_transcribing()):
                self.on_cancel()
                return None

            # Cmd+R = restart, Cmd+Q = quit — only when OUR app is frontmost
            if bool(flags & CMD_FLAG) and keycode in (R_KEYCODE, Q_KEYCODE):
                front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
                if front and front.bundleIdentifier() == APP_BUNDLE_ID:
                    if keycode == R_KEYCODE and self.on_restart:
                        self.on_restart()
                        return None
                    if keycode == Q_KEYCODE and self.on_quit:
                        self.on_quit()
                        return None

            if self._ctrl_down_time is not None:
                self._ctrl_had_keydown = True

            if self.toggle_key == "ctrl_opt_v":
                ctrl = bool(flags & CTRL_FLAG)
                opt = bool(flags & OPT_FLAG)
                if ctrl and opt and keycode == V_KEYCODE:
                    self.on_toggle()
                    return None

            if self.toggle_key == "f5" and keycode == F5_KEYCODE:
                self.on_toggle()
                return None
            return event

        if event_type == Quartz.kCGEventFlagsChanged:
            fn_down = bool(flags & FN_FLAG)
            ctrl_down = bool(flags & CTRL_FLAG)

            if self.toggle_key == "fn":
                if fn_down and self._fn_down_time is None:
                    self._fn_down_time = time.time()
                    self._fn_hold_mode = False
                    self._start_hold_timer()

                elif not fn_down and self._fn_down_time is not None:
                    held = time.time() - self._fn_down_time
                    was_hold = self._fn_hold_mode
                    was_paused = self._fn_hold_paused
                    self._fn_down_time = None
                    self._fn_hold_mode = False

                    if was_hold and not was_paused:
                        self.on_hold_stop()
                    elif was_hold and was_paused:
                        pass
                    elif self.recording_active:
                        # Stop immediately — no threshold delay when stopping
                        self.on_toggle()
                    elif held >= FN_HOLD_THRESHOLD:
                        self.on_toggle()

            if ctrl_down and self._ctrl_down_time is None:
                self._ctrl_down_time = time.time()
                self._ctrl_had_keydown = False
            elif not ctrl_down and self._ctrl_down_time is not None:
                held = time.time() - self._ctrl_down_time
                solo = not self._ctrl_had_keydown
                self._ctrl_down_time = None
                self._ctrl_had_keydown = False
                if solo and held < CTRL_TAP_THRESHOLD:
                    self.on_pause()

            # Double-tap Option detection
            if self.toggle_key == "double_opt":
                opt_down = bool(flags & OPT_FLAG)
                if opt_down and not self._opt_was_down:
                    self._opt_was_down = True
                elif not opt_down and self._opt_was_down:
                    self._opt_was_down = False
                    now = time.time()
                    if now - self._last_opt_up_time < DOUBLE_TAP_THRESHOLD:
                        self._last_opt_up_time = 0
                        self.on_toggle()
                    else:
                        self._last_opt_up_time = now

        return event


# ── Helper ──────────────────────────────────────────────────────────────

def _on_main(block):
    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(block)


def _active_screen():
    mouse_loc = AppKit.NSEvent.mouseLocation()
    for screen in AppKit.NSScreen.screens():
        frame = screen.frame()
        if AppKit.NSMouseInRect(mouse_loc, frame, False):
            return screen
    return AppKit.NSScreen.mainScreen()


def _screen_top(screen=None):
    if screen is None:
        screen = _active_screen()
    frame = screen.frame()
    visible = screen.visibleFrame()
    menu_h = (frame.size.height - visible.size.height - (visible.origin.y - frame.origin.y))
    if menu_h < MIN_MENUBAR_H:
        menu_h = MENUBAR_H
    return frame.origin.y + frame.size.height - menu_h - WIDGET_OFFSET


# ── Floating Widget ─────────────────────────────────────────────────────

class FloatingWidget:
    def __init__(self, on_click_start, on_click_stop):
        self.on_click_start = on_click_start
        self.on_click_stop = on_click_stop
        self.window = None
        self.bar_view = None
        self.label = None
        self.label2 = None
        self.mini_label = None
        self.bar_bg = None
        self.vibrancy = None
        self.pause_btn = None
        self.stop_btn = None
        self.rec_btn = None
        self._rec_bg = None
        self._pause_callback = None
        self._stop_callback = None
        self._expanded = False
        self._hint_mode = False
        self._recording_mode = False
        self._msg_stop = True
        self._ready = threading.Event()
        self._bar_max_w = PILL_W - 24
        self._hovering = False
        self._current_hotkey = "fn"
        self._last_dark = None
        self._theme_name = "glass"
        _on_main(self._create_window)

    def set_recording_callbacks(self, on_pause, on_stop):
        self._pause_callback = on_pause
        self._stop_callback = on_stop

    def _dot_color(self, hover=False):
        """Return appropriate dot color for current system appearance."""
        dark = _is_dark_mode()
        t = self._get_theme()
        if hover:
            key = "dot_hover_dark" if dark else "dot_hover_light"
        else:
            key = "dot_dark" if dark else "dot_light"
        r, g, b = t[key]
        return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)

    def _get_theme(self):
        """Return current theme dict."""
        return THEMES.get(self._theme_name, THEMES["glass"])

    def _theme_color(self, key, alpha=1.0):
        """Return NSColor from theme key, with light mode variants."""
        t = self._get_theme()
        # Use light mode variant if available and in light mode
        if not _is_dark_mode() and key in ("text_color", "hint_text_color", "key_bg"):
            light_key = key + "_light"
            if light_key in t:
                vals = t[light_key]
                if len(vals) == 4:
                    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(vals[0], vals[1], vals[2], vals[3])
                return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(vals[0], vals[1], vals[2], alpha)
        vals = t.get(key, (0.5, 0.5, 0.5))
        if len(vals) == 4:
            return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(vals[0], vals[1], vals[2], vals[3])
        return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(vals[0], vals[1], vals[2], alpha)

    def _theme_material(self):
        """Return NSVisualEffectMaterial for current theme."""
        t = self._get_theme()
        material_name = t.get("material", "HUDWindow")
        materials = {
            "HUDWindow": AppKit.NSVisualEffectMaterialHUDWindow,
            "UnderWindowBackground": AppKit.NSVisualEffectMaterialUnderWindowBackground,
            "Popover": AppKit.NSVisualEffectMaterialPopover,
        }
        return materials.get(material_name, AppKit.NSVisualEffectMaterialHUDWindow)

    def set_theme(self, theme_name):
        """Apply a new theme to the widget."""
        if theme_name not in THEMES:
            return
        self._theme_name = theme_name
        t = self._get_theme()
        def _do():
            if not self.window:
                return
            # Update material
            self.vibrancy.setMaterial_(self._theme_material())
            # Update corner radius
            if self._expanded and self._recording_mode:
                self.vibrancy.layer().setCornerRadius_(t["corner_radius_panel"])
            else:
                self.vibrancy.layer().setCornerRadius_(t["corner_radius_pill"])
            # Update border
            bw = t.get("border_width", 0)
            self.vibrancy.layer().setBorderWidth_(bw)
            if bw > 0:
                bc = t.get("border_color", (0.5, 0.5, 0.5, 0.3))
                self.vibrancy.layer().setBorderColor_(
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(bc[0], bc[1], bc[2], bc[3]).CGColor()
                )
            else:
                self.vibrancy.layer().setBorderColor_(AppKit.NSColor.clearColor().CGColor())
            # Update shadow glow
            shadow_r = t.get("shadow_radius", 0)
            content = self.window.contentView()
            if shadow_r > 0:
                content.layer().setShadowOpacity_(0.5)
                content.layer().setShadowRadius_(shadow_r)
                content.layer().setShadowOffset_(Quartz.CGSizeMake(0, 0))
                content.layer().setShadowColor_(
                    Quartz.CGColorCreateGenericGray(0.0, 1.0)
                )
            else:
                content.layer().setShadowOpacity_(0)
            # Update opacity
            if self._expanded:
                self.window.setAlphaValue_(t["alpha_expanded"])
            elif self._hovering:
                self.window.setAlphaValue_(t["alpha_hover"])
            else:
                self.window.setAlphaValue_(t["alpha_mini"])
            # Update dot color
            if not self._expanded:
                self.mini_label.setTextColor_(self._dot_color(hover=self._hovering))
            # Update button backgrounds and corners
            btn_corner = t.get("button_corner", 4.0)
            if self.pause_btn:
                self.pause_btn.layer().setBackgroundColor_(self._theme_color("button_bg").CGColor())
                self.pause_btn.layer().setCornerRadius_(btn_corner)
                self.pause_btn.setTextColor_(self._theme_color("text_color"))
            if self.stop_btn:
                self.stop_btn.layer().setBackgroundColor_(self._theme_color("stop_bg").CGColor())
                self.stop_btn.layer().setCornerRadius_(btn_corner)
                self.stop_btn.setTextColor_(self._theme_color("text_color"))
            if self._rec_bg:
                self._rec_bg.layer().setBackgroundColor_(self._theme_color("rec_bg").CGColor())
                self._rec_bg.layer().setCornerRadius_(t.get("button_corner", CORNER_RADIUS_BUTTON))
            if self.rec_btn:
                self.rec_btn.setTextColor_(self._theme_color("text_color"))
            if self.bar_bg:
                self.bar_bg.layer().setBackgroundColor_(self._theme_color("bar_bg").CGColor())
            # Update label text colors if visible
            if self.label and not self.label.isHidden():
                self.label.setTextColor_(self._theme_color("text_color"))
            if self.label2 and not self.label2.isHidden():
                self.label2.setTextColor_(self._theme_color("hint_text_color"))
        _on_main(_do)

    def _make_attributed(self, parts, size=10.5, center=True):
        result = AppKit.NSMutableAttributedString.alloc().init()
        normal_font = AppKit.NSFont.systemFontOfSize_weight_(size, AppKit.NSFontWeightMedium)
        key_font = AppKit.NSFont.monospacedSystemFontOfSize_weight_(size, AppKit.NSFontWeightBold)
        text_clr = self._theme_color("text_color")
        key_bg = self._theme_color("key_bg")
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        if center:
            para.setAlignment_(AppKit.NSTextAlignmentCenter)

        for text, is_key in parts:
            attrs = {
                AppKit.NSFontAttributeName: key_font if is_key else normal_font,
                AppKit.NSForegroundColorAttributeName: text_clr,
                AppKit.NSParagraphStyleAttributeName: para,
            }
            if is_key:
                attrs[AppKit.NSBackgroundColorAttributeName] = key_bg
            part = AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            result.appendAttributedString_(part)
        return result

    def _create_window(self):
        screen = _active_screen()
        screen_w = screen.frame().size.width
        screen_x = screen.frame().origin.x
        top = _screen_top(screen)
        x = screen_x + (screen_w - MINI_W) / 2
        y = top - MINI_H

        self.window = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((x, y), (MINI_W, MINI_H)),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self.window.setFloatingPanel_(True)
        self.window.setWorksWhenModal_(True)
        self.window.setHidesOnDeactivate_(False)
        self.window.setLevel_(Quartz.kCGMainMenuWindowLevel + 2)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.window.setHasShadow_(True)
        self.window.setMovableByWindowBackground_(False)
        self.window.setCanHide_(False)
        self.window.setIgnoresMouseEvents_(False)
        self.window.setAcceptsMouseMovedEvents_(True)
        self.window.setAlphaValue_(self._get_theme()["alpha_mini"])

        self.window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary |
            AppKit.NSWindowCollectionBehaviorTransient
        )

        content = self.window.contentView()
        content.setWantsLayer_(True)

        self.vibrancy = AppKit.NSVisualEffectView.alloc().initWithFrame_(content.bounds())
        self.vibrancy.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )
        self.vibrancy.setMaterial_(self._theme_material())
        self.vibrancy.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        self.vibrancy.setState_(AppKit.NSVisualEffectStateActive)
        self.vibrancy.setWantsLayer_(True)
        self.vibrancy.layer().setCornerRadius_(self._get_theme()["corner_radius_pill"])
        self.vibrancy.layer().setMasksToBounds_(True)
        # Apply theme border
        _init_t = self._get_theme()
        _init_bw = _init_t.get("border_width", 0)
        if _init_bw > 0:
            self.vibrancy.layer().setBorderWidth_(_init_bw)
            _init_bc = _init_t.get("border_color", (0.5, 0.5, 0.5, 0.3))
            self.vibrancy.layer().setBorderColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    _init_bc[0], _init_bc[1], _init_bc[2], _init_bc[3]).CGColor()
            )
        # Apply theme shadow
        _init_sr = _init_t.get("shadow_radius", 0)
        if _init_sr > 0:
            content.layer().setShadowOpacity_(0.5)
            content.layer().setShadowRadius_(_init_sr)
            content.layer().setShadowOffset_(Quartz.CGSizeMake(0, 0))
            content.layer().setShadowColor_(
                Quartz.CGColorCreateGenericGray(0.0, 1.0)
            )
        content.addSubview_(self.vibrancy)

        # Mini dots — nudged down for visual centering
        self.mini_label = AppKit.NSTextField.alloc().initWithFrame_(((0, -2), (MINI_W, MINI_H)))
        self._setup_label(self.mini_label, "· · ·", 12, AppKit.NSFontWeightBold,
                          self._dot_color())
        content.addSubview_(self.mini_label)

        # Hint line 1 (hidden)
        self.label = AppKit.NSTextField.alloc().initWithFrame_(((8, 20), (HINT_W - 16, 16)))
        self.label.setBezeled_(False)
        self.label.setDrawsBackground_(False)
        self.label.setEditable_(False)
        self.label.setSelectable_(False)
        self.label.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.label.setHidden_(True)
        content.addSubview_(self.label)

        # Hint line 2 (hidden)
        self.label2 = AppKit.NSTextField.alloc().initWithFrame_(((8, 6), (HINT_W - 16, 14)))
        self._setup_label(self.label2, "", 9.5, AppKit.NSFontWeightMedium,
                          self._theme_color("hint_text_color"))
        self.label2.setHidden_(True)
        content.addSubview_(self.label2)

        # Bar bg (hidden)
        bar_w = PILL_W - BAR_INSET * 2
        self._bar_max_w = bar_w
        self.bar_bg = AppKit.NSView.alloc().initWithFrame_(((BAR_INSET, 5), (bar_w, 8)))
        self.bar_bg.setWantsLayer_(True)
        self.bar_bg.layer().setBackgroundColor_(self._theme_color("bar_bg").CGColor())
        self.bar_bg.layer().setCornerRadius_(CORNER_RADIUS_SMALL)
        self.bar_bg.setHidden_(True)
        content.addSubview_(self.bar_bg)

        # Bar fill (hidden)
        self.bar_view = AppKit.NSView.alloc().initWithFrame_(((BAR_INSET, 5), (1, 8)))
        self.bar_view.setWantsLayer_(True)
        self.bar_view.layer().setCornerRadius_(CORNER_RADIUS_SMALL)
        self.bar_view.setHidden_(True)
        content.addSubview_(self.bar_view)

        # Pause button (hidden, shown during recording)
        self.pause_btn = AppKit.NSTextField.alloc().initWithFrame_(((REC_PILL_W - 80, 24), (32, 16)))
        self._setup_label(self.pause_btn, "⏸", 11, AppKit.NSFontWeightBold,
                          self._theme_color("text_color"))
        self.pause_btn.setWantsLayer_(True)
        self.pause_btn.layer().setCornerRadius_(self._get_theme().get("button_corner", CORNER_RADIUS_SMALL))
        self.pause_btn.layer().setBackgroundColor_(self._theme_color("button_bg").CGColor())
        self.pause_btn.setHidden_(True)
        content.addSubview_(self.pause_btn)

        # Stop button (hidden, shown during recording)
        self.stop_btn = AppKit.NSTextField.alloc().initWithFrame_(((REC_PILL_W - 44, 24), (32, 16)))
        self._setup_label(self.stop_btn, "\u23f9", 11, AppKit.NSFontWeightBold,
                          self._theme_color("text_color"))
        self.stop_btn.setWantsLayer_(True)
        self.stop_btn.layer().setCornerRadius_(self._get_theme().get("button_corner", CORNER_RADIUS_SMALL))
        self.stop_btn.layer().setBackgroundColor_(self._theme_color("stop_bg").CGColor())
        self.stop_btn.setHidden_(True)
        content.addSubview_(self.stop_btn)

        # REC button (hidden, shown in hint mode as clickable target)
        # Use an NSView as the button background, with a text label inside
        self._rec_bg = AppKit.NSView.alloc().initWithFrame_(((HINT_W - 64, 10), (58, 26)))
        self._rec_bg.setWantsLayer_(True)
        self._rec_bg.layer().setCornerRadius_(self._get_theme().get("button_corner", CORNER_RADIUS_BUTTON))
        self._rec_bg.layer().setBackgroundColor_(self._theme_color("rec_bg").CGColor())
        self._rec_bg.setHidden_(True)
        content.addSubview_(self._rec_bg)
        # Text label centered inside the bg view — use tight height to avoid top-align gap
        self.rec_btn = AppKit.NSTextField.alloc().initWithFrame_(((0, 2), (56, 20)))
        self._setup_label(self.rec_btn, "\U0001f534 REC", 11, AppKit.NSFontWeightHeavy,
                          self._theme_color("text_color"))
        self._rec_bg.addSubview_(self.rec_btn)

        self.window.orderFrontRegardless()
        self._ready.set()

        AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskLeftMouseDown, self._local_click_handler
        )

    def set_hotkey_display(self, key):
        self._current_hotkey = key

    def _local_click_handler(self, event):
        if event.window() == self.window:
            loc = event.locationInWindow()
            self._handle_click(loc)
        return event

    def _setup_label(self, label, text, size, weight, color):
        label.setStringValue_(text)
        label.setTextColor_(color)
        label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(size, weight))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setAlignment_(AppKit.NSTextAlignmentCenter)

    def _handle_click(self, loc=None):
        if self._recording_mode:
            # Check if click is on pause or stop button regions
            if loc and self._recording_mode:
                x = loc.x
                pill_w = REC_PILL_W
                # Stop button region (right side)
                if x >= pill_w - 48 and x <= pill_w - 8:
                    if self._stop_callback:
                        self._stop_callback()
                    return
                # Pause button region
                if x >= pill_w - 88 and x < pill_w - 48:
                    if self._pause_callback:
                        self._pause_callback()
                    return
            # Click elsewhere on recording pill = stop
            if self.on_click_stop:
                self.on_click_stop()
        elif not self._expanded:
            self._show_hint()
        elif self._hint_mode:
            # Click on REC button region or anywhere in hint = start recording
            self._hint_mode = False
            def _hide_rec():
                if self.rec_btn:
                    self._rec_bg.setHidden_(True)
            _on_main(_hide_rec)
            if self.on_click_start:
                self.on_click_start()

    def check_hover(self):
        if self._expanded or not self.window or not self._ready.is_set():
            if self._hovering:
                self._hovering = False
            return

        mouse = AppKit.NSEvent.mouseLocation()
        frame = self.window.frame()
        hit = AppKit.NSMakeRect(frame.origin.x - 5, frame.origin.y - 5,
                                frame.size.width + 10, frame.size.height + 10)
        inside = AppKit.NSMouseInRect(mouse, hit, False)

        if inside and not self._hovering:
            self._hovering = True
            self._do_hover_enter()
        elif not inside and self._hovering:
            self._hovering = False
            self._do_hover_exit()

    def _do_hover_enter(self):
        def _do():
            if not self.window or self._expanded:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            x = screen_x + (screen_w - MINI_HOVER_W) / 2
            y = top - MINI_HOVER_H
            self.window.setFrame_display_(((x, y), (MINI_HOVER_W, MINI_HOVER_H)), True)
            self.mini_label.setFrame_(((0, -3), (MINI_HOVER_W, MINI_HOVER_H)))
            self.mini_label.setTextColor_(self._dot_color(hover=True))
            self.window.setAlphaValue_(self._get_theme()["alpha_hover"])
        _on_main(_do)

    def _do_hover_exit(self):
        def _do():
            if not self.window or self._expanded:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            x = screen_x + (screen_w - MINI_W) / 2
            y = top - MINI_H
            self.window.setFrame_display_(((x, y), (MINI_W, MINI_H)), True)
            self.mini_label.setFrame_(((0, -2), (MINI_W, MINI_H)))
            self.mini_label.setTextColor_(self._dot_color())
            self.window.setAlphaValue_(self._get_theme()["alpha_mini"])
        _on_main(_do)

    def move_to_active_screen(self):
        if not self._ready.is_set():
            return
        def _do():
            if not self.window:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            if self._expanded:
                if self._hint_mode:
                    w, h = HINT_W, HINT_H
                else:
                    w, h = REC_PILL_W, REC_PILL_H
            else:
                w, h = MINI_W, MINI_H
            x = screen_x + (screen_w - w) / 2
            y = top - h
            self.window.setFrame_display_(((x, y), (w, h)), True)
        _on_main(_do)

    def _show_hint(self):
        self._ready.wait()
        self._expanded = True
        self._hint_mode = True
        self._recording_mode = False

        def _do():
            if not self.window:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            x = screen_x + (screen_w - HINT_W) / 2
            y = top - HINT_H
            self.window.setFrame_display_(((x, y), (HINT_W, HINT_H)), True)
            self.vibrancy.layer().setCornerRadius_(self._get_theme()["corner_radius_pill"])
            self.mini_label.setHidden_(True)

            if self._current_hotkey == "fn":
                attr1 = self._make_attributed([
                    ("Tap ", False), (" fn ", True), (" or click \u2192", False),
                ], size=11, center=False)
            elif self._current_hotkey == "f5":
                attr1 = self._make_attributed([
                    ("Press ", False), (" F5 ", True), (" or click \u2192", False),
                ], size=11, center=False)
            elif self._current_hotkey == "double_opt":
                attr1 = self._make_attributed([
                    ("Double-tap ", False), (" \u2325 ", True), (" or click \u2192", False),
                ], size=10, center=False)
            else:
                attr1 = self._make_attributed([
                    ("Press ", False), (" Ctrl+Opt+V ", True), (" or \u2192", False),
                ], size=10, center=False)
            self.label.setFrame_(((10, 23), (HINT_W - 76, 16)))
            self.label.setAttributedStringValue_(attr1)
            self.label.setAlignment_(AppKit.NSTextAlignmentLeft)
            self.label.setHidden_(False)

            self.label2.setFrame_(((10, 8), (HINT_W - 76, 14)))
            self.label2.setStringValue_("to start dictating")
            self.label2.setTextColor_(self._theme_color("hint_text_color"))
            self.label2.setFont_(AppKit.NSFont.systemFontOfSize_weight_(10, AppKit.NSFontWeightMedium))
            self.label2.setAlignment_(AppKit.NSTextAlignmentLeft)
            self.label2.setHidden_(False)

            # Show REC button on the right side — styled as a button
            self._rec_bg.setFrame_(((HINT_W - 64, 10), (58, 26)))
            self._rec_bg.setHidden_(False)

            self.bar_bg.setHidden_(True)
            self.bar_view.setHidden_(True)
            self.pause_btn.setHidden_(True)
            self.stop_btn.setHidden_(True)
            self.window.setAlphaValue_(self._get_theme()["alpha_expanded"])
        _on_main(_do)

        def _auto_collapse():
            time.sleep(HINT_COLLAPSE_DELAY)
            if self._hint_mode:
                self._hint_mode = False
                self.collapse()
        threading.Thread(target=_auto_collapse, daemon=True).start()

    def expand(self):
        self._ready.wait()
        self._expanded = True
        self._hint_mode = False
        self._recording_mode = True
        self._msg_stop = False

        def _do():
            if not self.window:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            # Use wider pill for recording controls
            x = screen_x + (screen_w - REC_PILL_W) / 2
            y = top - REC_PILL_H
            self.window.setFrame_display_(((x, y), (REC_PILL_W, REC_PILL_H)), True)
            self.vibrancy.layer().setCornerRadius_(self._get_theme()["corner_radius_panel"])
            self.mini_label.setHidden_(True)

            self.label.setFrame_(((10, 26), (REC_PILL_W - 100, 14)))
            self.label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(9.5, AppKit.NSFontWeightMedium))
            self.label.setTextColor_(self._theme_color("text_color"))
            self.label.setStringValue_("🎙️ Recording...")
            self.label.setAlignment_(AppKit.NSTextAlignmentLeft)
            self.label.setHidden_(False)
            self.label2.setHidden_(True)
            self._rec_bg.setHidden_(True)

            # Show pause and stop buttons
            self.pause_btn.setFrame_(((REC_PILL_W - 80, 24), (32, 16)))
            self.pause_btn.setStringValue_("⏸")
            self.pause_btn.setHidden_(False)
            self.stop_btn.setFrame_(((REC_PILL_W - 44, 24), (32, 16)))
            self.stop_btn.setHidden_(False)

            # Level bar — adjusted for wider pill
            bar_w = REC_PILL_W - BAR_INSET * 2
            self._bar_max_w = bar_w
            self.bar_bg.setFrame_(((BAR_INSET, 5), (bar_w, 8)))
            self.bar_bg.setHidden_(False)
            self.bar_view.setFrame_(((BAR_INSET, 5), (1, 8)))
            self.bar_view.setHidden_(False)
            self.window.setAlphaValue_(self._get_theme()["alpha_expanded"])
        _on_main(_do)
        self._start_alternation()

    def collapse(self):
        self._expanded = False
        self._hint_mode = False
        self._recording_mode = False
        self._msg_stop = True

        def _do():
            if not self.window:
                return
            screen = _active_screen()
            screen_w = screen.frame().size.width
            screen_x = screen.frame().origin.x
            top = _screen_top(screen)
            x = screen_x + (screen_w - MINI_W) / 2
            y = top - MINI_H
            self.window.setFrame_display_(((x, y), (MINI_W, MINI_H)), True)
            self.vibrancy.layer().setCornerRadius_(self._get_theme()["corner_radius_pill"])
            self.mini_label.setFrame_(((0, -2), (MINI_W, MINI_H)))
            self.mini_label.setTextColor_(self._dot_color())
            self.mini_label.setHidden_(False)
            self.label.setAlignment_(AppKit.NSTextAlignmentCenter)
            self.label.setHidden_(True)
            self.label2.setHidden_(True)
            self.bar_bg.setHidden_(True)
            self.bar_view.setHidden_(True)
            self.pause_btn.setHidden_(True)
            self.stop_btn.setHidden_(True)
            self._rec_bg.setHidden_(True)
            self.window.setAlphaValue_(self._get_theme()["alpha_mini"])
        _on_main(_do)

    def _start_alternation(self):
        self._msg_stop = False
        def _run():
            toggle = True
            while not self._msg_stop:
                time.sleep(MSG_ALTERNATE_DELAY)
                if self._msg_stop:
                    break
                toggle = not toggle
                if toggle:
                    self._set_label("🎙️ Recording — esc to cancel")
                else:
                    self._set_label("🎙️ Ctrl to pause")
        threading.Thread(target=_run, daemon=True).start()

    def _set_label(self, text):
        def _do():
            if self.label:
                self.label.setFont_(AppKit.NSFont.systemFontOfSize_weight_(9.5, AppKit.NSFontWeightMedium))
                self.label.setTextColor_(self._theme_color("text_color"))
                self.label.setStringValue_(text)
        _on_main(_do)

    def update_level(self, level):
        if not self.bar_view or not self._expanded:
            return
        scaled = min(level * SENSITIVITY, 1.0)
        bar_width = max(1, int(scaled * self._bar_max_w))
        t = self._get_theme()

        if scaled < VU_THRESHOLD_LOW:
            r, g, b = t["vu_color_low"]
        elif scaled < VU_THRESHOLD_HIGH:
            r, g, b = t["vu_color_mid"]
        else:
            r, g, b = t["vu_color_high"]
        def _do():
            if self.bar_view:
                self.bar_view.setFrame_(((BAR_INSET, 5), (bar_width, 8)))
                self.bar_view.layer().setBackgroundColor_(
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).CGColor()
                )
        _on_main(_do)

    def set_status(self, text):
        self._msg_stop = True
        self._set_label(text)

    def set_paused_visual(self, paused):
        """Update pause button visual to show resume/pause state."""
        def _do():
            if self.pause_btn:
                if paused:
                    # Microphone + arrow = "resume dictating" (not confusing play icon)
                    self.pause_btn.setStringValue_("\U0001f399")
                    self.pause_btn.layer().setBackgroundColor_(self._theme_color("pause_resume_bg").CGColor())
                else:
                    self.pause_btn.setStringValue_("\u23f8")
                    self.pause_btn.layer().setBackgroundColor_(self._theme_color("button_bg").CGColor())
        _on_main(_do)


# ── History Entry ───────────────────────────────────────────────────────

class HistoryEntry:
    def __init__(self, text, session=None):
        self.text = text
        self.timestamp = datetime.datetime.now()
        self.session = session

    @property
    def time_str(self):
        return self.timestamp.strftime("%I:%M %p")

    @property
    def date_str(self):
        today = datetime.date.today()
        if self.timestamp.date() == today:
            return "Today"
        elif self.timestamp.date() == today - datetime.timedelta(days=1):
            return "Yesterday"
        return self.timestamp.strftime("%b %d")

    @property
    def preview(self):
        t = self.text[:40] + "..." if len(self.text) > 40 else self.text
        return f"{self.date_str} {self.time_str} — {t}"

    @property
    def full_lines(self):
        words = self.text.split()
        lines = []
        current = ""
        for w in words:
            if current and len(current) + len(w) + 1 > 55:
                lines.append(current)
                current = w
            else:
                current = f"{current} {w}" if current else w
        if current:
            lines.append(current)
        return lines if lines else [self.text]


# ── Save Manager ────────────────────────────────────────────────────────

class SaveManager:
    def __init__(self):
        self.save_audio = False
        self.save_transcripts = False
        self.save_dir = DEFAULT_SAVE_DIR

    def _ensure_dirs(self, session_time=None):
        """Create date-organized directories: Dictations/audio/YYYY/MM/DD/"""
        ts = session_time or datetime.datetime.now()
        date_subdir = os.path.join(str(ts.year), f"{ts.month:02d}", f"{ts.day:02d}")
        base = os.path.join(self.save_dir, DICTATIONS_FOLDER)
        audio_dir = os.path.join(base, "audio", date_subdir)
        text_dir = os.path.join(base, "transcripts", date_subdir)
        os.makedirs(audio_dir, exist_ok=True)
        os.makedirs(text_dir, exist_ok=True)
        return audio_dir, text_dir

    def save_session(self, session: RecordingSession, sample_size: int):
        if not self.save_audio and not self.save_transcripts:
            return
        if not session:
            return

        ts = session.start_time.strftime("%Y%m%d_%H%M%S")
        audio_dir, text_dir = self._ensure_dirs(session.start_time)

        if self.save_audio and session.all_frames:
            wav_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            with wave.open(wav_tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(sample_size)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b"".join(session.all_frames))
            mp3_path = os.path.join(audio_dir, f"session_{ts}.mp3")
            wav_to_mp3(wav_tmp.name, mp3_path)
            os.unlink(wav_tmp.name)

        if self.save_transcripts and session.segments:
            txt_path = os.path.join(text_dir, f"session_{ts}.txt")
            try:
                transcript = session.format_transcript()
                _log.info("Saving transcript (%d segments, %d chars) to %s",
                          len(session.segments), len(transcript), txt_path)
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(transcript + "\n")
            except Exception as e:
                _log.error("Failed to save transcript: %s", e)

    def choose_directory(self):
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setMessage_("Choose folder for Dictations")
        panel.setDirectoryURL_(AppKit.NSURL.fileURLWithPath_(self.save_dir))
        result = panel.runModal()
        if result == AppKit.NSModalResponseOK:
            url = panel.URLs()[0]
            self.save_dir = url.path()
            return self.save_dir
        return None


# ── Launch at Login ─────────────────────────────────────────────────────

def _is_launch_at_login():
    return os.path.exists(LAUNCH_AGENT_PLIST)


def _find_app_path():
    """Find the .app bundle path. Works whether running from bundle or source."""
    # Check if running inside a .app bundle
    exe = os.path.realpath(sys.executable)
    parts = exe.split(os.sep)
    for i, part in enumerate(parts):
        if part.endswith(".app"):
            return os.sep + os.path.join(*parts[:i + 1])
    # Check common install location
    app_path = "/Applications/PX Dictate.app"
    if os.path.exists(app_path):
        return app_path
    return None


def _set_launch_at_login(enabled: bool):
    if enabled:
        os.makedirs(LAUNCHAGENT_LOG_DIR, exist_ok=True)
        safe_log = xml_escape(LAUNCHAGENT_LOG_PATH)
        safe_bundle_id = xml_escape(APP_BUNDLE_ID)
        app_path = _find_app_path()
        if app_path:
            # Use 'open' to launch the .app bundle (proper macOS way)
            safe_app = xml_escape(app_path)
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{safe_bundle_id}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>{safe_app}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{safe_log}</string>
    <key>StandardErrorPath</key>
    <string>{safe_log}</string>
</dict>
</plist>"""
        else:
            # Fallback: run the script directly
            safe_script = xml_escape(os.path.abspath(__file__))
            safe_python = xml_escape(sys.executable)
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{safe_bundle_id}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{safe_python}</string>
        <string>{safe_script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{safe_log}</string>
    <key>StandardErrorPath</key>
    <string>{safe_log}</string>
</dict>
</plist>"""
        os.makedirs(os.path.dirname(LAUNCH_AGENT_PLIST), exist_ok=True)
        with open(LAUNCH_AGENT_PLIST, "w") as f:
            f.write(plist)
    else:
        if os.path.exists(LAUNCH_AGENT_PLIST):
            os.unlink(LAUNCH_AGENT_PLIST)


# ── Menu Bar App ────────────────────────────────────────────────────────

class PXDictateApp(rumps.App):
    def __init__(self):
        super().__init__("PX Dictate", title="🎙️", quit_button=None)
        self.recording = False
        self.paused = False
        self.frames = []
        self.session = None
        self.audio_mgr = AudioManager()
        self.widget = FloatingWidget(
            on_click_start=self._on_widget_click,
            on_click_stop=self._on_widget_stop,
        )
        self.widget.set_recording_callbacks(
            on_pause=self._on_pause,
            on_stop=self._on_widget_stop,
        )
        self.prefs = PrefsManager()
        saved_theme = self.prefs.get("theme")
        if saved_theme and saved_theme in THEMES:
            self.widget.set_theme(saved_theme)
        self.lang = self.prefs.get("lang")
        # Load model preference and update global
        self._current_model = self.prefs.get("model")
        self._apply_model(self._current_model)
        self.auto_paste = self.prefs.get("auto_paste")
        global _sounds_enabled
        _sounds_enabled = self.prefs.get("sounds_enabled")
        self._record_system_sounds = self.prefs.get("record_system_sounds")
        _record_sys_sounds_ref[0] = self._record_system_sounds
        self.save_mgr = SaveManager()
        self.save_mgr.save_audio = self.prefs.get("save_audio")
        self.save_mgr.save_transcripts = self.prefs.get("save_transcripts")
        self.save_mgr.save_dir = self.prefs.get("save_dir")
        self._toggle_triggered = False
        self._pause_triggered = False
        self._click_triggered = False
        self._stop_triggered = False
        self._quit_triggered = False
        self._restart_triggered = False
        self._hold_start_triggered = False
        self._hold_stop_triggered = False
        self._hold_msg_triggered = False
        self.history = collections.deque(maxlen=HISTORY_MAX)
        self._collecting = False
        self._hold_active = False
        self._pending_segments = 0       # count of in-flight _process_segment threads
        self._segments_lock = threading.Lock()
        self._guide_window = None
        self._guide_btn = None
        self._cmd_q_monitor = None
        self._transcribing = False
        self._speech_detected = False
        self._silence_monitor_active = False

        self.hotkey_mgr = HotkeyManager(
            on_toggle=self._on_toggle,
            on_pause=self._on_pause,
            on_hold_start=self._on_hold_start,
            on_hold_stop=self._on_hold_stop,
            on_hold_msg=self._on_hold_msg,
            on_stop=self._on_stop,
            on_cancel=self.cancel_recording,
            on_quit=self._on_quit,
            on_restart=self._on_restart,
        )
        self.hotkey_mgr.toggle_key = self.prefs.get("hotkey")
        self.hotkey_mgr._is_transcribing = lambda: self._transcribing

        # Build menu labels from saved prefs
        hotkey_display = {"fn": "Hold fn", "ctrl_opt_v": "Ctrl+Opt+V", "f5": "F5", "double_opt": "Double-tap ⌥"}.get(
            self.prefs.get("hotkey"), "Hold fn"
        )
        login_label = "  Launch at Login: ON" if _is_launch_at_login() else "  Launch at Login: OFF"
        save_dir_short = self.save_mgr.save_dir.replace(os.path.expanduser("~"), "~")

        # Build menu with Amphetamine-style checkmark toggles
        paste_item = rumps.MenuItem("Auto-paste", callback=self.toggle_paste)
        paste_item.state = self.auto_paste
        sounds_item = rumps.MenuItem("Sounds", callback=self.toggle_sounds)
        sounds_item.state = _sounds_enabled
        sys_sounds_item = rumps.MenuItem("Record System Sounds", callback=self.toggle_record_system_sounds)
        sys_sounds_item.state = self._record_system_sounds

        save_audio_item = rumps.MenuItem("  Save Audio", callback=self.toggle_save_audio)
        save_audio_item.state = self.save_mgr.save_audio
        save_transcripts_item = rumps.MenuItem("  Save Transcripts", callback=self.toggle_save_transcripts)
        save_transcripts_item.state = self.save_mgr.save_transcripts

        login_item = rumps.MenuItem("  Launch at Login", callback=self.toggle_launch_at_login)
        login_item.state = _is_launch_at_login()

        self._lang_menu = self._build_language_menu()
        self._model_menu = self._build_model_menu()

        fn_hotkey_item = rumps.MenuItem("  Hold fn (default)", callback=lambda s: self._set_hotkey("fn", s))
        fn_hotkey_item.state = (self.hotkey_mgr.toggle_key == "fn")
        ctrlv_hotkey_item = rumps.MenuItem("  Ctrl+Option+V", callback=lambda s: self._set_hotkey("ctrl_opt_v", s))
        ctrlv_hotkey_item.state = (self.hotkey_mgr.toggle_key == "ctrl_opt_v")
        f5_hotkey_item = rumps.MenuItem("  F5 (external keyboards)", callback=lambda s: self._set_hotkey("f5", s))
        f5_hotkey_item.state = (self.hotkey_mgr.toggle_key == "f5")
        double_opt_item = rumps.MenuItem("  Double-tap ⌥ Option", callback=lambda s: self._set_hotkey("double_opt", s))
        double_opt_item.state = (self.hotkey_mgr.toggle_key == "double_opt")

        # Theme submenu
        current_theme = self.prefs.get("theme")
        theme_menu = rumps.MenuItem(f"Theme: {THEMES[current_theme]['name']}")
        for theme_key, theme_data in THEMES.items():
            titem = rumps.MenuItem(f"  {theme_data['name']}", callback=lambda s, tk=theme_key: self._set_theme(tk, s))
            if theme_key == current_theme:
                titem.state = True
            theme_menu.add(titem)

        self.menu = [
            rumps.MenuItem(f"Start Recording ({hotkey_display} / esc to stop)", callback=self.toggle_recording),
            rumps.MenuItem("Pause & Process (tap Control)", callback=self.do_pause_process),
            None,
            self._lang_menu,
            self._model_menu,
            None,
            paste_item,
            sounds_item,
            sys_sounds_item,
            None,
            rumps.MenuItem("Save Options:", callback=None),
            save_audio_item,
            save_transcripts_item,
            rumps.MenuItem(f"  Save Location: {save_dir_short}", callback=self.choose_save_dir),
            None,
            rumps.MenuItem(f"Hotkey: {hotkey_display}", callback=None),
            fn_hotkey_item,
            ctrlv_hotkey_item,
            f5_hotkey_item,
            double_opt_item,
            None,
            theme_menu,
            None,
            login_item,
            None,
            rumps.MenuItem("History:", callback=None),
            rumps.MenuItem("  (none yet)", callback=None),
            None,
            self._build_help_menu(),
            self._build_feedback_menu(),
            rumps.MenuItem(f"About {APP_NAME}", callback=self.show_about),
            rumps.MenuItem("Restart (\u2318R)", callback=self.restart_app),
            rumps.MenuItem("Quit (\u2318Q)", callback=self.quit_app),
        ]

        # Restore history from disk
        self._restore_history()

        self.hotkey_mgr.start()
        _on_main(_init_sounds)
        threading.Thread(target=self._init_audio, daemon=True).start()

        # Local Cmd+Q monitor (backup — CGEventTap may not catch it)
        def _setup_cmd_q(app_self):
            def _handler(event):
                try:
                    mods = event.modifierFlags()
                    if (mods & CMD_FLAG) and event.keyCode() == Q_KEYCODE:
                        app_self._on_quit()
                        return None
                except Exception:
                    pass
                return event
            app_self._cmd_q_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                AppKit.NSKeyDownMask, _handler
            )
        _on_main(lambda: _setup_cmd_q(self))

        # Show setup guide on first run or if Accessibility is missing
        def _check_setup():
            time.sleep(1.5)  # let the app finish launching
            if _should_show_wizard():
                _show_wizard()
            # Retry event tap periodically (user may grant Accessibility at any time)
            for attempt in range(30):  # retry for up to 5 minutes
                time.sleep(10)
                if self.hotkey_mgr._tap_active:
                    _log.info("Event tap is now active!")
                    break
                _log.info("Retry event tap attempt %d...", attempt + 1)
                def _do_retry():
                    self.hotkey_mgr.retry()
                _on_main(_do_retry)
        threading.Thread(target=_check_setup, daemon=True).start()

    def _init_audio(self):
        # Check dependencies first
        ok, msg = _check_dependencies()
        if not ok:
            def _alert():
                rumps.notification(APP_NAME, "Setup Required", msg[:200], sound=True)
            _on_main(_alert)
            return

        self.audio_mgr.start()
        self.audio_mgr.wait_ready()
        if getattr(self.audio_mgr, '_error', None):
            def _mic_alert():
                rumps.notification(
                    APP_NAME, "Microphone Error",
                    "Grant Microphone access in System Settings → Privacy & Security → Microphone, then restart.",
                    sound=True,
                )
            _on_main(_mic_alert)
            return

        threading.Thread(target=self._audio_loop, daemon=True).start()

    def _audio_loop(self):
        while True:
            if self._collecting and not self.paused:
                data = self.audio_mgr.read_chunk()
                if data:
                    self.frames.append(data)
                    level = rms_level(data)
                    self.widget.update_level(level)
                    if level > SILENCE_THRESHOLD and not self._speech_detected:
                        self._speech_detected = True
                else:
                    time.sleep(0.01)
            else:
                self.audio_mgr.drain()
                time.sleep(0.03)

    def _on_toggle(self):
        self._toggle_triggered = True

    def _on_pause(self):
        self._pause_triggered = True

    def _on_stop(self):
        self._stop_triggered = True

    def _on_hold_start(self):
        self._hold_start_triggered = True

    def _on_hold_stop(self):
        self._hold_stop_triggered = True

    def _on_hold_msg(self):
        self._hold_msg_triggered = True

    def _on_widget_click(self):
        self._click_triggered = True

    def _on_widget_stop(self):
        self._stop_triggered = True

    def _on_quit(self):
        self._quit_triggered = True

    def _on_restart(self):
        self._restart_triggered = True

    @rumps.timer(0.1)
    def check_hotkeys(self, _):
        if self._toggle_triggered:
            self._toggle_triggered = False
            self.toggle_recording(None)
        if self._pause_triggered:
            self._pause_triggered = False
            self.do_pause_process(None)
        if self._stop_triggered:
            self._stop_triggered = False
            if self.recording:
                self._hold_active = False
                self.stop_recording()
        if self._click_triggered:
            self._click_triggered = False
            if not self.recording:
                self.start_recording()
        if self._hold_start_triggered:
            self._hold_start_triggered = False
            if not self.recording:
                self._hold_active = True
                self.start_recording()
        if self._hold_stop_triggered:
            self._hold_stop_triggered = False
            if self.recording and self._hold_active and not self.paused:
                self._hold_active = False
                self.stop_recording()
        if self._hold_msg_triggered:
            self._hold_msg_triggered = False
            if self.recording:
                self.widget.set_status("🎙️ Hold fn — esc to stop")
                def _resume():
                    time.sleep(5)
                    if self.recording and self._collecting and not self.paused:
                        self.widget._start_alternation()
                threading.Thread(target=_resume, daemon=True).start()
        if self._restart_triggered:
            self._restart_triggered = False
            self.restart_app(None)
        if self._quit_triggered:
            self._quit_triggered = False
            self.quit_app(None)

    @rumps.timer(0.15)
    def check_hover(self, _):
        self.widget.check_hover()

    @rumps.timer(2.0)
    def check_screen_and_theme(self, _):
        # Update dot colors when system theme changes
        dark = _is_dark_mode()
        if dark != self.widget._last_dark:
            self.widget._last_dark = dark
            if not self.widget._expanded:
                def _update():
                    if self.widget.mini_label and not self.widget._hovering:
                        self.widget.mini_label.setTextColor_(self.widget._dot_color())
                _on_main(_update)

        if self._collecting or self.recording:
            self.widget.move_to_active_screen()

    def _set_title(self, t):
        _on_main(lambda: setattr(self, 'title', t))

    def _build_language_menu(self):
        """Build Language sub-menu with Whisper-supported languages."""
        lang_menu = rumps.MenuItem(f"Language: {LANGUAGE_NAMES.get(self.lang, 'Auto-detect')}")
        for code, name in LANGUAGE_NAMES.items():
            item = rumps.MenuItem(f"  {name}", callback=lambda s, c=code: self.set_lang(c, s))
            item.state = (self.lang == code)
            lang_menu.add(item)
        lang_menu.add(None)
        other = rumps.MenuItem("  Other (use Auto-detect)", callback=None)
        other.set_callback(None)  # not selectable
        lang_menu.add(other)
        return lang_menu

    def _apply_model(self, model_name: str):
        """Set the active whisper model globally."""
        global WHISPER_MODEL
        path = _model_path_for(model_name)
        if os.path.exists(path):
            WHISPER_MODEL = path
            self._current_model = model_name
            _log.info("Model set to: %s (%s)", model_name, path)
        else:
            _log.warning("Model not found: %s — keeping current", path)

    def _build_model_menu(self):
        """Build Model sub-menu showing available models."""
        available = _available_models()
        label = _MODEL_LABELS.get(self._current_model, self._current_model)
        short_label = self._current_model.capitalize()
        model_menu = rumps.MenuItem(f"Model: {short_label}")
        for m in _MODEL_SIZES:
            is_available = m in available
            prefix = "  " if is_available else "  ⬇ "
            suffix = "" if is_available else " (not downloaded)"
            item = rumps.MenuItem(
                f"{prefix}{_MODEL_LABELS.get(m, m)}{suffix}",
                callback=lambda s, model=m: self._set_model(model, s),
            )
            if is_available:
                item.state = (m == self._current_model)
            model_menu.add(item)
        model_menu.add(None)
        dl_item = rumps.MenuItem("  Download models…", callback=self._show_download_help)
        model_menu.add(dl_item)
        return model_menu

    def _set_model(self, model_name, sender):
        """Switch to a different whisper model."""
        path = _model_path_for(model_name)
        if not os.path.exists(path):
            rumps.alert(
                title="Model Not Downloaded",
                message=(
                    f"The {model_name} model is not installed.\n\n"
                    f"To download it, run in Terminal:\n\n"
                    f"curl -L -o ~/.px-dictate/models/ggml-{model_name}.bin "
                    f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model_name}.bin"
                ),
            )
            return
        self._apply_model(model_name)
        self.prefs.set("model", model_name)
        # Update menu title and checkmarks
        short_label = model_name.capitalize()
        if model_name == "large-v3":
            short_label = "Large v3"
        self._model_menu.title = f"Model: {short_label}"
        available = _available_models()
        for item in self._model_menu.values():
            if hasattr(item, 'title'):
                for m in _MODEL_SIZES:
                    if _MODEL_LABELS.get(m, "") in item.title and m in available:
                        item.state = (m == model_name)

    def _show_download_help(self, sender):
        """Show help for downloading whisper models."""
        rumps.alert(
            title="Download Whisper Models",
            message=(
                "Run these commands in Terminal to download models:\n\n"
                "# Medium (1.5 GB — very good quality):\n"
                "curl -L -o ~/.px-dictate/models/ggml-medium.bin \\\n"
                "  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin\n\n"
                "# Large v3 (3.1 GB — best quality):\n"
                "curl -L -o ~/.px-dictate/models/ggml-large-v3.bin \\\n"
                "  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3.bin"
            ),
        )

    def _set_theme(self, theme_key, sender):
        """Change the floating pill theme."""
        self.prefs._prefs["theme"] = theme_key
        self.prefs.save()
        if self.widget:
            self.widget.set_theme(theme_key)
        # Update menu checkmarks
        theme_name = THEMES[theme_key]["name"]
        parent_key = None
        for key in self.menu:
            if key and key.startswith("Theme:"):
                parent_key = key
                break
        if parent_key:
            self.menu[parent_key].title = f"Theme: {theme_name}"
            for child_key in self.menu[parent_key]:
                self.menu[parent_key][child_key].state = (child_key.strip() == theme_name)

    def _set_hotkey(self, key, sender):
        self.hotkey_mgr.toggle_key = key
        self.widget.set_hotkey_display(key)
        self.prefs.set("hotkey", key)
        display = {"fn": "Hold fn", "ctrl_opt_v": "Ctrl+Opt+V", "f5": "F5", "double_opt": "Double-tap ⌥"}
        for item in self.menu.values():
            if hasattr(item, 'title') and item.title.startswith("Hotkey:"):
                item.title = f"Hotkey: {display.get(key, key)}"
                break
        for item in self.menu.values():
            if hasattr(item, 'title') and ('Recording' in item.title):
                hk = display.get(key, key)
                if self.recording:
                    item.title = f"Stop Recording ({hk} / esc)"
                else:
                    item.title = f"Start Recording ({hk} / esc to stop)"
                break
        # Update checkmarks
        hotkey_names = {"fn": "Hold fn (default)", "ctrl_opt_v": "Ctrl+Option+V", "f5": "F5 (external keyboards)", "double_opt": "Double-tap ⌥ Option"}
        for item in self.menu.values():
            if hasattr(item, 'title'):
                t = item.title.strip()
                for hk, hname in hotkey_names.items():
                    if t == hname:
                        item.state = (hk == key)

    def set_lang(self, lang, sender):
        self.lang = lang
        self.prefs.set("lang", lang)
        # Update submenu title
        if hasattr(self, '_lang_menu'):
            self._lang_menu.title = f"Language: {LANGUAGE_NAMES.get(lang, 'Auto-detect')}"
            # Update checkmarks in submenu
            for item in self._lang_menu.values():
                if hasattr(item, 'title'):
                    t = item.title.strip()
                    for lcode, lname in LANGUAGE_NAMES.items():
                        if t == lname:
                            item.state = (lcode == lang)

    def toggle_paste(self, sender):
        self.auto_paste = not self.auto_paste
        self.prefs.set("auto_paste", self.auto_paste)
        sender.state = self.auto_paste

    def toggle_sounds(self, sender):
        global _sounds_enabled
        _sounds_enabled = not _sounds_enabled
        self.prefs.set("sounds_enabled", _sounds_enabled)
        sender.state = _sounds_enabled

    def toggle_record_system_sounds(self, sender):
        self._record_system_sounds = not self._record_system_sounds
        self.prefs.set("record_system_sounds", self._record_system_sounds)
        _record_sys_sounds_ref[0] = self._record_system_sounds
        sender.state = self._record_system_sounds

    def toggle_save_audio(self, sender):
        self.save_mgr.save_audio = not self.save_mgr.save_audio
        self.prefs.set("save_audio", self.save_mgr.save_audio)
        sender.state = self.save_mgr.save_audio

    def toggle_save_transcripts(self, sender):
        self.save_mgr.save_transcripts = not self.save_mgr.save_transcripts
        self.prefs.set("save_transcripts", self.save_mgr.save_transcripts)
        sender.state = self.save_mgr.save_transcripts

    def choose_save_dir(self, sender):
        def _pick():
            new_dir = self.save_mgr.choose_directory()
            if new_dir:
                self.prefs.set("save_dir", new_dir)
                short = new_dir.replace(os.path.expanduser("~"), "~")
                sender.title = f"  Save Location: {short}"
        _on_main(_pick)

    def toggle_launch_at_login(self, sender):
        currently_on = _is_launch_at_login()
        _set_launch_at_login(not currently_on)
        sender.state = not currently_on

    def show_about(self, sender):
        def _do():
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"{APP_NAME} v{APP_VERSION}")
            alert.setInformativeText_(
                "Free & open-source voice-to-text for macOS.\n"
                "Powered by Whisper — 100% local, private, no cloud.\n\n"
                f"Created by {APP_AUTHOR}\n"
                f"{APP_COMPANY}\n\n"
                "Perfect for vibecoding, meetings, ideas & more.\n"
                "No subscription — forever free."
            )
            alert.addButtonWithTitle_("OK")
            alert.addButtonWithTitle_("\u2b50 GitHub")
            alert.addButtonWithTitle_("\u2615 Buy Me a Coffee")
            result = alert.runModal()
            if result == AppKit.NSAlertSecondButtonReturn:
                webbrowser.open(APP_GITHUB)
            elif result == AppKit.NSAlertThirdButtonReturn:
                webbrowser.open(APP_DONATE)
        _on_main(_do)

    def _build_help_menu(self):
        """Build Help sub-menu."""
        help_menu = rumps.MenuItem("Help")
        help_menu.add(rumps.MenuItem("Setup Guide...", callback=self.show_setup_guide))
        help_menu.add(rumps.MenuItem("User Guide...", callback=self.show_user_guide))
        help_menu.add(rumps.MenuItem("Improve Accuracy — Voice Isolation...", callback=self._show_voice_isolation_tip))
        help_menu.add(None)
        help_menu.add(rumps.MenuItem("Check for Updates...", callback=self._check_for_updates))
        help_menu.add(rumps.MenuItem("Uninstall...", callback=self.show_uninstall))
        return help_menu

    def _build_feedback_menu(self):
        """Build Feedback & Support sub-menu like Amphetamine."""
        fb_menu = rumps.MenuItem("Feedback & Support")
        fb_menu.add(rumps.MenuItem("Report an Issue...", callback=lambda s: webbrowser.open(APP_GITHUB + "/issues")))
        fb_menu.add(rumps.MenuItem("GitHub Repository...", callback=lambda s: webbrowser.open(APP_GITHUB)))
        fb_menu.add(None)
        fb_menu.add(rumps.MenuItem("Buy Us a Coffee...", callback=lambda s: webbrowser.open(APP_DONATE)))
        return fb_menu

    def show_setup_guide(self, sender):
        """Re-show the onboarding wizard."""
        _show_wizard()

    def _show_voice_isolation_tip(self, sender):
        """Show Voice Isolation tip in a simple alert."""
        def _do():
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Improve Accuracy — Voice Isolation")
            alert.setInformativeText_(
                "macOS Voice Isolation uses Apple's Neural Engine to filter "
                "background noise, dramatically improving transcription accuracy.\n\n"
                "How to enable:\n"
                "1. Start any recording (so the mic icon appears in menu bar)\n"
                "2. Click the mic icon in the menu bar\n"
                "3. Select 'Voice Isolation'\n\n"
                "Or: Control Center \u2192 Mic Mode \u2192 Voice Isolation\n\n"
                "Requirements: Apple Silicon (M1+), macOS 12 Monterey or later.\n"
                "Once enabled, it stays on for all apps."
            )
            alert.setAlertStyle_(AppKit.NSAlertStyleInformational)
            alert.addButtonWithTitle_("Got it")
            alert.runModal()
        _on_main(_do)

    def _check_for_updates(self, sender):
        """Check GitHub for newer release, compare with APP_VERSION."""
        def _do_check():
            try:
                url = f"https://api.github.com/repos/pxinnovative/px-dictate/releases/latest"
                req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                remote_tag = data.get("tag_name", "").lstrip("v")
                html_url = data.get("html_url", APP_GITHUB + "/releases")

                if not remote_tag:
                    self._show_update_alert("Could not determine latest version.", None)
                    return

                # Simple semantic version comparison
                local_parts = [int(x) for x in APP_VERSION.split(".")]
                remote_parts = [int(x) for x in remote_tag.split(".")]
                if remote_parts > local_parts:
                    self._show_update_alert(
                        f"New version available: v{remote_tag}\nYou have: v{APP_VERSION}",
                        html_url,
                    )
                else:
                    self._show_update_alert(
                        f"You're up to date! (v{APP_VERSION})", None
                    )
            except Exception as e:
                _log.warning("Update check failed: %s", e)
                self._show_update_alert(f"Could not check for updates.\n{e}", None)
        threading.Thread(target=_do_check, daemon=True).start()

    def _show_update_alert(self, message, download_url):
        """Show update check result as a native alert."""
        def _do():
            AppKit.NSApp.activateIgnoringOtherApps_(True)
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Check for Updates")
            alert.setInformativeText_(message)
            alert.setAlertStyle_(AppKit.NSAlertStyleInformational)
            if download_url:
                alert.addButtonWithTitle_("Download")
                alert.addButtonWithTitle_("Later")
                result = alert.runModal()
                if result == AppKit.NSAlertFirstButtonReturn:
                    webbrowser.open(download_url)
            else:
                alert.addButtonWithTitle_("OK")
                alert.runModal()
        _on_main(_do)

    def show_user_guide(self, sender):
        """Show user guide with bold headers, bullets, and formatted text."""
        def _do():
            AppKit.NSApp.activateIgnoringOtherApps_(True)

            guide_w, guide_h = 580, 680
            screen = AppKit.NSScreen.mainScreen()
            sx = screen.frame().origin.x + (screen.frame().size.width - guide_w) / 2
            sy = screen.frame().origin.y + (screen.frame().size.height - guide_h) / 2

            win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                ((sx, sy), (guide_w, guide_h)),
                AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable,
                AppKit.NSBackingStoreBuffered,
                False,
            )
            win.setTitle_(f"{APP_NAME} \u2014 User Guide")
            win.setLevel_(AppKit.NSFloatingWindowLevel)
            win.setReleasedWhenClosed_(False)
            content = win.contentView()

            scroll = AppKit.NSScrollView.alloc().initWithFrame_(((0, 0), (guide_w, guide_h)))
            scroll.setHasVerticalScroller_(True)
            scroll.setHasHorizontalScroller_(False)
            scroll.setBorderType_(AppKit.NSNoBorder)
            scroll.setDrawsBackground_(False)

            tv = AppKit.NSTextView.alloc().initWithFrame_(((0, 0), (guide_w - 20, guide_h)))
            tv.setEditable_(False)
            tv.setSelectable_(True)
            tv.setDrawsBackground_(False)
            tv.setTextContainerInset_(AppKit.NSMakeSize(20, 15))

            # Build attributed string with bold headers and bullet points
            attr = AppKit.NSMutableAttributedString.alloc().init()
            bold = AppKit.NSFont.systemFontOfSize_weight_(13, AppKit.NSFontWeightBold)
            normal = AppKit.NSFont.systemFontOfSize_(13)
            small_bold = AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightSemibold)
            gray = AppKit.NSColor.secondaryLabelColor()
            black = AppKit.NSColor.labelColor()

            # Intro paragraph
            intro_text = (
                f"{APP_NAME} is a voice-to-text tool that runs entirely on your Mac. "
                "Record, pause, and transcribe \u2014 all with a simple hotkey or click.\n\n"
            )
            intro_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                intro_text,
                {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: gray}
            )
            attr.appendAttributedString_(intro_str)

            sections = [
                ("Recording", [
                    ("Start", "Tap fn key (short press) or click the floating pill"),
                    ("Stop", "Tap fn again, press ESC, or click \u23f9 in the pill"),
                    ("Hold mode", "Hold fn for 1.5s+ \u2014 recording stops when you release"),
                ]),
                ("Pause & Segments", [
                    ("Pause", "Tap Control key \u2014 current audio is transcribed instantly"),
                    ("Resume", "Tap Control again \u2014 creates a new segment"),
                    ("Timestamps", "Each segment gets its own timestamp in the transcript"),
                ]),
                ("Floating Pill", [
                    ("Location", "The 3 dots (\u00b7 \u00b7 \u00b7) at the top of your screen"),
                    ("Expand", "Click to expand \u2192 click \u23fa REC to start recording"),
                    ("Controls", "During recording: click \u23f8 to pause, \u23f9 to stop"),
                    ("Level bar", "The color bar shows your microphone level in real-time"),
                ]),
                ("Menu Options", [
                    ("Language", "Choose auto-detect or a specific language"),
                    ("Auto-paste", "Automatically paste transcription into active app"),
                    ("Sounds", "Toggle recording start/stop beep sounds"),
                    ("Record System Sounds", "When OFF, beeps won't be captured in audio"),
                    ("Save Audio", "Save recordings as MP3 files"),
                    ("Save Transcripts", "Save session transcripts with timestamps"),
                    ("Save Location", "Choose where Dictations folder is created"),
                    ("Hotkey", "Switch between fn key and Ctrl+Opt+V"),
                    ("Launch at Login", "Auto-start PX Dictate when you log in"),
                    ("History", "View and copy your last 10 transcriptions"),
                ]),
                ("Keyboard Shortcuts", [
                    ("fn (tap)", "Toggle recording on/off"),
                    ("fn (hold 1.5s+)", "Hold-to-record mode"),
                    ("Control (tap)", "Pause & process segment"),
                    ("ESC", "Stop recording"),
                    ("\u2318Q", "Quit PX Dictate"),
                ]),
            ]

            for si, (section_title, items) in enumerate(sections):
                if si > 0:
                    # Separator
                    sep_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        "\n", {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(6)}
                    )
                    attr.appendAttributedString_(sep_str)
                # Section header
                header = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    section_title + "\n",
                    {AppKit.NSFontAttributeName: bold, AppKit.NSForegroundColorAttributeName: black}
                )
                attr.appendAttributedString_(header)
                # Items as bullets
                for label, desc in items:
                    bullet = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        f"  \u2022  {label}: ",
                        {AppKit.NSFontAttributeName: small_bold, AppKit.NSForegroundColorAttributeName: black}
                    )
                    attr.appendAttributedString_(bullet)
                    desc_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                        desc + "\n",
                        {AppKit.NSFontAttributeName: normal, AppKit.NSForegroundColorAttributeName: gray}
                    )
                    attr.appendAttributedString_(desc_str)

            tv.textStorage().setAttributedString_(attr)
            scroll.setDocumentView_(tv)
            content.addSubview_(scroll)

            # Store ref to keep window alive
            self._guide_window = win

            win.makeKeyAndOrderFront_(None)
        _on_main(_do)

    def show_uninstall(self, sender):
        """Show uninstall instructions and offer to uninstall."""
        def _do():
            msg = (
                "To completely remove PX Dictate:\n\n"
                "1. Quit PX Dictate (⌘Q or menu → Quit)\n"
                "2. Delete the app from /Applications/\n"
                "3. Remove preferences and data:\n"
                "   ~/Library/Application Support/PX Dictate/\n"
                "4. Remove Launch Agent (if Launch at Login was ON):\n"
                f"   ~/Library/LaunchAgents/{APP_BUNDLE_ID}.plist\n\n"
                "Optional — remove whisper model (~500MB):\n"
                "   rm -rf ~/.px-dictate/\n\n"
                "Optional — remove whisper-cli:\n"
                "   brew uninstall whisper-cpp\n\n"
                "Click 'Uninstall Now' to remove the app, preferences,\n"
                "and launch agent automatically. The whisper model and\n"
                "whisper-cli are shared tools and won't be removed."
            )

            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_(f"Uninstall {APP_NAME}")
            alert.setInformativeText_(msg)
            alert.addButtonWithTitle_("Cancel")
            alert.addButtonWithTitle_("Uninstall Now")
            alert.setAlertStyle_(AppKit.NSAlertStyleWarning)

            win = alert.window()
            win.setStyleMask_(win.styleMask() | AppKit.NSWindowStyleMaskClosable)
            AppKit.NSApp.activateIgnoringOtherApps_(True)

            result = alert.runModal()
            if result == AppKit.NSAlertSecondButtonReturn:
                self._do_uninstall()
        _on_main(_do)

    def _do_uninstall(self):
        """Remove app, preferences, and launch agent."""
        import shutil as _shutil
        # Remove launch agent
        if os.path.exists(LAUNCH_AGENT_PLIST):
            os.unlink(LAUNCH_AGENT_PLIST)
        # Remove preferences and data
        if os.path.exists(APP_SUPPORT_DIR):
            _shutil.rmtree(APP_SUPPORT_DIR, ignore_errors=True)
        # Remove NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
        defaults.removeObjectForKey_(SETUP_DONE_KEY)
        defaults.synchronize()
        # Schedule app deletion and quit — pure Python, no shell
        app_path = _find_app_path()
        if app_path and app_path.startswith("/Applications/"):
            subprocess.Popen(
                [sys.executable, "-c",
                 "import time, shutil, sys; time.sleep(2); shutil.rmtree(sys.argv[1], ignore_errors=True)",
                 app_path],
                start_new_session=True,
            )
        rumps.notification(
            APP_NAME, "Uninstalled",
            "PX Dictate has been removed. The app will now quit.",
            sound=False,
        )
        time.sleep(1)
        self.quit_app(None)

    def _restore_history(self):
        saved = PrefsManager.load_history()
        for item in saved:
            try:
                ts = datetime.datetime.fromisoformat(item["timestamp"])
                entry = HistoryEntry(item["text"])
                entry.timestamp = ts
                self.history.append(entry)
            except (KeyError, ValueError):
                continue
        if self.history:
            self._update_history_menu()

    def _persist_history(self):
        entries = [
            {"timestamp": e.timestamp.isoformat(), "text": e.text}
            for e in self.history
        ]
        PrefsManager.save_history(entries)

    def _add_to_history(self, text, session=None):
        self.history.append(HistoryEntry(text, session=session))
        self._update_history_menu()
        self._persist_history()

    def _update_history_menu(self):
        keys_to_remove = [k for k in self.menu.keys() if k.startswith("  📝") or k == "  (none yet)"]
        for k in keys_to_remove:
            try:
                del self.menu[k]
            except Exception:
                pass

        if not self.history:
            self.menu.insert_after("History:", rumps.MenuItem("  (none yet)"))
            return

        # Insert oldest first so newest ends up right after "History:"
        for entry in list(self.history):
            parent = rumps.MenuItem(f"  📝 {entry.preview}")
            for line in entry.full_lines:
                parent.add(rumps.MenuItem(f"  {line}"))
            parent.add(None)
            parent.add(rumps.MenuItem(
                "  📋 Copy full text",
                callback=lambda s, e=entry: self._copy_history(e),
            ))
            self.menu.insert_after("History:", parent)

    def _copy_history(self, entry):
        def _do_copy():
            pb = AppKit.NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(entry.text, AppKit.NSPasteboardTypeString)
        _on_main(_do_copy)
        play_sound("pasted")
        rumps.notification(
            "PX Dictate",
            f"{entry.date_str} {entry.time_str} — Copied!",
            "Text copied to clipboard",
            sound=False,
        )

    # ── Recording ───────────────────────────────────────────────────────

    def toggle_recording(self, sender):
        if self.recording:
            self._hold_active = False
            self.stop_recording()
        else:
            self.start_recording()

    def do_pause_process(self, sender):
        if not self.recording:
            return
        if self.paused:
            # Unpause
            self.paused = False
            self._collecting = True
            self._set_title("🔴")
            self.widget.set_paused_visual(False)
            play_sound("unpause")
            if self.session:
                self.session.resume()
            self.widget.set_status("🎙️ Recording — esc to stop")
            self.widget._start_alternation()
            if self._hold_active:
                self.hotkey_mgr.set_hold_paused(False)
        else:
            # Pause — capture timestamp BEFORE session.pause()
            self.paused = True
            self._collecting = False
            current_frames = list(self.frames)
            self.frames = []
            seg_time = datetime.datetime.now()  # timestamp for the segment
            self._set_title("⏸️")
            self.widget.set_paused_visual(True)
            play_sound("pause")
            self.widget.update_level(0)
            self.widget.set_status("⏸️ Paused — Ctrl resume")
            if self.session:
                self.session.add_frames(current_frames)
                self.session.pause()  # pause event gets a slightly later timestamp
            if self._hold_active:
                self.hotkey_mgr.set_hold_paused(True)
            if current_frames:
                threading.Thread(
                    target=self._process_segment,
                    args=(current_frames, seg_time),
                    daemon=True,
                ).start()

    def _process_segment(self, frames, seg_time=None):
        with self._segments_lock:
            self._pending_segments += 1
        try:
            self._process_segment_inner(frames, seg_time)
        finally:
            with self._segments_lock:
                self._pending_segments -= 1

    def _process_segment_inner(self, frames, seg_time=None):
        min_frames = int(SAMPLE_RATE / CHUNK * 0.5)
        if len(frames) < min_frames:
            if self.paused:
                self.widget.set_status("⏸️ Paused (too short) — Ctrl resume")
            return

        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.audio_mgr.get_sample_size())
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b"".join(frames))

            text = transcribe(tmp.name, lang=self.lang)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if text:
            if self.session:
                self.session.add_segment(text, timestamp=seg_time)
            if self.auto_paste:
                paste_to_active_app(text)
                play_sound("pasted")
            if self.paused:
                preview = text[:20] + "..." if len(text) > 20 else text
                self.widget.set_status("⏸️ ✅ Segment saved — Ctrl resume")
        elif self.paused:
            self.widget.set_status("⏸️ Paused — Ctrl resume")

    def start_recording(self):
        if self.recording:
            return
        self.recording = True
        self.paused = False
        self.frames = []
        self.session = RecordingSession()
        self._set_title("🔴")
        self.hotkey_mgr.recording_active = True
        _recording_active_ref[0] = True
        play_sound("start")

        for item in self.menu.values():
            if hasattr(item, 'title') and 'Recording' in item.title:
                item.title = "Stop Recording (esc)"
                break

        self.widget.move_to_active_screen()
        self.widget.expand()
        self._collecting = True
        self._speech_detected = False
        self._silence_monitor_active = True
        threading.Thread(target=self._silence_monitor, daemon=True).start()

    def cancel_recording(self):
        """Cancel recording — discard everything, don't transcribe."""
        self._transcribing = False
        self._silence_monitor_active = False
        if not self.recording:
            return
        self.recording = False
        self.paused = False
        self._collecting = False
        self._hold_active = False
        self.hotkey_mgr.recording_active = False
        _recording_active_ref[0] = False

        for item in self.menu.values():
            if hasattr(item, 'title') and 'Recording' in item.title:
                item.title = "Start Recording (fn / esc to stop)"
                break

        self.frames = []
        if self.session:
            self.session = None

        _log.info("Recording cancelled by user (ESC)")
        play_sound("stop")
        self.widget.set_status("\U0001f6ab Cancelled")
        self.widget.update_level(0)
        self._set_title("🎙️")
        threading.Timer(1.5, self.widget.collapse).start()

    def stop_recording(self):
        if not self.recording:
            return
        self._silence_monitor_active = False
        self.recording = False
        self.paused = False
        self._collecting = False
        self._hold_active = False
        self.hotkey_mgr.recording_active = False
        _recording_active_ref[0] = False

        for item in self.menu.values():
            if hasattr(item, 'title') and 'Recording' in item.title:
                item.title = "Start Recording (fn / esc to stop)"
                break

        remaining = list(self.frames)
        self.frames = []

        # Check minimum duration — discard too-short recordings to avoid Whisper hallucinations
        total_frames = len(remaining)
        if self.session:
            total_frames += len(self.session.all_frames)
        duration_secs = total_frames * CHUNK / SAMPLE_RATE
        if duration_secs < MIN_RECORDING_SECS:
            _log.info("Recording too short (%.1fs < %.1fs) — discarded", duration_secs, MIN_RECORDING_SECS)
            play_sound("stop")
            self.widget.set_status("Too short — try again")
            self.widget.update_level(0)
            self._set_title("🎙️")
            if self.session:
                self.session = None
            threading.Timer(1.5, self.widget.collapse).start()
            return

        self._set_title("⏳")
        play_sound("stop")
        self.widget.set_status("🔄 Transcribing...")
        self._transcribing = True
        self.widget.update_level(0)
        seg_time = datetime.datetime.now()  # capture recording-end time before whisper

        if self.session:
            self.session.add_frames(remaining)
            self.session.stop()  # end_time = when user stopped recording

        if remaining:
            threading.Thread(target=self._transcribe_final, args=(remaining, seg_time), daemon=True).start()
        else:
            # No new frames — but wait for any in-flight pause segments to finish
            threading.Thread(target=self._wait_and_finalize, daemon=True).start()

    def _silence_monitor(self):
        """Monitor for silence at recording start. Auto-cancel if no speech for SILENCE_TIMEOUT seconds."""
        start = time.time()
        while self._silence_monitor_active and self.recording:
            time.sleep(0.5)
            elapsed = time.time() - start
            if self._speech_detected:
                self._silence_monitor_active = False
                return
            if elapsed >= SILENCE_TIMEOUT:
                # Start countdown
                for i in range(SILENCE_COUNTDOWN, 0, -1):
                    if not self._silence_monitor_active or self._speech_detected:
                        return
                    self.widget.set_status(f"\U0001f507 No speech detected \u2014 cancelling in {i}...")
                    time.sleep(1)
                if self._silence_monitor_active and not self._speech_detected:
                    _log.info("Auto-cancel: no speech detected for %ds", SILENCE_TIMEOUT)
                    self.cancel_recording()
                return

    def _wait_and_finalize(self):
        """Wait for in-flight _process_segment threads, then finalize."""
        for _ in range(100):  # max ~10 seconds
            with self._segments_lock:
                if self._pending_segments == 0:
                    break
            time.sleep(0.1)
        if self.session and self.session.full_text:
            self._finalize_session()
        else:
            self.widget.collapse()
            self._set_title("🎙️")

    def _transcribe_final(self, frames, seg_time=None):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with wave.open(tmp.name, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self.audio_mgr.get_sample_size())
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b"".join(frames))

            text = transcribe(tmp.name, lang=self.lang)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

        if text:
            if self.session:
                self.session.add_segment(text, timestamp=seg_time)
            if self.auto_paste:
                paste_to_active_app(text)
                play_sound("pasted")

        # Wait for any in-flight pause segments before finalizing
        for _ in range(100):  # max ~10 seconds
            with self._segments_lock:
                if self._pending_segments == 0:
                    break
            time.sleep(0.1)

        self._finalize_session()

    def _finalize_session(self):
        self._transcribing = False
        session = self.session
        self.session = None

        if session:
            if not session.end_time:
                session.stop()  # fallback for pause→stop (no remaining frames)
            _log.info("Finalizing session: %d segments, full_text=%d chars",
                      len(session.segments), len(session.full_text))

        # Always save audio if enabled, even if transcription failed
        if session and (self.save_mgr.save_audio or self.save_mgr.save_transcripts):
            threading.Thread(
                target=self.save_mgr.save_session,
                args=(session, self.audio_mgr.get_sample_size()),
                daemon=True,
            ).start()

        if session and session.full_text:
            full = session.full_text
            self.widget.set_status("✅ Transcript copied!")
            self._add_to_history(full, session=session)
        else:
            play_sound("error")
            self.widget.set_status("❌ Empty")

        time.sleep(FINALIZE_DELAY)
        self.widget.collapse()
        self._set_title("🎙️")

    def restart_app(self, sender):
        """Quit and relaunch the app."""
        self.recording = False
        self._collecting = False
        self.hotkey_mgr.recording_active = False
        self.audio_mgr.shutdown()
        # Relaunch: use the bundle path if running as .app, otherwise the script
        bundle = AppKit.NSBundle.mainBundle().bundlePath()
        if bundle.endswith(".app"):
            subprocess.Popen(["open", "-n", bundle])
        else:
            subprocess.Popen([sys.executable, __file__])
        rumps.quit_application()

    def quit_app(self, sender):
        self.recording = False
        self._collecting = False
        self.hotkey_mgr.recording_active = False
        self.audio_mgr.shutdown()
        rumps.quit_application()


if __name__ == "__main__":
    PXDictateApp().run()
