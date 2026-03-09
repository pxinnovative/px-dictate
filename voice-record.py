#!/usr/bin/env python3
"""
PX Dictate Recorder — Record with live audio visualization + Whisper transcription.

Usage:
    python3 voice-record.py [--lang es|en|auto] [--duration N]
    Press Ctrl+C to stop recording.
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import struct
import subprocess
import sys
import tempfile
import wave

import pyaudio

# ── Config ──────────────────────────────────────────────────────────────
WHISPER_MODEL = os.environ.get(
    "WHISPER_MODEL",
    os.path.expanduser("~/.px-dictate/models/ggml-small.bin"),
)
WHISPER_CLI = os.environ.get("WHISPER_CLI", "whisper-cli")
SAMPLE_RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK = 1024  # ~64ms at 16kHz
BAR_WIDTH = 30
SILENCE_THRESHOLD = 0.001  # RMS below this = "silent"

# ── Visual bars ─────────────────────────────────────────────────────────
BLOCKS = " ░▒▓█"


def rms_level(data: bytes) -> float:
    """Calculate RMS amplitude from raw PCM bytes (0.0 to 1.0)."""
    count = len(data) // 2
    if count == 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", data)
    sum_sq = sum(s * s for s in samples)
    rms = math.sqrt(sum_sq / count) / 32768.0
    return min(rms, 1.0)


def render_bar(level: float, width: int = BAR_WIDTH) -> str:
    """Render a visual bar based on audio level."""
    if level < SILENCE_THRESHOLD:
        return f"\r  🎙️  Recording...  {'·' * width}  "

    # Scale level (amplify for normal speaking voice)
    gain = globals().get("_SENSITIVITY", 20.0)
    scaled = min(level * gain, 1.0)
    filled = int(scaled * width)

    bar = ""
    for i in range(width):
        if i < filled:
            # Gradient: green → yellow → red
            intensity = i / width
            if intensity < 0.5:
                bar += "█"
            elif intensity < 0.8:
                bar += "▓"
            else:
                bar += "▒"
        else:
            bar += "·"

    return f"\r  🎙️  Recording...  {bar}  "


def record(duration: float | None = None) -> str:
    """Record audio from mic with live visualization. Returns path to WAV file."""
    pa = pyaudio.PyAudio()
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_path = tmp.name
    tmp.close()

    stream = pa.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK,
    )

    frames = []
    recording = True
    total_chunks = int(SAMPLE_RATE / CHUNK * duration) if duration else None

    def stop_handler(sig, frame):
        nonlocal recording
        recording = False

    signal.signal(signal.SIGINT, stop_handler)

    print("", file=sys.stderr)
    if duration:
        print(f"  🎙️  Recording {duration}s... Press Ctrl+C to stop early", file=sys.stderr)
    else:
        print("  🎙️  Recording... Press Ctrl+C to stop", file=sys.stderr)
    print("", file=sys.stderr)

    chunk_count = 0
    try:
        while recording:
            if total_chunks and chunk_count >= total_chunks:
                break

            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)
            chunk_count += 1

            level = rms_level(data)
            sys.stderr.write(render_bar(level))
            sys.stderr.flush()

    except Exception:
        pass

    # Clear the bar line
    sys.stderr.write("\r" + " " * 60 + "\r")
    sys.stderr.flush()
    print("  ⏹️  Recording stopped.", file=sys.stderr)

    stream.stop_stream()
    stream.close()
    pa.terminate()

    # Save WAV
    with wave.open(tmp_path, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(pa.get_sample_size(FORMAT))
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(frames))

    return tmp_path


def transcribe(audio_path: str, lang: str = "auto") -> str:
    """Transcribe audio file using whisper-cpp."""
    cmd = [
        WHISPER_CLI,
        "-m", WHISPER_MODEL,
        "-f", audio_path,
        "--no-timestamps",
        "-t", "4",
    ]
    if lang != "auto":
        cmd.extend(["-l", lang])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        print(f"  ❌  Whisper error: {result.stderr}", file=sys.stderr)
        return ""

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return " ".join(lines)


def main():
    parser = argparse.ArgumentParser(description="PX Dictate — Record & Transcribe")
    parser.add_argument("--lang", default="auto", help="Language (en, es, auto)")
    parser.add_argument("--duration", type=float, default=None, help="Duration in seconds")
    parser.add_argument("--model", default=None, help="Path to Whisper model")
    parser.add_argument("--sensitivity", type=float, default=8.0, help="Mic sensitivity multiplier (default: 8, higher=more responsive)")
    args = parser.parse_args()

    if args.model:
        global WHISPER_MODEL
        WHISPER_MODEL = args.model

    # Patch sensitivity into render_bar
    global _SENSITIVITY
    _SENSITIVITY = args.sensitivity

    if not os.path.exists(WHISPER_MODEL):
        print(f"  ❌  Model not found: {WHISPER_MODEL}", file=sys.stderr)
        sys.exit(1)

    # Record
    audio_path = record(duration=args.duration)

    # Check file size
    if os.path.getsize(audio_path) < 1000:
        print("  ❌  Recording too short.", file=sys.stderr)
        os.unlink(audio_path)
        sys.exit(1)

    # Transcribe
    print("  🔄  Transcribing...", file=sys.stderr)
    text = transcribe(audio_path, lang=args.lang)

    # Always delete audio file (privacy by design)
    os.unlink(audio_path)

    if not text:
        print("  ❌  Empty transcription.", file=sys.stderr)
        sys.exit(1)

    # Output
    print("  ✅  Transcription:", file=sys.stderr)
    print("", file=sys.stderr)
    print(text)

    # Copy to clipboard
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True)
        print("", file=sys.stderr)
        print("  📋  Copied to clipboard.", file=sys.stderr)
    except Exception:
        pass


if __name__ == "__main__":
    main()
