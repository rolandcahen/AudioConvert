#!/usr/bin/env python3
"""
audio_engine.py — Moteur de conversion de banques de sons vers WAV 48 kHz / 32-bit float

Fonctionnalités :
  • Détection du vrai format via ffprobe (ignore les extensions fausses ou absentes)
  • Conversion → WAV pcm_f32le 48 kHz, nombre de pistes conservé
  • Fusion des paires désentrelacées .L / .R en fichier stéréo (ou multicanal)
  • Renommage : suppression des accents, espaces et caractères non-standards
  • Réparation des fichiers corrompus : 7 stratégies de secours successives
  • Conversion des PCM bruts sans en-tête (anciens Sound Designer II, etc.)
"""

import os, subprocess, json, unicodedata, re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

# ── Format cible ──────────────────────────────────────────────────────────────

TARGET_CODEC = "pcm_f32le"   # WAV 32-bit IEEE float
TARGET_RATE  = 48000          # Hz ; le nombre de canaux est toujours conservé

# ── Extensions ignorées sans sonder ffprobe ───────────────────────────────────

SKIP_EXTENSIONS = {
    ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".gif", ".tiff", ".bmp",
    ".zip", ".tar", ".gz", ".rar", ".7z",
    ".app", ".dmg", ".pkg", ".exe",
    ".html", ".htm", ".xml", ".json", ".csv",
    ".mid", ".midi", ".ptf", ".ptx", ".als", ".logic",
}

# ── Filtre d'entrelacement stéréo ────────────────────────────────────────────

STEREO_MERGE = (
    "-filter_complex", "[0:a][1:a]amerge=inputs=2[out]",
    "-map", "[out]",
)

# ── Stratégies de réparation (essayées dans l'ordre si conversion standard échoue) ──

REPAIR_STRATEGIES = [
    # Description           flags globaux (avant -i)                          flags par-input
    ("err-ignore",     ["-err_detect", "ignore_err", "-fflags", "+discardcorrupt"], []),
    ("probe-max",      ["-analyzeduration", "100M", "-probesize", "100M"],          []),
    ("raw-s16be-44k",  [],  ["-f", "s16be", "-ar", "44100", "-ac", "1"]),
    ("raw-s16le-44k",  [],  ["-f", "s16le", "-ar", "44100", "-ac", "1"]),
    ("raw-s16be-48k",  [],  ["-f", "s16be", "-ar", "48000", "-ac", "1"]),
    ("raw-s24be-44k",  [],  ["-f", "s24be", "-ar", "44100", "-ac", "1"]),
    ("raw-u8-44k",     [],  ["-f", "u8",    "-ar", "44100", "-ac", "1"]),
]


# ── Détection ────────────────────────────────────────────────────────────────

def ffprobe_audio(fpath: Path) -> Optional[tuple]:
    """Retourne (codec, sample_rate, channels) ou None si le fichier n'est pas audio."""
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "a:0", str(fpath)],
        capture_output=True,
    )
    if r.returncode != 0:
        return None
    try:
        streams = json.loads(r.stdout).get("streams", [])
        if not streams:
            return None
        s = streams[0]
        return s.get("codec_name"), s.get("sample_rate"), s.get("channels")
    except (json.JSONDecodeError, KeyError):
        return None


def is_raw_pcm_candidate(fpath: Path) -> bool:
    """Heuristique : le fichier pourrait être du PCM brut sans en-tête (ex. Sound Designer II)."""
    if fpath.stat().st_size < 1024:
        return False
    r = subprocess.run(["file", str(fpath)], capture_output=True)
    out = r.stdout.decode("utf-8", errors="replace").lower()
    if not any(k in out for k in ("data", "common data format", "raw")):
        return False
    try:
        sample = fpath.read_bytes()[:8192]
        return sample.count(0) / len(sample) < 0.9 if sample else False
    except OSError:
        return False


def pcm_ffmpeg_fmt(bits: int, le: bool) -> str:
    return "u8" if bits == 8 else f"s{bits}{'le' if le else 'be'}"


# ── Conversion ffmpeg ─────────────────────────────────────────────────────────

