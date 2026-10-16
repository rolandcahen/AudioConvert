#!/usr/bin/env python3
"""
audio_engine.py — Moteur de conversion de banques de sons vers WAV 48 kHz / 32-bit float

Fonctionnalités :
  • Détection du vrai format via ffprobe (ignore les extensions fausses ou absentes)
  • Conversion → WAV pcm_f32le 48 kHz, nombre de pistes conservé
  • Fusion des paires désentrelacées .L / .R (ou nom.L.ext / nom.R.ext) en stéréo
  • Renommage : suppression des accents, espaces et caractères non-standards
  • Réparation des fichiers corrompus : 7 stratégies de secours successives
  • Conversion des PCM bruts sans en-tête (anciens Sound Designer II, etc.)
  • Conversions en parallèle, écriture atomique (pas de WAV partiel en cas d'échec)
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

# Nombre de conversions simultanées (ffmpeg PCM est surtout limité par le disque)
DEFAULT_WORKERS = max(1, min(os.cpu_count() or 4, 8))

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


def _run(cmd: list) -> subprocess.CompletedProcess:
    """subprocess.run sans stdin (évite qu'ffmpeg attende le clavier en parallèle)."""
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)


# ── Détection ────────────────────────────────────────────────────────────────

def ffprobe_audio(fpath: Path) -> Optional[tuple]:
    """Retourne (codec, sample_rate, channels) ou None si le fichier n'est pas audio."""
    r = _run(["ffprobe", "-v", "quiet", "-print_format", "json",
              "-show_streams", "-select_streams", "a:0", str(fpath)])
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
    try:
        if fpath.stat().st_size < 1024:
            return False
    except OSError:
        return False
    r = _run(["file", str(fpath)])
    out = r.stdout.decode("utf-8", errors="replace").lower()
    if not any(k in out for k in ("data", "common data format", "raw")):
        return False
    try:
        with open(fpath, "rb") as fh:          # ne lit que 8 Ko, pas tout le fichier
            sample = fh.read(8192)
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
    Écriture dans un fichier temporaire puis renommage : un échec ou une
    interruption ne laisse jamais de WAV partiel (qui serait ensuite « ignoré
    car existant » au lancement suivant).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.stem}.part.wav")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    cmd += list(pre_global)
    for flags, path in inputs:
        cmd += list(flags) + ["-i", str(path)]
    cmd += list(extra) + ["-c:a", TARGET_CODEC, "-ar", str(TARGET_RATE), str(tmp)]
    r = _run(cmd)
    # Vérifie aussi que le fichier de sortie n'est pas un WAV vide (> 100 octets)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 100:
        os.replace(tmp, dst)
        return True
    try:
        tmp.unlink()
    except OSError:
        pass
    return False


def repair_convert(src: Path, dst: Path) -> tuple:
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


def natural_dst(src: Path, root: Path, out_root: Path, flatten: bool = False,
                stem: Optional[str] = None) -> Path:
    """
    Chemin de destination avec nom nettoyé.
    flatten=False : arborescence conservée (comportement par défaut).
    flatten=True  : tous les fichiers dans out_root/, préfixés par leurs
                    dossiers parents pour éviter les collisions de noms.
    stem          : nom de base imposé (utilisé pour les paires L/R).
    """
    rel   = src.relative_to(root)
    parts = list(rel.parts)
    stem  = sanitize_name(stem if stem is not None else Path(parts[-1]).stem)
    if flatten and len(parts) > 1:
        prefix = "_".join(sanitize_name(p) for p in parts[:-1])
        stem   = f"{prefix}_{stem}"
    if flatten:
        return out_root / (stem + ".wav")
    return out_root / Path(*parts[:-1]) / (stem + ".wav")


def unique_dst(nat: Path, seen: set) -> Path:
    """
    Garantit un nom unique dans ce lot : kick.aif et kick.wav donneraient
    tous deux kick.wav → le second devient kick_2.wav au lieu d'être ignoré.
    Comparaison insensible à la casse (APFS/HFS+ le sont par défaut).
    """
    cand, n = nat, 2
    while str(cand).lower() in seen:
        cand = nat.with_name(f"{nat.stem}_{n}{nat.suffix}")
        n += 1
    seen.add(str(cand).lower())
    return cand


