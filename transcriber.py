"""
PX Dictate Transcriber — Local Whisper transcription module.

Uses whisper-cpp via subprocess for 100% local speech-to-text.

Usage:
    from transcriber import Transcriber

    t = Transcriber()
    text = t.transcribe("/path/to/audio.wav")
    text = t.transcribe("/path/to/audio.wav", lang="es")
"""

import subprocess
import tempfile
import os
from pathlib import Path


class Transcriber:
    """Local Whisper transcription using whisper-cpp."""

    DEFAULT_MODEL = Path.home() / ".px-dictate/models/ggml-small.bin"
    DEFAULT_CLI = "whisper-cli"

    def __init__(self, model_path: str | None = None, cli_path: str | None = None):
        self.model_path = Path(model_path or os.environ.get("WHISPER_MODEL", self.DEFAULT_MODEL))
        self.cli_path = cli_path or os.environ.get("WHISPER_CLI", self.DEFAULT_CLI)

        if not self.model_path.exists():
            raise FileNotFoundError(f"Whisper model not found: {self.model_path}")

    def transcribe(
        self,
        audio_path: str,
        lang: str = "auto",
        threads: int = 4,
    ) -> str:
        """Transcribe an audio file to text.

        Args:
            audio_path: Path to audio file (WAV 16kHz mono recommended)
            lang: Language code ('en', 'es', 'auto')
            threads: Number of CPU threads

        Returns:
            Transcribed text string
        """
        audio_path = Path(audio_path)
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        cmd = [
            self.cli_path,
            "-m", str(self.model_path),
            "-f", str(audio_path),
            "--no-timestamps",
            "-t", str(threads),
        ]

        if lang != "auto":
            cmd.extend(["-l", lang])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Whisper failed: {result.stderr}")

        text = result.stdout.strip()
        # Clean up whisper output (remove empty lines, leading spaces)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return " ".join(lines)

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        lang: str = "auto",
        sample_rate: int = 16000,
    ) -> str:
        """Transcribe raw audio bytes (WAV format).

        Useful for API endpoints that receive audio as bytes.
        """
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            return self.transcribe(tmp.name, lang=lang)
