#!/bin/bash
# Double-cliquer sur ce fichier depuis le Finder pour lancer l'app.
# Si macOS bloque : clic-droit → Ouvrir → Ouvrir (une seule fois requise).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Chercher Python 3
PYTHON=""
for candidate in python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python 3 introuvable" message "Installez Python 3 depuis python.org ou via Homebrew : brew install python3" as critical'
    exit 1
fi

# Chercher ffmpeg
if ! command -v ffmpeg &>/dev/null; then
    # Homebrew sur Apple Silicon ou Intel
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
fi

if ! command -v ffmpeg &>/dev/null; then
    osascript -e 'display alert "ffmpeg introuvable" message "Installez ffmpeg avec Homebrew :\n  brew install ffmpeg\n\nSi Homebrew nest pas installé : https://brew.sh" as critical'
    exit 1
fi

cd "$SCRIPT_DIR"
"$PYTHON" audio_convert_app.py
