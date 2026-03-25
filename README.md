# PX Dictate

**Free & open-source voice-to-text for macOS — 100% local, private, no cloud.**

Powered by [Whisper](https://github.com/ggerganov/whisper.cpp). Hold a key, speak, and your words appear wherever your cursor is. No subscription. No data leaves your Mac. Free and open-source.

Perfect for vibecoding, writing, meetings, brainstorming, journaling — anything where your voice is faster than your keyboard.

---

## Features

- **Local transcription** — Whisper AI runs entirely on your Mac. Zero data leaves your device.
- **Floating widget** — Frosted glass pill that follows you across spaces, even fullscreen.
- **Smart hotkeys** — Hold `fn` to record, tap `Control` to pause & process segments.
- **Multi-language** — Auto-detects English, Spanish, French & more per segment.
- **Auto-paste** — Transcribed text is automatically pasted into your active app.
- **Session recording** — Save audio (MP3) and timestamped transcripts per session.
- **History** — Last 10 transcriptions accessible from the menu bar.
- **Privacy by design** — Audio deleted immediately after transcription (unless you choose to save).

## System Requirements

- **macOS** 10.15 Catalina or later
- **Minimum:** 8GB RAM, any Mac 2018+ (Intel or Apple Silicon)
- **Recommended:** 16GB RAM, Apple Silicon (M1/M2/M3/M4) for fastest transcription
- ~500MB disk space for the Whisper model

> **Older or low-RAM Mac?** A future update will add an OpenAI Whisper API option — cloud-based transcription at ~$0.006/minute. All you'll need is an OpenAI account.

## First Run — Permissions

The first time you open PX Dictate, macOS will ask for these permissions. **All are required for core functionality:**

| Permission | Why |
|------------|-----|
| **Microphone** | To record your voice |
| **Accessibility** | To paste transcribed text into your active app |
| **Notifications** | To show status updates (optional but recommended) |

Go to **System Settings > Privacy & Security** to grant each one. You may need to restart the app after granting permissions.

You'll also need to change one keyboard setting:
- **System Settings > Keyboard > Press fn key to > "Do Nothing"**

This lets PX Dictate capture the `fn` key as a hotkey.

## Quick Start

### Option A: One-Command Installer (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/pxinnovative/px-dictate/master/install.sh | bash
```

The installer walks you through each step with confirmations. It will:
1. Check your system (macOS, Python, architecture)
2. Install Homebrew (if needed)
3. Install dependencies (`whisper-cpp`, `portaudio`, `ffmpeg`, Python packages)
4. Download a Whisper model (you choose the size)
5. Build and install PX Dictate.app

Flags: `--yes` (skip confirmations), `--model small` (pre-select model), `--no-build` (deps only).

### Option B: Manual Install

#### Prerequisites

- **Python 3.9+** (pre-installed on macOS)
- **[Homebrew](https://brew.sh)** (macOS package manager)

#### 1. Install dependencies

```bash
brew install whisper-cpp portaudio ffmpeg
pip3 install pyaudio rumps pyobjc
```

#### 2. Download the Whisper model (~465MB, one-time)

```bash
mkdir -p ~/.px-dictate/models
curl -L -o ~/.px-dictate/models/ggml-small.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

#### 3. Run

```bash
python3 px_dictate_app.py
```

Look for the 🎙️ icon in your menu bar. That's PX Dictate.

> **First run?** macOS will ask for Microphone, Accessibility, and Notification permissions. Grant all of them, then restart the app. See [First Run — Permissions](#first-run--permissions) above.

## Keyboard Shortcuts

| Action | How |
|--------|-----|
| **Start recording** | Short press `fn` (0.5-1.5s) or click the widget |
| **Stop recording** | Press `fn` again, `ESC`, or click the widget |
| **Hold-to-record** | Hold `fn` for 1.5s+ — release to stop |
| **Pause & process** | Tap `Control` (solo, no other key) |
| **Resume** | Tap `Control` again |

## Settings

All settings are available in the menu bar dropdown and persist across restarts. Configuration is stored in `~/Library/Application Support/PX Dictate/`.

### Environment Variables (optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `~/.px-dictate/models/ggml-small.bin` | Path to Whisper model |
| `WHISPER_CLI` | `whisper-cli` | Path to whisper-cpp binary |
| `PX_DICTATE_LANG` | `auto` | Default language |
| `PX_DICTATE_SENSITIVITY` | `8.0` | Mic sensitivity multiplier |

## Build as .app

```bash
python3 setup.py py2app
# Output: dist/PX Dictate.app
```

## Local vs Cloud Transcription

|  | Local (default) | OpenAI Whisper API |
|--|-----------------|-------------------|
| **Privacy** | 100% on your Mac | Audio sent to OpenAI |
| **Cost** | Free | ~$0.006/minute |
| **Speed** | 2-5s (Apple Silicon), 5-15s (Intel) | 1-3s |
| **Requires** | 8GB+ RAM, whisper-cpp | OpenAI API key |
| **Internet** | Not needed | Required |

> Note: OpenAI API option is planned for a future release. Today, everything runs 100% locally.

## Why PX Dictate?

| | PX Dictate | Wispr | SuperWhisper | Others |
|--|----------|-------|-------------|--------|
| **Price** | **Free** | Paid subscription | Paid | Varies |
| **Privacy** | 100% local | Cloud | Local option | Cloud |
| **Open source** | Yes (AGPL-3.0) | No | No | Varies |
| **Vibecoding ready** | Yes | Yes | Yes | No |
| **Customizable** | Fully — it's your code | No | Limited | No |

You own the code. You own your data. No vendor lock-in. No surprise pricing changes. No "we updated our privacy policy" emails.

## Community

- Star this repo if PX Dictate is useful to you
- Join the conversation in [GitHub Issues](../../issues)
- Share your experience — we want to hear how you use it
- PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md)

This is the first open-source project from PX Innovative. We're building in public and we want your input.

## Roadmap

### v1.1 — Completed
- [x] Theme system — 3 themes: Glass (default), Classic, Minimal with light/dark mode support
- [x] Minimum recording duration — prevents Whisper hallucinations on short recordings
- [x] Voice Isolation tip — onboarding wizard page + Help menu guidance
- [x] Check for Updates — compares against GitHub Releases
- [x] Configurable hotkeys — fn, Ctrl+Opt+V, F5, Double-tap Option
- [x] ESC to cancel — discards recording without transcribing
- [x] Silence auto-cancel — 20s of no speech triggers countdown and auto-cancel
- [x] Light mode support — readable text in macOS light appearance

### v1.2 — In Progress
- [ ] Multi-mode hotkeys — hold, double-tap, and single-tap-stop on every key ([#15](../../issues/15))
- [ ] Transcript preview in pill — 2-line animated text ([#16](../../issues/16))
- [ ] Audio waveform visualization — animated alternative to VU meter ([#14](../../issues/14))
- [ ] Custom vocabulary / domain-specific terms ([#5](../../issues/5))
- [ ] Onboarding wizard redesign ([#6](../../issues/6))
- [ ] Double-tap fn trigger ([#12](../../issues/12))
- [ ] Wake word detection — "Hey Dictate" ([#13](../../issues/13))
- [ ] Self-contained .dmg installer ([#1](../../issues/1))

### Future
- [ ] OpenAI Whisper API fallback for older Macs
- [ ] Homebrew cask (`brew install --cask px-dictate`)
- [ ] Code signing & notarization (Apple Developer)
- [ ] Mac App Store release

## Support

- **Bug reports:** [GitHub Issues](../../issues)
- **Buy me a coffee:** [buymeacoffee.com/pxinnovative](https://buymeacoffee.com/pxinnovative)
- **Star the repo** — it helps more than you think

## License

[AGPL-3.0](LICENSE) — free to use, modify, and distribute. If you distribute a modified version, you must share the source.

"PX Dictate" is a trademark of PX Innovative Solutions Inc. — see [TRADEMARK.md](TRADEMARK.md).

---

Made with 🎙️ by [Victor Kerber](https://github.com/pxinnovative) @ [PX Innovative Solutions Inc.](https://pxinnovative.com)