def _lr_side(f: Path) -> Optional[tuple]:
    """
    Reconnaît les mono désentrelacés :
      nom.L / nom.R            (Sound Designer II, anciens Pro Tools)
      nom.L.wav / nom.R.aif    (Pro Tools récents, multi-mono)
    Retourne (clé_de_groupe, côté, nom_de_base) ou None.
    """
    ext = f.suffix.upper()
    if ext in (".L", ".R"):
        return (str(f.parent / f.stem).lower(), ext, f.stem)
    inner = Path(f.stem).suffix.upper()
    if inner in (".L", ".R") and ext:
        base = Path(f.stem).stem
        return (str(f.parent / base).lower() + "|" + ext.lower(), inner, base)
    return None


def group_lr_pairs(files: list) -> tuple:
    """
    Identifie les paires stéréo désentrelacées .L / .R (insensible à la casse).
    Retourne (fichiers_normaux, [(gauche, droite, nom_de_base)], orphelins).
    """
    by_key: dict = {}
    for f in files:
        side = _lr_side(f)
        if side:
            key, s, base = side
            by_key.setdefault(key, {"base": base})[s] = f

    used, pairs, orphans = set(), [], []
    for sides in by_key.values():
        base = sides.pop("base")
        if ".L" in sides and ".R" in sides:
            pairs.append((sides[".L"], sides[".R"], base))
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
    cancelled:   bool = False
    start_time: datetime = field(default_factory=datetime.now)

    def add(self, tag: str, msg: str):
        self.lines.append(f"[{tag}] {msg}")

    def summary(self) -> str:
        elapsed = int((datetime.now() - self.start_time).total_seconds())
        total = (self.converted + self.merged + self.orphans + self.repaired
                 + self.raw_converted + self.raw_merged + self.raw_orphans)
        head = "Annulé" if self.cancelled else "Terminé"
        parts = [
            f"{head} en {elapsed}s — {total} WAV 48 kHz / 32-bit float créés",
            f"Standard  : {self.converted} conv / {self.merged} fusionnés L+R / {self.orphans} orphelins",
            f"PCM brut  : {self.raw_converted} conv / {self.raw_merged} fusionnés / {self.raw_orphans} orphelins",
        ]
        if self.repaired:
            parts.append(f"Réparés   : {self.repaired} (fichiers corrompus récupérés)")
        parts.append(f"Ignorés   : {self.skipped} / Erreurs : {self.errors}")
        return "\n".join(parts)


# ── Tâches de conversion ──────────────────────────────────────────────────────

@dataclass
class Job:
    tag:     str                 # CONV, MERGE, ORPHAN, RAW, RAW_MERGE, RAW_ORPHAN
    counter: str                 # attribut de Report à incrémenter en cas de succès
    name:    str                 # nom affiché dans le journal
    msg:     str                 # ligne du rapport (sans la destination)
    dst:     Optional[Path]      # None → ignoré (existe déjà)
    op:      Callable            # op(dst) -> bool
    repair:  Optional[Callable] = None   # repair(dst) -> (bool, méthode)


@dataclass
class Ctx:
    root:        Path
    out_root:    Path
    overwrite:   bool
    flatten:     bool
    seen: set = field(default_factory=set)

    def dst(self, src: Path, stem: Optional[str] = None) -> Optional[Path]:
        """Destination unique, ou None si elle existe déjà et qu'on n'écrase pas."""
        d = unique_dst(natural_dst(src, self.root, self.out_root, self.flatten, stem),
                       self.seen)
        return None if (d.exists() and not self.overwrite) else d


def build_audio_jobs(ctx: Ctx, audio_files: list, try_repair: bool) -> list:
    regular, pairs, orphans = group_lr_pairs(audio_files)
    jobs = []
    for s in regular:
        jobs.append(Job(
            "CONV", "converted", s.name, s.name, ctx.dst(s),
            lambda d, s=s: _ffmpeg(d, [([], s)]),
            (lambda d, s=s: repair_convert(s, d)) if try_repair else None,
        ))
    for l, r, base in pairs:
        jobs.append(Job(
            "MERGE", "merged", base, f"{l.name} + {r.name}", ctx.dst(l, base),
            lambda d, l=l, r=r: _ffmpeg(d, [([], l), ([], r)], STEREO_MERGE),
        ))
    for s in orphans:
        jobs.append(Job(
            "ORPHAN", "orphans", s.name, f"{s.name} (orphelin)", ctx.dst(s),
            lambda d, s=s: _ffmpeg(d, [([], s)]),
            (lambda d, s=s: repair_convert(s, d)) if try_repair else None,
        ))
    return jobs