def _ffmpeg(dst: Path, inputs: list, extra: tuple = (), pre_global: list = ()) -> bool:
    """
    Wrapper ffmpeg unifié → WAV TARGET_CODEC à TARGET_RATE Hz.

    inputs     : [(flags_par_input, chemin_source), ...]
    extra      : flags de sortie supplémentaires (ex. filtre amerge)
    pre_global : flags globaux avant les -i (ex. -err_detect ignore_err)

    Le nombre de canaux de la source est conservé (pas de -ac forcé).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y"] + list(pre_global)
    for flags, path in inputs:
        cmd += flags + ["-i", str(path)]
    cmd += list(extra) + ["-c:a", TARGET_CODEC, "-ar", str(TARGET_RATE), str(dst)]
    r = subprocess.run(cmd, capture_output=True)
    # Vérifie aussi que le fichier de sortie n'est pas un WAV vide (> 100 octets)
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 100


def repair_convert(src: Path, dst: Path) -> tuple[bool, Optional[str]]:
    """
    Essaie 7 stratégies de réparation successives.
    Retourne (succès, nom_de_la_stratégie_utilisée).
    """
    for name, pre_g, per_inp in REPAIR_STRATEGIES:
        if _ffmpeg(dst, [(per_inp, src)], pre_global=pre_g):
            return True, name
    return False, None


# ── Nommage et chemin ─────────────────────────────────────────────────────────

def sanitize_name(name: str) -> str:
    """
    Supprime accents et caractères non-standards d'un nom de fichier.
    Conserve lettres, chiffres, tirets et points.
    """
    normalized = "".join(
        c for c in unicodedata.normalize("NFD", name)
        if unicodedata.category(c) != "Mn"
    )
    cleaned = re.sub(r"[^\w.\-]", "_", normalized)
    return re.sub(r"_+", "_", cleaned).strip("_") or "fichier"


def natural_dst(src: Path, root: Path, out_root: Path, flatten: bool = False) -> Path:
    """
    Chemin de destination avec nom nettoyé.
    flatten=False : arborescence conservée (comportement par défaut).
    flatten=True  : tous les fichiers dans out_root/, préfixés par leurs
                    dossiers parents pour éviter les collisions de noms.
    """
    rel   = src.relative_to(root)
    parts = list(rel.parts)
    stem  = sanitize_name(Path(parts[-1]).stem)
    if flatten and len(parts) > 1:
        prefix = "_".join(sanitize_name(p) for p in parts[:-1])
        stem   = f"{prefix}_{stem}"
    if flatten:
        return out_root / (stem + ".wav")
    return out_root / Path(*parts[:-1]) / (stem + ".wav")


def resolve_dst(nat: Path, overwrite: bool, seen: set) -> Optional[Path]:
    """Retourne le chemin de destination, ou None si le fichier doit être ignoré."""
    if nat in seen:
        return None
    seen.add(nat)
    return None if (nat.exists() and not overwrite) else nat


def group_lr_pairs(files: list) -> tuple:
    """
    Identifie les paires stéréo désentrelacées .L / .R (insensible à la casse).
    Retourne (fichiers_normaux, [(gauche, droite)], orphelins).
    """
    by_stem: dict = {}
    for f in files:
        ext = f.suffix.upper()
        if ext in (".L", ".R"):
            stem = f.parent / f.stem
            by_stem.setdefault(stem, {})[ext] = f

    used, pairs, orphans = set(), [], []
    for stem, sides in by_stem.items():
        if ".L" in sides and ".R" in sides:
            pairs.append((sides[".L"], sides[".R"]))
            used.update(sides.values())
        else:
            orphans.extend(sides.values())
            used.update(sides.values())

    return [f for f in files if f not in used], pairs, orphans


# ── Rapport ───────────────────────────────────────────────────────────────────

@dataclass
class Report:
    lines: list = field(default_factory=list)
    converted:    int = 0   # conversions standard réussies
    merged:       int = 0   # paires L+R entrelacées
    orphans:      int = 0   # L ou R sans leur pendant (converti seul)
    raw_converted:int = 0   # PCM brut converti
    raw_merged:   int = 0   # PCM brut paire L+R
    raw_orphans:  int = 0   # PCM brut orphelin
    repaired:     int = 0   # fichiers récupérés via stratégie de réparation
    skipped:      int = 0
    errors:       int = 0
    start_time: datetime = field(default_factory=datetime.now)

    def add(self, tag: str, msg: str):
        self.lines.append(f"[{tag}] {msg}")

    def summary(self) -> str:
        elapsed = int((datetime.now() - self.start_time).total_seconds())
        total = (self.converted + self.merged + self.orphans + self.repaired
                 + self.raw_converted + self.raw_merged + self.raw_orphans)
        parts = [
            f"Terminé en {elapsed}s — {total} WAV 48 kHz / 32-bit float créés",
            f"Standard  : {self.converted} conv / {self.merged} fusionnés L+R / {self.orphans} orphelins",
            f"PCM brut  : {self.raw_converted} conv / {self.raw_merged} fusionnés / {self.raw_orphans} orphelins",
        ]
        if self.repaired:
            parts.append(f"Réparés   : {self.repaired} (fichiers corrompus récupérés)")
        parts.append(f"Ignorés   : {self.skipped} / Erreurs : {self.errors}")
        return "\n".join(parts)


# ── Contexte de traitement ────────────────────────────────────────────────────

@dataclass
class Ctx:
    report:      Report
    root:        Path
    out_root:    Path
    dry_run:     bool
    overwrite:   bool
    try_repair:  bool
    flatten:     bool        # True → tous les fichiers à plat dans out_root
    cancel_flag: list        # [False] — mettre [True] pour annuler
    progress_cb: Callable   # (tag: str, message: str) -> None
    seen: set = field(default_factory=set)


def _apply(
    ctx: Ctx,
    nat: Path,
    tag: str,
    make_msg,
    op,
    repair_op=None,
) -> str:
    """
    Résout la destination, loggue et exécute l'opération.
    Si op() échoue et ctx.try_repair est True, tente repair_op().
    Retourne : 'ok' | 'repaired' | 'skip' | 'error' | 'cancel'
    """
    if ctx.cancel_flag[0]:
        return "cancel"

    dst = resolve_dst(nat, ctx.overwrite, ctx.seen)
    if dst is None:
        ctx.report.skipped += 1
        ctx.progress_cb("SKIP", nat.name)
        return "skip"

    ctx.report.add(tag, make_msg(dst))
    ctx.progress_cb(tag, nat.name)

    if ctx.dry_run:
        return "ok"

    if op(dst):
        return "ok"

    # Tentative de réparation
    if ctx.try_repair and repair_op:
        ok, method = repair_op(dst)
        if ok:
            ctx.report.add("REPAIRED", f"Réparé ({method}) : {nat.name}")
            ctx.progress_cb("REPAIRED", nat.name)
            return "repaired"

    ctx.report.add("ERROR", f"Échec : {nat.name}")
    ctx.report.errors += 1
    ctx.progress_cb("ERROR", nat.name)
    return "error"


# ── Scan parallèle ────────────────────────────────────────────────────────────

def _probe_one(fpath: Path):
    ext = fpath.suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return fpath, "skip", f"extension '{ext}' ignorée"
    info = ffprobe_audio(fpath)
    if info:
        return fpath, "audio", info
    if is_raw_pcm_candidate(fpath):
        return fpath, "raw", None
    return fpath, "skip", "non-audio"


def scan_files(root: Path, progress_cb: Callable) -> tuple:
    all_files = []
    for dp, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if not f.startswith("."):
                all_files.append(Path(dp) / f)

    progress_cb("SCAN", f"Analyse de {len(all_files)} fichiers…")

    audio, raw, skip_list, probe_cache = [], [], [], {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for fpath, kind, info in ex.map(_probe_one, all_files):
            if kind == "audio":
                audio.append(fpath)
                probe_cache[fpath] = info
            elif kind == "raw":
                raw.append(fpath)
            else:
                skip_list.append((fpath, info))

    return audio, raw, skip_list, probe_cache


# ── Traitement audio standard ─────────────────────────────────────────────────

def process_audio(ctx: Ctx, audio_files: list, probe_cache: dict):
    regular, pairs, orphans = group_lr_pairs(audio_files)

    for src in regular:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(src, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "CONV",
            lambda d, s=src: f"{s.name} → {d.name}",
            lambda d, s=src: _ffmpeg(d, [([], s)]),
            repair_op=lambda d, s=src: repair_convert(s, d),
        )
        if r == "ok":       ctx.report.converted += 1
        elif r == "repaired": ctx.report.repaired += 1

    for left, right in pairs:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(left, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "MERGE",
            lambda d, l=left, ri=right: f"{l.name} + {ri.name} → {d.name}",
            lambda d, l=left, ri=right: _ffmpeg(d, [([], l), ([], ri)], STEREO_MERGE),
        )
        if r in ("ok", "repaired"):
            ctx.report.merged += 1

    for src in orphans:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(src, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "ORPHAN",
            lambda d, s=src: f"{s.name} (orphelin) → {d.name}",
            lambda d, s=src: _ffmpeg(d, [([], s)]),
            repair_op=lambda d, s=src: repair_convert(s, d),
        )
        if r in ("ok", "repaired"):
            ctx.report.orphans += 1


# ── Traitement PCM brut ───────────────────────────────────────────────────────

def process_raw_pcm(ctx: Ctx, raw_files: list, rate: int, bits: int, le: bool):
    fmt = pcm_ffmpeg_fmt(bits, le)

    def pre(ch): return ["-f", fmt, "-ar", str(rate), "-ac", str(ch)]

    regular, pairs, orphans = group_lr_pairs(raw_files)

    for src in regular:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(src, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "RAW",
            lambda d, s=src: f"{s.name} (PCM brut) → {d.name}",
            lambda d, s=src: _ffmpeg(d, [(pre(1), s)]),
        )
        if r in ("ok", "repaired"):
            ctx.report.raw_converted += 1

    for left, right in pairs:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(left, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "RAW_MERGE",
            lambda d, l=left, ri=right: f"{l.name} + {ri.name} (PCM brut stéréo) → {d.name}",
            lambda d, l=left, ri=right: _ffmpeg(d, [(pre(1), l), (pre(1), ri)], STEREO_MERGE),
        )
        if r in ("ok", "repaired"):
            ctx.report.raw_merged += 1

    for src in orphans:
        if ctx.cancel_flag[0]:
            break
        nat = natural_dst(src, ctx.root, ctx.out_root, ctx.flatten)
        r = _apply(
            ctx, nat, "RAW_ORPHAN",
            lambda d, s=src: f"{s.name} (PCM brut, orphelin) → {d.name}",
            lambda d, s=src: _ffmpeg(d, [(pre(1), s)]),
        )
        if r in ("ok", "repaired"):
            ctx.report.raw_orphans += 1


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run_conversion(
    root: Path,
    out_root: Path,
    dry_run: bool,
    overwrite: bool,
    skip_raw: bool,
    try_repair: bool,
    flatten: bool,
    rate: int,
    bits: int,
    le: bool,
    cancel_flag: list,
    progress_cb: Callable,
) -> Report:
    """
    Appeler depuis un thread secondaire.
    progress_cb(tag, message) est appelé depuis ce thread.
    """
    report = Report()
    ctx = Ctx(
        report=report,
        root=root,
        out_root=out_root,
        dry_run=dry_run,
        overwrite=overwrite,
        try_repair=try_repair,
        flatten=flatten,
        cancel_flag=cancel_flag,
        progress_cb=progress_cb,
    )

    audio, raw, skip_list, probe_cache = scan_files(root, progress_cb)

    for fpath, reason in skip_list:
        report.add("SKIP", f"{fpath.name} — {reason}")
        report.skipped += 1

    progress_cb("__TOTAL__", str(len(audio) + len(raw)))
    progress_cb("INFO", (
        f"Trouvé : {len(audio)} audio, {len(raw)} PCM brut, "
        f"{len(skip_list)} ignorés"
    ))

    process_audio(ctx, audio, probe_cache)

    if not skip_raw and not cancel_flag[0]:
        process_raw_pcm(ctx, raw, rate, bits, le)

    return report
