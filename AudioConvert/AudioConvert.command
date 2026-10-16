#!/bin/bash
# Double-cliquer sur ce fichier depuis le Finder pour lancer l'app.
# Si macOS bloque : clic-droit → Ouvrir → Ouvrir (une seule fois requise),
# ou lancez ../install.sh qui règle tout automatiquement.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Homebrew (Apple Silicon ou Intel) n'est pas toujours dans le PATH depuis le Finder
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# Python : environnement créé par install.sh en priorité, sinon un Python 3 avec Tkinter
PYTHON=""
for candidate in "$SCRIPT_DIR/.venv/bin/python" /opt/homebrew/bin/python3 \
                 /usr/local/bin/python3 python3 /usr/bin/python3; do
    if command -v "$candidate" &>/dev/null && "$candidate" -c "import tkinter" 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    osascript -e 'display alert "Python 3 (avec Tkinter) introuvable" message "Lancez install.sh depuis le dossier AudioConvert, ou installez Python depuis python.org." as critical'
    exit 1
fi

if ! command -v ffmpeg &>/dev/null || ! command -v ffprobe &>/dev/null; then
    osascript -e 'display alert "ffmpeg introuvable" message "Lancez install.sh, ou installez ffmpeg avec Homebrew :\n  brew install ffmpeg\n\nSi Homebrew n’est pas installé : https://brew.sh" as critical'
    exit 1
fi

cd "$SCRIPT_DIR"
exec "$PYTHON" audio_convert_app.py
