#!/bin/bash
# AudioConvert — installer
#
#   ./install.sh              install dependencies + create ~/Applications/AudioConvert.app
#   ./install.sh --no-app     install dependencies only (launch with AudioConvert.command)
#   ./install.sh --uninstall  remove the virtual environment and the .app launcher
#
# What it does (macOS):
#   1. checks for Homebrew (offers to install it if missing)
#   2. installs ffmpeg (provides ffmpeg + ffprobe) if missing
#   3. finds a Python 3 with a working Tkinter (installs python-tk via Homebrew if needed)
#   4. creates a private virtual environment in AudioConvert/.venv
#      and installs tkinterdnd2 (optional drag & drop support)
#   5. removes the macOS quarantine flag and creates a double-clickable .app
# Nothing is installed system-wide except Homebrew packages.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$REPO_DIR/AudioConvert"
VENV="$APP_DIR/.venv"
BUNDLE="$HOME/Applications/AudioConvert.app"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }
ask()  { local a; read -r -p "  $1 [y/N] " a </dev/tty || true; [[ "$a" =~ ^[YyOo] ]]; }

MAKE_APP=1
case "${1:-}" in
    --no-app)    MAKE_APP=0 ;;
    --uninstall)
        rm -rf "$VENV" "$BUNDLE"
        echo "Removed $VENV and $BUNDLE (Homebrew packages left untouched)."
        exit 0 ;;
    -h|--help)   sed -n '2,16p' "$0"; exit 0 ;;
    "") ;;
    *) die "Unknown option: $1 (try --help)" ;;
esac

OS="$(uname -s)"
bold "AudioConvert installer ($OS)"

# Homebrew paths are not always in PATH (e.g. fresh shells on Apple Silicon)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
if [ "$OS" = "Darwin" ]; then
    if ! command -v brew >/dev/null; then
        warn "Homebrew not found (needed to install ffmpeg)."
        if ask "Install Homebrew now (official script from https://brew.sh)?"; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
        fi
    fi
    command -v brew >/dev/null && ok "Homebrew: $(brew --prefix)"
fi

# ── 2. ffmpeg / ffprobe ───────────────────────────────────────────────────────
if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
    ok "ffmpeg: $(ffmpeg -version 2>/dev/null | sed -n 1p | cut -d' ' -f1-3)"
elif command -v brew >/dev/null; then
    bold "Installing ffmpeg with Homebrew (this can take a few minutes)…"
    brew install ffmpeg
    ok "ffmpeg installed"
elif [ "$OS" = "Linux" ]; then
    die "ffmpeg missing — install it with your package manager, e.g.: sudo apt install ffmpeg python3-tk python3-venv"
else
    die "ffmpeg missing and Homebrew unavailable — install Homebrew (https://brew.sh) then rerun."
fi

# ── 3. Python 3 with Tkinter ──────────────────────────────────────────────────
has_tk() { "$1" -c 'import sys, tkinter; assert sys.version_info >= (3, 9)' 2>/dev/null; }

PYTHON=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 /usr/bin/python3; do
    if command -v "$c" >/dev/null && has_tk "$c"; then PYTHON="$(command -v "$c")"; break; fi
done

if [ -z "$PYTHON" ] && command -v brew >/dev/null; then
    # Homebrew's Python ships without Tkinter: add the matching python-tk formula
    if ! command -v "$(brew --prefix)/bin/python3" >/dev/null; then
        bold "Installing Python with Homebrew…"
        brew install python
    fi
    BREW_PY="$(brew --prefix)/bin/python3"
    PYVER="$("$BREW_PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    bold "Installing Tkinter for Python $PYVER (python-tk@$PYVER)…"
    brew install "python-tk@$PYVER"
    has_tk "$BREW_PY" && PYTHON="$BREW_PY"
fi
if [ -z "$PYTHON" ]; then
    [ "$OS" = "Linux" ] && die "No Python ≥ 3.9 with Tkinter found — e.g.: sudo apt install python3-tk python3-venv"
    die "No Python ≥ 3.9 with Tkinter found. Install one from https://www.python.org/downloads/ and rerun."
fi
ok "Python: $PYTHON ($("$PYTHON" -V 2>&1))"

# ── 4. Virtual environment + optional drag & drop ────────────────────────────
if [ ! -x "$VENV/bin/python" ] || ! has_tk "$VENV/bin/python"; then
    rm -rf "$VENV"
    "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
if "$VENV/bin/python" -m pip install --quiet -r "$REPO_DIR/requirements.txt"; then
    if "$VENV/bin/python" -c 'import tkinterdnd2' 2>/dev/null; then
        ok "Drag & drop enabled (tkinterdnd2)"
    fi
else
    warn "tkinterdnd2 could not be installed — the app works, but without drag & drop."
fi
ok "Virtual environment: $VENV"

# ── 5. Launchers ──────────────────────────────────────────────────────────────
chmod +x "$APP_DIR/AudioConvert.command" "$REPO_DIR/install.sh"
if [ "$OS" = "Darwin" ]; then
    xattr -dr com.apple.quarantine "$REPO_DIR" 2>/dev/null || true

    if [ "$MAKE_APP" = 1 ]; then
        mkdir -p "$BUNDLE/Contents/MacOS"
        cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>AudioConvert</string>
  <key>CFBundleIdentifier</key><string>com.github.rolandcahen.audioconvert</string>
  <key>CFBundleExecutable</key><string>AudioConvert</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.2</string>
  <key>LSMinimumSystemVersion</key><string>10.13</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
        cat > "$BUNDLE/Contents/MacOS/AudioConvert" <<LAUNCH
#!/bin/bash
# Generated by install.sh — rerun it if you move the AudioConvert folder.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
APP="$APP_DIR"
if [ ! -f "\$APP/audio_convert_app.py" ]; then
    osascript -e 'display alert "AudioConvert folder not found" message "It was moved or deleted. Run install.sh again from its new location." as critical'
    exit 1
fi
cd "\$APP" && exec "$VENV/bin/python" audio_convert_app.py
LAUNCH
        chmod +x "$BUNDLE/Contents/MacOS/AudioConvert"
        ok "App created: $BUNDLE (find it in Launchpad / Spotlight)"
    fi
fi

echo
bold "Done."
if [ "$OS" = "Darwin" ] && [ "$MAKE_APP" = 1 ]; then
    echo "  Launch AudioConvert from ~/Applications, Spotlight, or double-click AudioConvert/AudioConvert.command"
else
    echo "  Launch with: $APP_DIR/AudioConvert.command"
fi
