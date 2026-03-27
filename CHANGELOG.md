# Changelog

All notable changes to PX Dictate are documented here.

---

## [1.1.5] — 2026-03-27

### Onboarding Wizard Redesign
- Redesigned from 6 pages → 3 clean pages: Welcome, Quick Setup, You're all set!
- Taller window (560×620) — no scroll required on any page
- Unified background, page indicator dots, modern Back / Next / Done navigation
- Page 1: key features in bold, "No cloud. No subscription." emphasis, REQUIREMENTS section
- Page 2: two-step setup with direct action buttons (Open Accessibility, Open Keyboard)
- Page 3: ✅ checkmark, keyboard shortcuts with bold key names (fn / ctrl / esc), PRO TIP section, italic author signature with PX Innovative hyperlink

### Update Dialog
- Added "What's New →" third button — links directly to GitHub Release notes
- Release notes now include step-by-step update instructions + Accessibility re-add reminder

---

## [1.1.4] — 2026-03-27

### Fixes
- Icon alignment: mic icon x=10→12 in recording and alternating states
- Status label x=12→13 (+1px right) for all status messages
- Silence monitor: immediate "Recording..." restoration when speech detected after "No sound detected..."
- Silence monitor poll interval: 0.5s → 0.15s (faster response)

### New GitHub Issues
- #21 — Mid-recording silence detection (auto-stop / auto-pause)
- #22 — Configurable silence detection timeouts

---

## [1.1.3] — 2026-03-26

### New Features
- **Transcription progress bar** (#18) — smooth 0→100% animation with percentage label
- **SF Symbols** — modern monochrome icons in menu bar and pill (Glass/Minimal themes), emoji for Classic
- **Recording timer** — mm:ss display in pill, pauses and resumes with recording
- **VU meter** — 4-color (green/yellow/orange/red), logarithmic compression

### Improvements
- Hallucination filter: 3 layers (energy RMS threshold + bracket regex + 7-language blocklist)
- Minimal theme: near-invisible background (vibrancy_alpha 0.08)
- Classic theme: red border outline on recording state
- Pause fixes: silence monitor stops on pause, no false auto-cancels

---

## [1.1.2] — 2026-03-22

### New Features
- Light mode support (auto-adapts to macOS appearance)
- Auto-update check on launch (configurable, off by default)
- ESC key cancels recording (discards audio, no transcription)

### Improvements
- Silence detection: configurable threshold, auto-cancel after extended silence
- Hallucination filter v1 (energy-based pre-filter)

---

## [1.1.1] — 2026-03-20

### New Features
- Custom hotkey support (#2) — reassign fn key via Settings
- Session recording improvements: date-organized folders (audio/YYYY/MM/DD/)

### Fixes
- Whisper model path resolution on clean installs
- Menu bar icon sizing on non-Retina displays

---

## [1.1.0] — 2026-03-18

### Major Release

**Theme System** (#4) — Three themes with live switching:
- **Classic** — familiar look, emoji icons, default
- **Glass** — frosted NSVisualEffectView, Sheet material, blue tint
- **Minimal** — near-invisible background, SF Symbols

**Voice Isolation Tip** — new wizard page + Help menu item explaining macOS Mic Mode

**Check for Updates** (#11) — GitHub Releases API comparison, download prompt with version info

**Min recording duration** — recordings under 1.5s discarded (prevents Whisper hallucinations)

### Fixes
- Removed transcription text from notifications (privacy)
- Removed unused NSObject import
- Code: print → _log.error for all runtime messages

### Closed Issues
- #2 (custom hotkeys), #4 (themes), #8 (silence detection), #11 (check for updates)

---

## [0.9-mvp] — 2026-03-10

### Initial Public Release

**Core Features:**
- Free, open-source macOS menu bar voice-to-text
- 100% local — powered by whisper-cpp, no cloud, no subscription
- fn key: short press toggle OR long hold-to-record
- Control key: pause/resume with per-segment transcription
- Auto-paste into active app after transcription
- Floating frosted glass pill widget (works in fullscreen via NSPanel)
- Multi-language auto-detect per segment (16 languages + auto)

**Session Recording:**
- MP3 audio + timestamped transcripts saved locally
- History: last 10 recordings, persists across restarts
- Date-organized folders: Dictations/audio/YYYY/MM/DD/

**App:**
- AGPL-3.0 license
- Menu bar only (LSUIElement: True, no dock icon)
- Onboarding wizard
- Launch at Login (LaunchAgent)
- Startup checks: alerts if whisper-cli or model missing
- Mic error handling
- Three themes foundation: Classic, Glass, Minimal
- About dialog with GitHub + Buy Me a Coffee links

---

## How to Update

1. Download the latest `PX.Dictate.zip` from [Releases](https://github.com/pxinnovative/px-dictate/releases)
2. Quit PX Dictate (menu bar icon → Quit)
3. Replace `/Applications/PX Dictate.app` with the new version
4. Launch PX Dictate
5. **Re-add to Accessibility**: System Settings → Privacy & Security → Accessibility → remove PX Dictate → click + and re-add it. This step is required after every update due to macOS code signature verification.