def build_raw_jobs(ctx: Ctx, raw_files: list, rate: int, bits: int, le: bool) -> list:
    fmt = pcm_ffmpeg_fmt(bits, le)
    pre = ["-f", fmt, "-ar", str(rate), "-ac", "1"]
    regular, pairs, orphans = group_lr_pairs(raw_files)
    jobs = []
    for s in regular:
        jobs.append(Job(
            "RAW", "raw_converted", s.name, f"{s.name} (PCM brut)", ctx.dst(s),
            lambda d, s=s: _ffmpeg(d, [(pre, s)]),
        ))
    for l, r, base in pairs:
        jobs.append(Job(
            "RAW_MERGE", "raw_merged", base, f"{l.name} + {r.name} (PCM brut stéréo)",
            ctx.dst(l, base),
            lambda d, l=l, r=r: _ffmpeg(d, [(pre, l), (pre, r)], STEREO_MERGE),
        ))
    for s in orphans:
        jobs.append(Job(
            "RAW_ORPHAN", "raw_orphans", s.name, f"{s.name} (PCM brut, orphelin)",
            ctx.dst(s),
            lambda d, s=s: _ffmpeg(d, [(pre, s)]),
        ))
    return jobs


def _run_job(job: Job, dry_run: bool, cancel_flag: list, progress_cb: Callable) -> tuple:
    """
    Exécute une tâche (dans un thread du pool).
    Retourne (statut, lignes_de_rapport) ; statut : ok | repaired | skip | error | cancel
    """
    if cancel_flag[0]:
        return "cancel", []
    try:
        if job.dst is None:
            progress_cb("SKIP", f"{job.name} — déjà converti")
            return "skip", [f"[SKIP] {job.name} — déjà converti"]

        lines = [f"[{job.tag}] {job.msg} → {job.dst.name}"]
        progress_cb(job.tag, job.name)
        if dry_run or job.op(job.dst):
            return "ok", lines

        if job.repair:
            ok, method = job.repair(job.dst)
            if ok:
                lines.append(f"[REPAIRED] Réparé ({method}) : {job.name}")
                progress_cb("REPAIRED", f"{job.name} ({method})")
                return "repaired", lines

        lines.append(f"[ERROR] Échec : {job.name}")
        progress_cb("ERROR", job.name)
        return "error", lines
    finally:
        progress_cb("__STEP__", "")


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


def scan_files(root: Path, progress_cb: Callable, exclude: Optional[Path] = None) -> tuple:
    exclude = exclude.resolve() if exclude else None
    all_files = []
    for dp, dirs, files in os.walk(root):
        # Ignore les dossiers cachés et le dossier de sortie s'il est dans la source
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and (Path(dp, d).resolve() != exclude)
        )
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
    workers: int = DEFAULT_WORKERS,
) -> Report:
    """
    Appeler depuis un thread secondaire.
    progress_cb(tag, message) est appelé depuis les threads de travail.
    Un événement ("__STEP__", "") est émis après chaque tâche terminée.
    """
    report = Report()
    ctx = Ctx(root=root, out_root=out_root, overwrite=overwrite, flatten=flatten)

    audio, raw, skip_list, _ = scan_files(root, progress_cb, exclude=out_root)

    for fpath, reason in skip_list:
        report.add("SKIP", f"{fpath.name} — {reason}")
        report.skipped += 1

    jobs = build_audio_jobs(ctx, audio, try_repair)
    if not skip_raw:
        jobs += build_raw_jobs(ctx, raw, rate, bits, le)

    progress_cb("__TOTAL__", str(len(jobs)))
    progress_cb("INFO", (
        f"Trouvé : {len(audio)} audio, {len(raw)} PCM brut, "
        f"{len(skip_list)} ignorés — {len(jobs)} WAV à produire "
        f"({workers} en parallèle)"
    ))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = ex.map(lambda j: _run_job(j, dry_run, cancel_flag, progress_cb), jobs)
        # ex.map rend les résultats dans l'ordre des tâches → rapport stable
        for job, (status, lines) in zip(jobs, results):
            report.lines.extend(lines)
            if status == "ok":
                setattr(report, job.counter, getattr(report, job.counter) + 1)
            elif status == "repaired":
                report.repaired += 1
            elif status == "skip":
                report.skipped += 1
            elif status == "error":
                report.errors += 1

    report.cancelled = bool(cancel_flag[0])
    return report
