#!/bin/bash
# PX Dictate Input — Record & Transcribe via Whisper (local)
# Usage: voice-record.sh [--lang es|en|auto] [--duration N]
# Press Ctrl+C to stop recording (or use --duration for fixed length)

set -euo pipefail

# Config
WHISPER_MODEL="${WHISPER_MODEL:-$HOME/.px-dictate/models/ggml-small.bin}"
WHISPER_CLI="${WHISPER_CLI:-whisper-cli}"
TEMP_DIR="${TMPDIR:-/tmp}"
AUDIO_FILE="${TEMP_DIR}/px-dictate-input.wav"
OUTPUT_FILE="${TEMP_DIR}/px-dictate-output.txt"
SAMPLE_RATE=16000
CHANNELS=1
LANG="auto"
DURATION=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --lang) LANG="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --model) WHISPER_MODEL="$2"; shift 2 ;;
    --help)
      echo "Usage: voice-record.sh [--lang es|en|auto] [--duration N] [--model PATH]"
      echo "  --lang     Language (default: auto-detect)"
      echo "  --duration Recording duration in seconds (default: manual stop with Ctrl+C)"
      echo "  --model    Path to whisper GGML model"
      exit 0
      ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Verify dependencies
if ! command -v sox &>/dev/null; then
  echo "ERROR: sox not found. Install with: brew install sox" >&2
  exit 1
fi

if ! command -v "$WHISPER_CLI" &>/dev/null; then
  echo "ERROR: whisper-cli not found. Install with: brew install whisper-cpp" >&2
  exit 1
fi

if [[ ! -f "$WHISPER_MODEL" ]]; then
  echo "ERROR: Whisper model not found at $WHISPER_MODEL" >&2
  exit 1
fi

# Record
echo "🎙️  Recording... (Press Ctrl+C to stop)" >&2

DURATION_ARGS=""
if [[ -n "$DURATION" ]]; then
  DURATION_ARGS="trim 0 $DURATION"
  echo "   Duration: ${DURATION}s" >&2
fi

# Trap Ctrl+C to stop recording gracefully
trap 'echo "" >&2; echo "⏹️  Recording stopped." >&2' INT

rec -q -r "$SAMPLE_RATE" -c "$CHANNELS" -b 16 "$AUDIO_FILE" $DURATION_ARGS 2>/dev/null || true

trap - INT

# Check if audio file was created and has content
if [[ ! -f "$AUDIO_FILE" ]] || [[ $(stat -f%z "$AUDIO_FILE" 2>/dev/null || stat -c%s "$AUDIO_FILE" 2>/dev/null) -lt 1000 ]]; then
  echo "ERROR: No audio recorded or recording too short." >&2
  exit 1
fi

# Transcribe
echo "🔄  Transcribing..." >&2

LANG_ARGS=""
if [[ "$LANG" != "auto" ]]; then
  LANG_ARGS="-l $LANG"
fi

"$WHISPER_CLI" \
  -m "$WHISPER_MODEL" \
  -f "$AUDIO_FILE" \
  $LANG_ARGS \
  --no-timestamps \
  -t 4 \
  2>/dev/null | sed '/^$/d' | sed 's/^ *//' > "$OUTPUT_FILE"

TRANSCRIPTION=$(cat "$OUTPUT_FILE")

if [[ -z "$TRANSCRIPTION" ]]; then
  echo "ERROR: Transcription empty. Audio may be too short or unclear." >&2
  exit 1
fi

# Output result
echo "✅  Transcription:" >&2
echo "" >&2
echo "$TRANSCRIPTION"

# Also copy to clipboard
echo "$TRANSCRIPTION" | pbcopy
echo "" >&2
echo "📋  Copied to clipboard." >&2

# Cleanup
rm -f "$AUDIO_FILE" "$OUTPUT_FILE"
