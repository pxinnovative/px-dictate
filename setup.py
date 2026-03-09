"""
py2app build script for PX Dictate.

Usage:
    python3 setup.py py2app

This creates PX Dictate.app in the dist/ folder.
"""
from setuptools import setup

APP = ["px_dictate_app.py"]
APP_NAME = "PX Dictate"

DATA_FILES = []

OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        # Keep in sync with APP_VERSION and APP_BUNDLE_ID in px_dictate_app.py
        "CFBundleIdentifier": "com.pxinnovative.pxdictate",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSMinimumSystemVersion": "10.15",
        "LSUIElement": False,  # Show in dock for visibility
        "NSSupportsAutomaticTermination": True,
        "NSSupportsSuddenTermination": True,
        "NSMicrophoneUsageDescription": "PX Dictate needs microphone access to record and transcribe your voice.",
        "NSAppleEventsUsageDescription": "PX Dictate uses accessibility to paste transcribed text into the active application.",
    },
    "packages": ["rumps", "pyaudio", "objc"],
    "includes": [
        "AppKit",
        "Quartz",
        "Foundation",
        "collections",
        "wave",
        "struct",
        "math",
        "json",
        "webbrowser",
    ],
    "excludes": ["tkinter", "matplotlib", "numpy", "scipy", "PIL"],
    "iconfile": "PXDictate.icns",
}

setup(
    app=APP,
    name=APP_NAME,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
