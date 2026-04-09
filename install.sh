#!/usr/bin/env bash
# PX Dictate — macOS Voice-to-Text Installer
# SPDX-License-Identifier: AGPL-3.0-or-later
# https://github.com/pxinnovative/px-dictate
#
# Compatible with bash 3.2+ (macOS default) — no associative arrays.

# ── Constants (zero hard-coding) ─────────────────────────────────────────────
APP_NAME="PX Dictate"
APP_VERSION="1.0.0"
REPO_URL="https://github.com/pxinnovative/px-dictate.git"
MODELS_DIR="${HOME}/.px-dictate/models"
HUGGINGFACE_BASE="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
MIN_MACOS_VERSION="10.15"
MIN_PYTHON_VERSION="3.9"
BREW_DEPS="whisper-cpp portaudio ffmpeg"
PIP_DEPS="pyaudio rumps pyobjc"
INSTALL_DIR="/Applications"

# ── Lookup helpers (bash 3.2 compatible — no declare -A) ─────────────────────
model_file() {
  case "$1" in
    tiny)     echo "ggml-tiny.bin" ;;
    base)     echo "ggml-base.bin" ;;
    small)    echo "ggml-small.bin" ;;
    medium)   echo "ggml-medium.bin" ;;
    large-v3) echo "ggml-large-v3.bin" ;;
  esac
}

model_size() {
  case "$1" in
    tiny)     echo "75 MB" ;;
    base)     echo "142 MB" ;;
    small)    echo "466 MB" ;;
    medium)   echo "1.5 GB" ;;
    large-v3) echo "3.1 GB" ;;
  esac
}

dep_binary() {
  case "$1" in
    whisper-cpp) echo "whisper-cli" ;;
    ffmpeg)      echo "ffmpeg" ;;
    *)           echo "" ;;
  esac
}

pip_import() {
  case "$1" in
    pyaudio) echo "pyaudio" ;;
    rumps)   echo "rumps" ;;
    pyobjc)  echo "objc" ;;
  esac
}

# ── CLI flags ────────────────────────────────────────────────────────────────
AUTO_YES=false
NO_BUILD=false
PRESELECTED_MODEL=""

usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  -y, --yes            Skip all confirmations (auto-yes)
  --no-build           Install dependencies only, don't build .app
  --model <size>       Pre-select Whisper model (tiny|base|small|medium|large-v3)
  -h, --help           Show this help message

Examples:
  $0                   Interactive install
  $0 -y --model small  Unattended install with small model
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes)       AUTO_YES=true; shift ;;
    --no-build)     NO_BUILD=true; shift ;;
    --model)        PRESELECTED_MODEL="$2"; shift 2 ;;
    -h|--help)      usage ;;
    *)              echo "Unknown option: $1"; usage ;;
  esac
done

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

info()    { printf "${BLUE}[info]${RESET}  %s\n" "$1"; }
ok()      { printf "${GREEN}[  ok]${RESET}  %s\n" "$1"; }
warn()    { printf "${YELLOW}[warn]${RESET}  %s\n" "$1"; }
fail()    { printf "${RED}[fail]${RESET}  %s\n" "$1"; }
step()    { printf "\n${BOLD}── Step %s ──${RESET}\n" "$1"; }

# Prompt returns 0 (yes) or 1 (no). Respects --yes flag.
confirm() {
  if $AUTO_YES; then return 0; fi
  printf "${BOLD}%s [Y/n]${RESET} " "$1"
  read -r answer
  case "${answer:-Y}" in
    [Yy]*) return 0 ;;
    *)     return 1 ;;
  esac
}

