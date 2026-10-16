# AudioConvert

**Batch-modernise old sound libraries into clean, uniform WAV files.**

AudioConvert walks through a folder (and all its sub-folders) and converts every audio file it can find into **WAV 48 kHz / 32-bit float**, keeping the original channel count. It is designed for legacy material that modern software often refuses to open: Sound Designer II files, headerless PCM from old Macs, split mono `.L` / `.R` pairs, files with wrong or missing extensions, and partially corrupted files.

Written in Python with a simple Tkinter interface; all the audio work is done by [ffmpeg](https://ffmpeg.org).

---

## Features

- **Real format detection** — every file is probed with `ffprobe`, so wrong or missing extensions don't matter.
- **One target format** — WAV, 48 kHz, 32-bit float (`pcm_f32le`). Mono stays mono, stereo stays stereo, multichannel stays multichannel.
- **Split stereo re-interleaving** — `name.L` + `name.R` (Sound Designer II / old Pro Tools) and `name.L.wav` + `name.R.wav` (multi-mono exports) are merged into one stereo file. An `L` or `R` without its partner is converted on its own and reported as an *orphan*.
- **Headerless PCM** — raw sample data (e.g. SDII files that lost their resource fork) is converted using the sample rate, bit depth and byte order you choose.
- **Repair mode** — if a normal conversion fails, 7 fallback strategies are tried in turn (ignore decoding errors, deep probing, several raw PCM interpretations).
- **Clean file names** — accents, spaces and unusual characters are removed (`Kick Ü 01.aif` → `Kick_U_01.wav`). Name collisions are numbered (`kick_2.wav`) instead of being silently skipped.
- **Folder structure preserved** — or *flattened* into a single folder, with parent folder names added as a prefix.
- **Safe to re-run** — existing WAVs are skipped unless you tick *Overwrite*; output is written atomically, so a failed or interrupted conversion never leaves a half-written file behind.
- **Parallel conversion** — several files are converted at once (up to 8, depending on your CPU).
- **Dry-run preview**, coloured log, progress bar, and a text report saved next to the output folder.
- **Drag & drop** of folders onto the window (when `tkinterdnd2` is installed — the installer does it).

## Requirements

- macOS (Apple Silicon or Intel). Linux works too, see below.
- [Homebrew](https://brew.sh) — the installer can install it for you.
- ffmpeg, and Python ≥ 3.9 with Tkinter — both installed by the installer.

## Installation (macOS)

1. Download the project: green **Code** button → **Download ZIP**, then unzip it where you want to keep it (or `git clone https://github.com/rolandcahen/AudioConvert.git`).
2. Open **Terminal**, drag the `AudioConvert` folder onto the Terminal window after typing `cd ` and press Enter, then run:

   ```bash
   ./install.sh
   ```

The installer:

- installs Homebrew (after asking), ffmpeg, and a Python with Tkinter if they are missing;
- creates a private Python environment in `AudioConvert/.venv` (nothing is added to your system Python);
- installs optional drag & drop support;
- removes the macOS quarantine flag, so you don't get the "unidentified developer" warning;
- creates **AudioConvert.app** in `~/Applications` (available from Spotlight and Launchpad).

Options:

| Command | Effect |
|---|---|
| `./install.sh` | Full install + `.app` launcher |
| `./install.sh --no-app` | Install dependencies only |
| `./install.sh --uninstall` | Remove `.venv` and the `.app` (Homebrew packages are kept) |

> The `.app` points to the folder you installed from. If you move the folder, run `./install.sh` again.

## Launching

- **AudioConvert** from Spotlight / Launchpad / `~/Applications`, or
- double-click `AudioConvert/AudioConvert.command` in the Finder.

## Usage

The interface is in French; the labels on screen are shown in brackets below.

1. **Source folder** (*Dossier source*) — the sound library to convert (*Parcourir…* or drag & drop).
2. **Output folder** (*Dossier de sortie*) — filled in automatically as `<source>_WAV_CONVERTED`; change it if you like. It must be different from the source folder.
3. **Options**
   - *Dry-run* (*Mode aperçu*) — simulate everything and log what would be done, without writing anything.
   - *Overwrite existing WAVs* (*Écraser les WAV déjà existants*) — otherwise files already converted are skipped.
   - *Skip raw PCM* (*Ignorer les PCM bruts*) — ignore headerless files.
   - *Try to repair corrupted files* (*Tenter réparation…*) — enables the 7 fallback strategies (on by default).
   - *Flatten* (*Mettre à plat*) — put everything into one folder, prefixing file names with their parent folders.
4. **Raw PCM parameters** (*Paramètres PCM brut*) — only used for headerless files: sample rate, bit depth and byte order (*big-endian* for old Macs / Sound Designer II, *little-endian* for PC files). If raw files sound like noise or play at the wrong speed, change these and run again with *Overwrite*.
5. Click **▶ Lancer la conversion** (start). *⏹ Annuler* cancels after the files in progress. The log is colour-coded:

| Tag | Meaning |
|---|---|
| `CONV` | standard conversion |
| `MERGE` | L + R pair merged into stereo |
| `ORPHAN` | L or R file without its partner |
| `RAW` / `RAW_MERGE` / `RAW_ORPHAN` | the same, for headerless PCM |
| `REPAIRED` | recovered by a repair strategy (the strategy name is shown) |
| `SKIP` | not audio, or already converted |
| `ERROR` | could not be converted, even after repair |

At the end, a report `RAPPORT_<output folder name>.txt` is written next to the output folder.

### Good to know

- The source files are **never modified**.
- Hidden files and folders (starting with `.`) are ignored, as are obvious non-audio extensions (documents, images, archives, MIDI, session files…).
- Headerless PCM detection is a heuristic: any unknown binary file that "looks like data" is treated as raw audio. Check the `RAW` entries in the log, and tick *Skip raw PCM* if your folders contain other binary files.
- Folder names are kept as they are; only file names are cleaned (except in *Flatten* mode).

## Linux

```bash
sudo apt install ffmpeg python3-tk python3-venv   # or your distribution's equivalent
./install.sh --no-app
./AudioConvert/AudioConvert.command
```

## Troubleshooting

| Problem | Solution |
|---|---|
| "ffmpeg not found" | Run `./install.sh`, or `brew install ffmpeg`. |
| macOS refuses to open `AudioConvert.command` | Run `./install.sh` once, or right-click → **Open** → **Open**. |
| Drag & drop does not work | Run `./install.sh` (it installs `tkinterdnd2`); *Parcourir…* always works. |
| The `.app` says the folder was not found | You moved the project folder: run `./install.sh` again from its new location. |
| Raw PCM files sound wrong | Try the other byte order, or another sample rate / bit depth, with *Overwrite* ticked. |

## Project structure

```
install.sh                    installer (macOS / Linux)
requirements.txt              optional Python dependency (drag & drop)
AudioConvert/
  AudioConvert.command        Finder launcher
  audio_convert_app.py        graphical interface (Tkinter)
  audio_engine.py             conversion engine (can be used without the GUI)
```

`audio_engine.run_conversion()` can also be called from your own Python scripts; see its docstring.

## Author

Roland Cahen
