#!/usr/bin/env python3
"""
audio_convert_app.py — Interface graphique pour audio_engine
Glisser-déposer, barre de progression, journal coloré, rapport automatique.
"""

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import queue
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio_engine

# ── Drag & drop (tkinterdnd2, optionnel) ─────────────────────────────────────

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _DND = True
    _BaseApp = TkinterDnD.Tk
except ImportError:
    _DND = False
    _BaseApp = tk.Tk

# ── Couleurs du journal ───────────────────────────────────────────────────────

LOG_COLORS = {
    "CONV":       "#c8c8c8",
    "MERGE":      "#6aabf7",
    "ORPHAN":     "#f0a050",
    "RAW":        "#d4a04a",
    "RAW_MERGE":  "#a0c870",
    "RAW_ORPHAN": "#c8a060",
    "REPAIRED":   "#f7c842",
    "SKIP":       "#666666",
    "INFO":       "#60c878",
    "SCAN":       "#9090e8",
    "ERROR":      "#f05050",
}


class AudioConvertApp(_BaseApp):

    def __init__(self):
        super().__init__()
        self.title("Audio Convert — Banque de Sons")
        self.minsize(820, 700)
        self.resizable(True, True)

        self._cancel_flag = [False]
        self._queue: queue.Queue = queue.Queue()
        self._total = 0
        self._processed = 0

        self._check_deps()
        self._build_ui()
        self._poll_queue()

    # ── Vérification de ffmpeg / ffprobe ─────────────────────────────────────

    def _check_deps(self):
        missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
        if missing:
            messagebox.showerror(
                "Dépendances manquantes",
                f"Outil(s) manquant(s) : {', '.join(missing)}\n\n"
                "Lancez ./install.sh, ou installez-les avec Homebrew :\n"
                "  brew install ffmpeg\n\n"
                "Si Homebrew n'est pas installé : https://brew.sh",
            )

    # ── Construction de l'interface ──────────────────────────────────────────

    def _build_ui(self):
        P = {"padx": 14, "pady": 5}
        dnd = "  (glisser-déposer accepté)" if _DND else ""

        # ── Dossier source ───────────────────────────────────────────────────
        src_lf = ttk.LabelFrame(self, text=f"Dossier source{dnd}", padding=8)
        src_lf.pack(fill="x", **P)
        self._src_var = tk.StringVar()
        self._src_var.trace_add("write", self._auto_fill_out)
        src_entry = ttk.Entry(src_lf, textvariable=self._src_var, font=("Menlo", 11))
        src_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(src_lf, text="Parcourir…", command=self._browse_src).pack(
            side="left", padx=(8, 0))
        self._setup_drop(src_lf,    var=self._src_var, as_parent=True)
        self._setup_drop(src_entry, var=self._src_var, as_parent=True)

        # ── Dossier de sortie ────────────────────────────────────────────────
        out_lf = ttk.LabelFrame(self, text=f"Dossier de sortie{dnd}", padding=8)
        out_lf.pack(fill="x", **P)
        self._out_var = tk.StringVar()
        out_entry = ttk.Entry(out_lf, textvariable=self._out_var, font=("Menlo", 11))
        out_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(out_lf, text="Parcourir…", command=self._browse_out).pack(
            side="left", padx=(8, 0))
        self._setup_drop(out_lf,    var=self._out_var, as_parent=False)
        self._setup_drop(out_entry, var=self._out_var, as_parent=False)

        # ── Options ──────────────────────────────────────────────────────────
        opt_lf = ttk.LabelFrame(self, text="Options", padding=10)
        opt_lf.pack(fill="x", **P)

        self._dryrun_var    = tk.BooleanVar(value=False)
        self._overwrite_var = tk.BooleanVar(value=False)
        self._skip_raw_var  = tk.BooleanVar(value=False)
        self._repair_var    = tk.BooleanVar(value=True)
        self._flatten_var   = tk.BooleanVar(value=False)

        # Bandeau d'avertissement dry-run (visible uniquement quand coché)
        self._dryrun_banner = tk.Label(
            opt_lf,
            text="⚠  MODE APERÇU ACTIVÉ — aucun fichier ne sera écrit",
            bg="#7a4000", fg="#ffe080", font=("Helvetica", 11, "bold"),
            pady=4,
        )
        self._dryrun_banner.grid(row=0, column=0, columnspan=2,
                                  sticky="ew", padx=0, pady=(0, 6))
        self._dryrun_banner.grid_remove()   # caché par défaut
        self._dryrun_var.trace_add("write", self._update_dryrun_banner)

        r0, r1 = 1, 2
        ttk.Checkbutton(opt_lf, text="Mode aperçu (dry-run) — simuler sans écrire",
                        variable=self._dryrun_var).grid(
            row=r0, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(opt_lf, text="Écraser les WAV déjà existants",
                        variable=self._overwrite_var).grid(
            row=r0, column=1, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(opt_lf, text="Ignorer les PCM bruts (anciens Mac)",
                        variable=self._skip_raw_var).grid(
            row=r1, column=0, sticky="w", padx=8, pady=2)
        ttk.Checkbutton(opt_lf,
                        text="Tenter réparation des fichiers corrompus (7 stratégies)",
                        variable=self._repair_var).grid(
            row=r1, column=1, sticky="w", padx=8, pady=2)

        r2 = 3
        ttk.Checkbutton(
            opt_lf,
            text="Mettre à plat — tous les fichiers dans un seul dossier "
                 "(les noms de dossiers parents sont ajoutés en préfixe)",
            variable=self._flatten_var,
        ).grid(row=r2, column=0, columnspan=2, sticky="w", padx=8, pady=2)

        ttk.Label(
            opt_lf,
            text="→ Format de sortie : WAV 48 kHz / 32-bit float  |  "
                 "Nombre de pistes conservé  |  Fusions L+R automatiques",
            foreground="#50a870",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=8, pady=(6, 0))

        # ── Paramètres PCM brut ──────────────────────────────────────────────
        pcm_lf = ttk.LabelFrame(
            self,
            text="Paramètres PCM brut — Sound Designer II, fichiers sans en-tête",
            padding=10,
        )
        pcm_lf.pack(fill="x", **P)

        ttk.Label(pcm_lf, text="Fréquence :").grid(row=0, column=0, sticky="e", padx=4)
        self._rate_var = tk.StringVar(value="44100")
        ttk.Combobox(pcm_lf, textvariable=self._rate_var, width=8,
                     values=["22050", "44100", "48000", "88200", "96000"]).grid(
            row=0, column=1, sticky="w", padx=2)
        ttk.Label(pcm_lf, text="Hz").grid(row=0, column=2, sticky="w", padx=(0, 12))

        ttk.Label(pcm_lf, text="Bits :").grid(row=0, column=3, sticky="e", padx=4)
        self._bits_var = tk.StringVar(value="16")
        ttk.Combobox(pcm_lf, textvariable=self._bits_var, width=5,
                     values=["8", "16", "24", "32"]).grid(
            row=0, column=4, sticky="w", padx=2)

        ttk.Label(pcm_lf, text="  Ordre :").grid(row=0, column=5, sticky="e", padx=(12, 4))
        self._le_var = tk.BooleanVar(value=False)
        ttk.Radiobutton(pcm_lf, text="Big-endian (Mac ancien)",
                        variable=self._le_var, value=False).grid(row=0, column=6, padx=4)
        ttk.Radiobutton(pcm_lf, text="Little-endian (PC)",
                        variable=self._le_var, value=True).grid(row=0, column=7, padx=4)

        # ── Boutons ──────────────────────────────────────────────────────────
        btn_f = ttk.Frame(self)
        btn_f.pack(fill="x", padx=14, pady=(4, 6))
        self._start_btn = ttk.Button(btn_f, text="▶  Lancer la conversion",
                                     command=self._start, width=26)
        self._start_btn.pack(side="left", padx=(0, 10))
        self._cancel_btn = ttk.Button(btn_f, text="⏹  Annuler",
                                      command=self._cancel, state="disabled", width=12)
        self._cancel_btn.pack(side="left")

        # ── Progression ──────────────────────────────────────────────────────
        prog_f = ttk.Frame(self)
        prog_f.pack(fill="x", padx=14, pady=(0, 4))
        self._progress_var = tk.DoubleVar()
        self._progress_bar = ttk.Progressbar(
            prog_f, variable=self._progress_var, maximum=100, mode="indeterminate")
        self._progress_bar.pack(fill="x")
        self._status_var = tk.StringVar(value="Prêt — choisissez un dossier source.")
        ttk.Label(prog_f, textvariable=self._status_var, anchor="w").pack(
            fill="x", pady=(4, 0))

        # ── Journal ───────────────────────────────────────────────────────────
        log_lf = ttk.LabelFrame(self, text="Journal", padding=8)
        log_lf.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self._log = scrolledtext.ScrolledText(
            log_lf, height=14, font=("Menlo", 10), state="disabled",
            wrap="none", bg="#1a1a1a", fg="#c8c8c8", insertbackground="white",
            selectbackground="#334")
        self._log.pack(fill="both", expand=True)
        for tag, color in LOG_COLORS.items():
            self._log.tag_config(tag, foreground=color)

    # ── Drag & drop ───────────────────────────────────────────────────────────

    def _setup_drop(self, widget, var: tk.StringVar, as_parent: bool):
        """Configure un widget comme cible de glisser-déposer."""
        if not _DND:
            return
        widget.drop_target_register(DND_FILES)

        def on_drop(event):
            paths = _parse_dnd(event.data)
            if not paths:
                return
            path = paths[0]
            # Si on dépose un fichier sur le champ source, utiliser son dossier parent
            if as_parent and Path(path).is_file():
                path = str(Path(path).parent)
            var.set(path)

        widget.dnd_bind("<<Drop>>", on_drop)

    # ── Bandeau dry-run ───────────────────────────────────────────────────────

    def _update_dryrun_banner(self, *_):
        if self._dryrun_var.get():
            self._dryrun_banner.grid()
            self._start_btn.configure(text="👁  Aperçu (rien ne sera écrit)", width=30)
        else:
            self._dryrun_banner.grid_remove()
            self._start_btn.configure(text="▶  Lancer la conversion", width=26)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _browse_src(self):
        p = filedialog.askdirectory(title="Dossier source — banque de sons")
        if p:
            self._src_var.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="Dossier de sortie des WAV convertis")
        if p:
            self._out_var.set(p)

    def _auto_fill_out(self, *_):
        src = self._src_var.get().rstrip("/")
        if src:
            self._out_var.set(src + "_WAV_CONVERTED")

    # ── Journal ───────────────────────────────────────────────────────────────

    def _log_line(self, tag: str, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", f"[{tag}] {msg}\n", tag if tag in LOG_COLORS else "")
        self._log.see("end")
        self._log.configure(state="disabled")

    # ── Lancement / annulation ────────────────────────────────────────────────

    def _start(self):
        src = self._src_var.get().strip()
        out = self._out_var.get().strip()
        if not src or not Path(src).is_dir():
            messagebox.showwarning("Dossier source",
                                   f"Dossier introuvable :\n{src or '(vide)'}")
            return
        if not out:
            messagebox.showwarning("Dossier de sortie",
                                   "Veuillez indiquer un dossier de sortie.")
            return
        if Path(out).resolve() == Path(src).resolve():
            messagebox.showwarning("Dossier de sortie",
                                   "Le dossier de sortie doit être différent du dossier source.")
            return
        try:
            rate = int(self._rate_var.get())
            bits = int(self._bits_var.get())
            if rate <= 0 or bits not in (8, 16, 24, 32):
                raise ValueError
        except ValueError:
            messagebox.showwarning("Paramètres PCM brut",
                                   "Fréquence ou nombre de bits invalide.")
            return

        self._cancel_flag[0] = False
        self._total = self._processed = 0
        self._start_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress_bar.configure(mode="indeterminate")
        self._progress_bar.start(12)
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._status_var.set("Analyse en cours…")

        threading.Thread(
            target=self._worker,
            kwargs=dict(
                root=Path(src),
                out_root=Path(out),
                dry_run=self._dryrun_var.get(),
                overwrite=self._overwrite_var.get(),
                skip_raw=self._skip_raw_var.get(),
                try_repair=self._repair_var.get(),
                flatten=self._flatten_var.get(),
                rate=rate,
                bits=bits,
                le=self._le_var.get(),
                cancel_flag=self._cancel_flag,
                progress_cb=self._on_progress,
            ),
            daemon=True,
        ).start()

    def _cancel(self):
        self._cancel_flag[0] = True
        self._status_var.set("Annulation…")
        self._cancel_btn.configure(state="disabled")

    # ── Thread worker ─────────────────────────────────────────────────────────

    def _on_progress(self, tag: str, msg: str):
        """Appelé depuis le thread worker — envoie dans la queue pour le thread principal."""
        self._queue.put((tag, msg))

    def _worker(self, **params):
        try:
            report = audio_engine.run_conversion(**params)
            self._queue.put(("__DONE__", report))
        except Exception:
            import traceback
            self._queue.put(("__FATAL__", traceback.format_exc()))

    # ── Polling de la queue (thread principal, toutes les 40 ms) ─────────────

    def _poll_queue(self):
        try:
            while True:
                tag, data = self._queue.get_nowait()
                if tag == "__DONE__":
                    self._on_done(data)
                elif tag == "__FATAL__":
                    self._log_line("ERROR", f"Erreur inattendue :\n{data}")
                    self._on_done(None)
                elif tag == "__TOTAL__":
                    self._total = int(data)
                    self._progress_bar.stop()
                    self._progress_bar.configure(mode="determinate")
                    self._progress_var.set(0)
                elif tag == "__STEP__":
                    # Une tâche terminée (conversion, fusion, ignorée ou échec)
                    self._processed += 1
                    if self._total > 0:
                        pct = min(100.0, self._processed * 100 / self._total)
                        self._progress_var.set(pct)
                        self._status_var.set(
                            f"Traitement  {self._processed} / {self._total} fichiers")
                else:
                    self._log_line(tag, data)
        except queue.Empty:
            pass
        self.after(40, self._poll_queue)

    # ── Fin de traitement ─────────────────────────────────────────────────────

    def _on_done(self, report):
        self._progress_bar.stop()
        self._progress_bar.configure(mode="determinate")
        self._progress_var.set(100 if report else 0)
        self._start_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")

        if not report:
            self._status_var.set("Annulé." if self._cancel_flag[0] else "Erreur.")
            return

        summary = report.summary()
        self._status_var.set(summary.split("\n")[0])

        self._log_line("INFO", "")
        self._log_line("INFO", "══════════════════ RÉSUMÉ ══════════════════")
        for line in summary.split("\n"):
            self._log_line("INFO", line)

        # Sauvegarde du rapport
        out_path = Path(self._out_var.get().strip())
        report_path = out_path.parent / f"RAPPORT_{out_path.name}.txt"
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                "\n".join(report.lines) + "\n\n" + report.summary() + "\n",
                encoding="utf-8",
            )
            self._log_line("INFO", f"Rapport sauvegardé : {report_path}")
        except Exception as e:
            self._log_line("ERROR", f"Rapport non sauvegardé : {e}")

        if report.errors > 0:
            messagebox.showwarning(
                "Conversion terminée avec erreurs",
                f"{report.errors} fichier(s) n'ont pas pu être convertis "
                f"(même après tentative de réparation).\n"
                "Consultez le journal pour les détails.",
            )


# ── Utilitaire DnD ────────────────────────────────────────────────────────────

def _parse_dnd(raw: str) -> list:
    """
    Décode la chaîne renvoyée par tkinterdnd2.
    Les chemins contenant des espaces sont encadrés par {}.
    """
    paths, i = [], 0
    raw = raw.strip()
    while i < len(raw):
        if raw[i] == "{":
            end = raw.index("}", i)
            paths.append(raw[i + 1:end])
            i = end + 2
        else:
            end = raw.find(" ", i)
            if end == -1:
                paths.append(raw[i:])
                break
            paths.append(raw[i:end])
            i = end + 1
    return [p for p in paths if p]


if __name__ == "__main__":
    app = AudioConvertApp()
    app.mainloop()