# Compare two dotted version strings: returns 0 if $1 >= $2
version_gte() {
  [ "$(printf '%s\n%s' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]
}

# ── Header ───────────────────────────────────────────────────────────────────
clear 2>/dev/null || true
printf "${CYAN}"
cat <<'BANNER'
    ____  _  __   ____  _      __        __
   / __ \| |/ /  / __ \(_)____/ /_____ _/ /____
  / /_/ /|   /  / / / / / ___/ __/ __ `/ __/ _ \
 / ____//   |  / /_/ / / /__/ /_/ /_/ / /_/  __/
/_/    /_/|_| /_____/_/\___/\__/\__,_/\__/\___/
╔══════════════════════════════════════════════╗
║  Private Voice-to-Text Tool - 100% Local AI  ║
╚══════════════════════════════════════════════╝
BANNER
printf "${RESET}\n"
info "Version ${APP_VERSION}"
echo ""

# ── Step 1: System Check ────────────────────────────────────────────────────
step "1/6 — System Check"

# macOS only
if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "${APP_NAME} is macOS-only. Detected: $(uname -s)"
  exit 1
fi
ok "macOS detected"

# Architecture
ARCH="$(uname -m)"
if [[ "${ARCH}" == "arm64" ]]; then
  ok "Apple Silicon (${ARCH})"
else
  ok "Intel (${ARCH})"
fi

# macOS version (10.15+ required)
MACOS_VERSION="$(sw_vers -productVersion)"
if version_gte "${MACOS_VERSION}" "${MIN_MACOS_VERSION}"; then
  ok "macOS ${MACOS_VERSION} (requires ${MIN_MACOS_VERSION}+)"
else
  fail "macOS ${MACOS_VERSION} is below minimum ${MIN_MACOS_VERSION}"
  exit 1
fi

# Python 3.9+
if ! command -v python3 &>/dev/null; then
  fail "Python 3 not found. Install from https://python.org or via Homebrew."
  exit 1
fi
PY_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if version_gte "${PY_VERSION}" "${MIN_PYTHON_VERSION}"; then
  ok "Python ${PY_VERSION} (requires ${MIN_PYTHON_VERSION}+)"
else
  fail "Python ${PY_VERSION} is below minimum ${MIN_PYTHON_VERSION}"
  exit 1
fi

# ── Step 2: Homebrew ────────────────────────────────────────────────────────
step "2/6 — Homebrew"

if command -v brew &>/dev/null; then
  ok "Homebrew found: $(brew --prefix)"
else
  warn "Homebrew is not installed."
  if confirm "Homebrew is required. Install it?"; then
    info "Installing Homebrew (this may take a minute)..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Ensure brew is on PATH for Apple Silicon
    if [[ -x "/opt/homebrew/bin/brew" ]]; then
      eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    ok "Homebrew installed"
  else
    echo ""
    info "Install Homebrew manually:"
    echo "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    echo ""
    info "Then re-run this installer."
    exit 0
  fi
fi

# ── Step 3: System Dependencies ─────────────────────────────────────────────
step "3/6 — System Dependencies"

MISSING_BREW=()
for dep in ${BREW_DEPS}; do
  bin="$(dep_binary "${dep}")"
  if brew list --formula "${dep}" &>/dev/null; then
    ok "${dep} installed (brew)"
  elif [[ -n "${bin}" ]] && command -v "${bin}" &>/dev/null; then
    ok "${dep} found: $(command -v "${bin}")"
  elif [[ "${dep}" == "portaudio" ]] && find /opt/homebrew/lib /usr/local/lib -name "libportaudio*" -print -quit 2>/dev/null | grep -q .; then
    ok "${dep} installed (library found)"
  else
    warn "${dep} not found"
    MISSING_BREW+=("${dep}")
  fi
done

if [[ ${#MISSING_BREW[@]} -gt 0 ]]; then
  if confirm "Install missing dependencies (${MISSING_BREW[*]})?"; then
    info "Running: brew install ${MISSING_BREW[*]}"
    brew install "${MISSING_BREW[@]}"
    ok "System dependencies installed"
  else
    warn "Skipped. Some features may not work without: ${MISSING_BREW[*]}"
  fi
else
  ok "All system dependencies present"
fi

# ── Step 4: Python Packages ─────────────────────────────────────────────────
step "4/6 — Python Packages"

MISSING_PIP=()
for pkg in ${PIP_DEPS}; do
  import_name="$(pip_import "${pkg}")"
  if python3 -c "import ${import_name}" &>/dev/null 2>&1; then
    ok "${pkg} installed"
  else
    warn "${pkg} not found"
    MISSING_PIP+=("${pkg}")
  fi
done

if [[ ${#MISSING_PIP[@]} -gt 0 ]]; then
  if confirm "Install missing Python packages (${MISSING_PIP[*]})?"; then
    info "Running: pip3 install ${MISSING_PIP[*]}"
    pip3 install "${MISSING_PIP[@]}"
    ok "Python packages installed"
  else
    warn "Skipped. ${APP_NAME} will not run without: ${MISSING_PIP[*]}"
  fi
else
  ok "All Python packages present"
fi

# ── Step 5: Whisper Model ───────────────────────────────────────────────────
step "5/6 — Whisper Model"

mkdir -p "${MODELS_DIR}"

# Check if any model already exists
EXISTING_MODEL="$(find "${MODELS_DIR}" -name 'ggml-*.bin' -print -quit 2>/dev/null)"

if [[ -n "${EXISTING_MODEL}" ]]; then
  ok "Model found: $(basename "${EXISTING_MODEL}")"
else
  info "No Whisper model found in ${MODELS_DIR}"

  CHOSEN_MODEL="${PRESELECTED_MODEL}"

  if [[ -z "${CHOSEN_MODEL}" ]]; then
    echo ""
    echo "  Choose a Whisper model:"
    echo "    1) tiny      ($(model_size tiny))   — fastest, lower quality"
    echo "    2) base      ($(model_size base))  — fast, fair quality"
    echo "    3) small     ($(model_size small))  — balanced [recommended]"
    echo "    4) medium    ($(model_size medium)) — slow, very good quality"
    echo "    5) large-v3  ($(model_size large-v3)) — slowest, best quality"
    echo "    s) Skip      — I'll download later"
    echo ""

    if $AUTO_YES; then
      CHOSEN_MODEL="small"
      info "Auto-selecting: small (recommended)"
    else
      printf "${BOLD}  Choice [3]:${RESET} "
      read -r choice
      case "${choice:-3}" in
        1) CHOSEN_MODEL="tiny" ;;
        2) CHOSEN_MODEL="base" ;;
        3) CHOSEN_MODEL="small" ;;
        4) CHOSEN_MODEL="medium" ;;
        5) CHOSEN_MODEL="large-v3" ;;
        s|S) CHOSEN_MODEL="" ;;
        *) warn "Invalid choice, skipping model download."; CHOSEN_MODEL="" ;;
      esac
    fi
  fi

  if [[ -n "${CHOSEN_MODEL}" ]]; then
    MODEL_FILE="$(model_file "${CHOSEN_MODEL}")"
    if [[ -z "${MODEL_FILE}" ]]; then
      fail "Unknown model: ${CHOSEN_MODEL}. Valid: tiny, base, small, medium, large-v3"
      exit 1
    fi
    MODEL_URL="${HUGGINGFACE_BASE}/${MODEL_FILE}"
    DEST="${MODELS_DIR}/${MODEL_FILE}"

    info "Downloading ${MODEL_FILE} ($(model_size "${CHOSEN_MODEL}"))..."
    if curl -L --progress-bar -o "${DEST}" "${MODEL_URL}"; then
      ok "Model saved to ${DEST}"
    else
      fail "Download failed. You can retry manually:"
      echo "  curl -L -o '${DEST}' '${MODEL_URL}'"
    fi
  else
    info "Skipped. Download a model later with:"
    echo "  curl -L -o '${MODELS_DIR}/ggml-small.bin' '${HUGGINGFACE_BASE}/ggml-small.bin'"
  fi
fi

# ── Step 6: Build & Install ─────────────────────────────────────────────────
step "6/6 — Build & Install"

if $NO_BUILD; then
  info "Skipping build (--no-build flag)."
  info "Run directly with: python3 px_dictate_app.py"
else
  # Detect if we're inside the repo already
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "${SCRIPT_DIR}/px_dictate_app.py" ]]; then
    REPO_DIR="${SCRIPT_DIR}"
    ok "Running from repo: ${REPO_DIR}"
  elif [[ -f "./px_dictate_app.py" ]]; then
    REPO_DIR="$(pwd)"
    ok "Running from repo: ${REPO_DIR}"
  else
    if confirm "Clone ${APP_NAME} repository?"; then
      REPO_DIR="${HOME}/px-dictate"
      info "Cloning to ${REPO_DIR}..."
      git clone "${REPO_URL}" "${REPO_DIR}"
      ok "Repository cloned"
    else
      info "Skipping clone. You can run from source later."
      REPO_DIR=""
    fi
  fi

  if [[ -n "${REPO_DIR}" ]]; then
    if confirm "Build ${APP_NAME}.app and install to ${INSTALL_DIR}?"; then
      info "Building ${APP_NAME}.app (clean build)..."
      cd "${REPO_DIR}"
      rm -rf build/ dist/
      python3 setup.py py2app

      APP_BUNDLE="dist/${APP_NAME}.app"
      if [[ -d "${APP_BUNDLE}" ]]; then
        info "Installing to ${INSTALL_DIR}..."
        rm -rf "${INSTALL_DIR}/${APP_NAME}.app"
        cp -R "${APP_BUNDLE}" "${INSTALL_DIR}/"
        ok "${APP_NAME}.app installed to ${INSTALL_DIR}"
      else
        fail "Build did not produce ${APP_BUNDLE}. Check errors above."
      fi
    else
      info "Skipped build. Run directly with: python3 ${REPO_DIR}/px_dictate_app.py"
    fi
  fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
printf "  ${GREEN}${BOLD}✓ ${APP_NAME} is ready!${RESET}\n"
echo ""
echo "  Open ${APP_NAME} from your Applications folder or Spotlight."
echo ""
printf "  ${BOLD}First run permissions needed:${RESET}\n"
echo "    • Microphone     — to record your voice"
echo "    • Accessibility  — to paste text into active apps"
echo "    • Notifications  — for status updates (recommended)"
echo ""
printf "  Go to ${BOLD}System Settings > Privacy & Security${RESET} to grant each one.\n"
printf "  Also set: ${BOLD}System Settings > Keyboard${RESET} > \"Press fn key to\" > \"Do Nothing\"\n"
echo ""
