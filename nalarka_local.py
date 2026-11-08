# Nalarka Motion Graphics Studio (GUI-on-core, v8.1)
import os
import sys
import json
import time
import queue
import shutil
import threading
import traceback
import webbrowser
import multiprocessing
from pathlib import Path
from typing import List, Dict, Tuple, Optional, Any

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from nalarka.core import *

class NalarkaStudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Nalarka Motion Graphics Studio v8.1 (Universal Procedural Vector Engine — OpenRouter + Long-Video Mode)")
        self.geometry("1240x1010")
        self.minsize(1080, 880)

        self.log_queue = queue.Queue()
        self.current_script_data = None
        self.current_plan = None
        self.current_namespace: Dict[str, Any] = {}
        self.current_words = []
        self.ref_library_dir = DEFAULT_REF_LIBRARY_DIR

        # Per-provider settings so switching providers never overwrites the other one's key/models.
        self._active_provider = PROVIDER_CUSTOM
        self.provider_cfg: Dict[str, Dict[str, str]] = {
            PROVIDER_CUSTOM: {
                "base_url": PROVIDER_DEFAULT_BASE_URL[PROVIDER_CUSTOM], "api_key": "",
                "coder_model": OAO_VISUAL_CODER_MODELS[0], "script_model": "deepseek-v4-flash",
            },
            PROVIDER_OPENROUTER: {
                "base_url": PROVIDER_DEFAULT_BASE_URL[PROVIDER_OPENROUTER], "api_key": "",
                "coder_model": OPENROUTER_VISUAL_CODER_MODELS[0], "script_model": OPENROUTER_SCRIPT_MODELS[0],
            },
        }
        self.model_lists: Dict[str, Tuple[List[str], List[str]]] = {
            PROVIDER_CUSTOM: (list(OAO_VISUAL_CODER_MODELS), list(OAO_SCRIPT_MODELS)),
            PROVIDER_OPENROUTER: (list(OPENROUTER_VISUAL_CODER_MODELS), list(OPENROUTER_SCRIPT_MODELS)),
        }

        self._build_ui()
        self._load_config()
        self._on_max_duration_changed()
        self._update_provider_hint()
        self._refresh_reference_library()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- SECTION 1: AI PROVIDER (CUSTOM OPENAI-COMPATIBLE / OPENROUTER) & DUAL MODEL CONFIGURATION ---
        ai_box = ttk.LabelFrame(main_frame, text=" 1. AI Provider: Custom OpenAI-compatible (oao.ai.id) or OpenRouter ", padding=6)
        ai_box.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(ai_box, text="Provider:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.provider_var = tk.StringVar(value=PROVIDER_LABELS[PROVIDER_CUSTOM])
        self.provider_combo = ttk.Combobox(
            ai_box, textvariable=self.provider_var, values=list(PROVIDER_LABELS.values()), state="readonly", width=34
        )
        self.provider_combo.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=2)
        self.provider_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_provider_changed())

        self.provider_hint_var = tk.StringVar(value="")
        ttk.Label(ai_box, textvariable=self.provider_hint_var, foreground="#555555").grid(
            row=0, column=2, columnspan=2, sticky=tk.W, padx=4, pady=2
        )

        ttk.Label(ai_box, text="Base URL:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.base_url_var = tk.StringVar(value=PROVIDER_DEFAULT_BASE_URL[PROVIDER_CUSTOM])
        ttk.Entry(ai_box, textvariable=self.base_url_var, width=34).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="Vector Artist Model (Stage 5):").grid(row=1, column=2, sticky=tk.W, padx=4, pady=2)
        self.coder_model_var = tk.StringVar(value="claude-fable-5")
        self.coder_combo = ttk.Combobox(ai_box, textvariable=self.coder_model_var, values=OAO_VISUAL_CODER_MODELS, width=26)
        self.coder_combo.grid(row=1, column=3, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="API Key:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=2)
        self.api_key_var = tk.StringVar(value="")
        ttk.Entry(ai_box, textvariable=self.api_key_var, show="*", width=34).grid(row=2, column=1, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="Script Model (Stage 1):").grid(row=2, column=2, sticky=tk.W, padx=4, pady=2)
        self.script_model_var = tk.StringVar(value="deepseek-v4-flash")
        self.script_combo = ttk.Combobox(ai_box, textvariable=self.script_model_var, values=OAO_SCRIPT_MODELS, width=26)
        self.script_combo.grid(row=2, column=3, sticky=tk.EW, padx=4, pady=2)

        btn_frame_ai = ttk.Frame(ai_box)
        btn_frame_ai.grid(row=0, column=4, rowspan=3, sticky=tk.E, padx=4)
        ttk.Button(btn_frame_ai, text="Save Config", command=self._save_config).grid(row=0, column=0, padx=3, pady=1, sticky=tk.EW)
        ttk.Button(btn_frame_ai, text="Test Vector Model", command=self._test_ai_connection).grid(row=0, column=1, padx=3, pady=1, sticky=tk.EW)
        ttk.Button(btn_frame_ai, text="Fetch OpenRouter Models", command=self._start_fetch_or_models).grid(
            row=1, column=0, columnspan=2, padx=3, pady=1, sticky=tk.EW
        )
        ai_box.columnconfigure(1, weight=2)
        ai_box.columnconfigure(3, weight=1)

        # --- SECTION 2A: TOPIC & STRUCTURED VOICEOVER SCRIPT ---
        script_box = ttk.LabelFrame(main_frame, text=" 2A. Topic, Max Duration & Structured Voiceover Script ([Hook], [Inti 1..N], [Penutup]) ", padding=6)
        script_box.pack(fill=tk.X, pady=(0, 6))

        top_row = ttk.Frame(script_box)
        top_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(top_row, text="Video Topic:").pack(side=tk.LEFT, padx=(0, 6))
        self.topic_var = tk.StringVar(value="Kenapa Suara Sendiri di Rekaman Terdengar Aneh & Bikin Cringe?")
        ttk.Entry(top_row, textvariable=self.topic_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        ttk.Label(top_row, text="Lang:").pack(side=tk.LEFT, padx=(0, 4))
        self.lang_var = tk.StringVar(value="Indonesian")
        ttk.Combobox(top_row, textvariable=self.lang_var, values=["Indonesian", "English"], state="readonly", width=11).pack(side=tk.LEFT, padx=(0, 6))

        self.btn_gen_script = ttk.Button(top_row, text="1. Generate Structured Script (AI)", command=self._start_generate_script)
        self.btn_gen_script.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(top_row, text="Copy Clean Text (For Web TTS)", command=self._copy_clean_text_clipboard).pack(side=tk.LEFT)

        dur_row = ttk.Frame(script_box)
        dur_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(dur_row, text="Max video duration (s):").pack(side=tk.LEFT, padx=(0, 4))
        self.max_dur_var = tk.StringVar(value=str(DEFAULT_MAX_DURATION))
        dur_combo = ttk.Combobox(dur_row, textvariable=self.max_dur_var, values=DURATION_PRESETS, width=6)
        dur_combo.pack(side=tk.LEFT, padx=(0, 8))
        dur_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_max_duration_changed())
        dur_combo.bind("<FocusOut>", lambda _e: self._on_max_duration_changed())
        dur_combo.bind("<Return>", lambda _e: self._on_max_duration_changed())
        self.dur_hint_var = tk.StringVar(value="")
        ttk.Label(dur_row, textvariable=self.dur_hint_var, foreground="#555555").pack(side=tk.LEFT)

        self.script_info_var = tk.StringVar(
            value="Structured Script ([Hook], [Inti 1..N], [Penutup] tags are automatically stripped before TTS):"
        )
        ttk.Label(script_box, textvariable=self.script_info_var).pack(anchor=tk.W)
        self.script_text = ScrolledText(script_box, height=7, font=("Segoe UI", 10), wrap=tk.WORD)
        self.script_text.pack(fill=tk.X, pady=(2, 0))

        default_sample = (
            "[Hook] Dengar rekaman suara sendiri, rasanya geli, malu, dan ingin langsung tekan skip. Padahal orang lain mendengarnya tiap hari. Kok bisa?\n\n"
            "[Inti 1] Saat kita bicara, suara sampai ke telinga dalam lewat dua jalan sekaligus: lewat udara di luar, dan lewat getaran tulang tenggorak di dalam kepala.\n\n"
            "[Inti 2] Tulang tengkorak memperkuat frekuensi rendah, sehingga di kepala kita sendiri, suara kita terdengar lebih dalam, berat, dan hangat.\n\n"
            "[Inti 3] Sebaliknya, mikrofon rekaman hanya menangkap suara yang lewat udara saja. Tanpa efek bass dari tulang, suara kita terdengar lebih tipis dan asing.\n\n"
            "[Penutup] Otak langsung terkejut karena versi tulang sudah jadi andalan seumur hidup. Jadi tenang, suaramu tidak aneh, hanya beda jalur dengar saja."
        )
        self.script_text.insert(tk.END, default_sample)

        # --- SECTION 2B: ELEVENLABS TTS CONFIGURATION ---
        el_box = ttk.LabelFrame(main_frame, text=" 2B. Voiceover TTS Engine (ElevenLabs API v1 + Free Premade Voices & Edge-TTS) ", padding=6)
        el_box.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(el_box, text="TTS Provider:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.tts_provider_var = tk.StringVar(value="ElevenLabs API v1")
        ttk.Combobox(
            el_box, textvariable=self.tts_provider_var,
            values=["ElevenLabs API v1", "Edge-TTS Fallback (Free)"],
            state="readonly", width=24
        ).grid(row=0, column=1, sticky=tk.W, padx=4, pady=2)

        ttk.Label(el_box, text="ElevenLabs API Key:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=2)
        self.el_api_key_var = tk.StringVar(value="")
        ttk.Entry(el_box, textvariable=self.el_api_key_var, show="*", width=34).grid(row=0, column=3, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(el_box, text="Select Voice / ID:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.el_voice_var = tk.StringVar(value=ELEVENLABS_VOICE_PRESETS[0])
        self.el_voice_combo = ttk.Combobox(el_box, textvariable=self.el_voice_var, values=ELEVENLABS_VOICE_PRESETS, width=36)
        self.el_voice_combo.grid(row=1, column=1, sticky=tk.EW, padx=4, pady=2)

        ttk.Button(el_box, text="Fetch My Voices", command=self._start_fetch_el_voices).grid(row=1, column=2, padx=4, pady=2)

        ttk.Label(el_box, text="ElevenLabs Model:").grid(row=1, column=3, sticky=tk.W, padx=4, pady=2)
        self.el_model_var = tk.StringVar(value="eleven_multilingual_v2")
        ttk.Combobox(
            el_box, textvariable=self.el_model_var,
            values=["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_turbo_v2_5"],
            state="readonly", width=22
        ).grid(row=1, column=4, sticky=tk.EW, padx=4, pady=2)

        tune_frame = ttk.Frame(el_box)
        tune_frame.grid(row=2, column=0, columnspan=5, sticky=tk.EW, pady=(4, 2))

        ttk.Label(tune_frame, text="Stability:").pack(side=tk.LEFT, padx=(4, 2))
        self.el_stability_var = tk.DoubleVar(value=0.50)
        ttk.Spinbox(tune_frame, from_=0.0, to=1.0, increment=0.05, textvariable=self.el_stability_var, width=5).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(tune_frame, text="Similarity:").pack(side=tk.LEFT, padx=(0, 2))
        self.el_sim_var = tk.DoubleVar(value=0.75)
        ttk.Spinbox(tune_frame, from_=0.0, to=1.0, increment=0.05, textvariable=self.el_sim_var, width=5).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(tune_frame, text="Style:").pack(side=tk.LEFT, padx=(0, 2))
        self.el_style_var = tk.DoubleVar(value=0.15)
        ttk.Spinbox(tune_frame, from_=0.0, to=1.0, increment=0.05, textvariable=self.el_style_var, width=5).pack(side=tk.LEFT, padx=(0, 8))

        self.el_boost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(tune_frame, text="Speaker Boost", variable=self.el_boost_var).pack(side=tk.LEFT, padx=(0, 8))

        self.el_ssml_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tune_frame, text='Add <break time="0.4s"/>', variable=self.el_ssml_var).pack(side=tk.LEFT, padx=(0, 8))

        self.btn_tts = ttk.Button(tune_frame, text="Generate Voiceover Audio Now", command=self._start_generate_tts)
        self.btn_tts.pack(side=tk.RIGHT, padx=4)
        el_box.columnconfigure(1, weight=1)
        el_box.columnconfigure(3, weight=1)

        # --- SECTION 3: SINGLE-FOLDER REFERENCE LIBRARY & RENDER PIPELINE ---
        asset_box = ttk.LabelFrame(
            main_frame,
            text=" 3. Reference Motion Graphic Library (Single Folder Selector), Assets & 2-Pass Vector Render (Stages 3 - 7) ",
            padding=6
        )
        asset_box.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(asset_box, text="Reference Library:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.ref_choice_var = tk.StringVar(value="None (Default Nalarka Flat-Vector Palette)")
        self.ref_combo = ttk.Combobox(asset_box, textvariable=self.ref_choice_var, state="readonly", width=36)
        self.ref_combo.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=2)
        self.ref_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_reference_selected())

        ref_btn_bar = ttk.Frame(asset_box)
        ref_btn_bar.grid(row=0, column=2, columnspan=4, sticky=tk.W, padx=2, pady=2)
        ttk.Button(ref_btn_bar, text="Refresh Library", command=self._refresh_reference_library).pack(side=tk.LEFT, padx=2)
        ttk.Button(ref_btn_bar, text="Set Library Folder...", command=self._choose_ref_library_dir).pack(side=tk.LEFT, padx=2)
        ttk.Button(ref_btn_bar, text="Import File to Library...", command=self._import_file_to_library).pack(side=tk.LEFT, padx=2)
        self.ref_folder_label_var = tk.StringVar(value=f"Folder: ./{DEFAULT_REF_LIBRARY_DIR}")
        ttk.Label(ref_btn_bar, textvariable=self.ref_folder_label_var, foreground="#555555").pack(side=tk.LEFT, padx=8)

        ttk.Label(asset_box, text="Voiceover Audio:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.vo_path_var = tk.StringVar(value="Suara_Voiceover.mp3")
        ttk.Entry(asset_box, textvariable=self.vo_path_var).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=2)
        ttk.Button(asset_box, text="Browse Audio...", command=self._browse_vo).grid(row=1, column=2, padx=3, pady=2)

        ttk.Label(asset_box, text="Background Music:").grid(row=1, column=3, sticky=tk.W, padx=4, pady=2)
        self.bgm_path_var = tk.StringVar(value="")
        ttk.Entry(asset_box, textvariable=self.bgm_path_var).grid(row=1, column=4, sticky=tk.EW, padx=4, pady=2)
        ttk.Button(asset_box, text="Browse BGM...", command=self._browse_bgm).grid(row=1, column=5, padx=3, pady=2)

        opt_frame = ttk.Frame(asset_box)
        opt_frame.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(6, 2))

        ttk.Label(opt_frame, text="Orientation:").pack(side=tk.LEFT, padx=(4, 4))
        self.orient_var = tk.StringVar(value="Match Reference")
        ttk.Combobox(
            opt_frame, textvariable=self.orient_var,
            values=["Match Reference", "1080x1920 (Portrait Shorts/Reels)", "1920x1080 (Landscape)"],
            state="readonly", width=25
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(opt_frame, text="Whisper:").pack(side=tk.LEFT, padx=(0, 4))
        self.whisper_var = tk.StringVar(value="base")
        ttk.Combobox(opt_frame, textvariable=self.whisper_var, values=["tiny", "base", "small"], state="readonly", width=6).pack(side=tk.LEFT, padx=(0, 8))

        self.synth_bgm_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt_frame, text="Auto-Synth BGM", variable=self.synth_bgm_var).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(opt_frame, text="Render workers:").pack(side=tk.LEFT, padx=(0, 4))
        self.workers_var = tk.IntVar(value=default_render_workers())
        ttk.Spinbox(opt_frame, from_=1, to=16, textvariable=self.workers_var, width=3).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(opt_frame, text="Output:").pack(side=tk.LEFT, padx=(0, 4))
        self.out_path_var = tk.StringVar(value="output_motion_graphic.mp4")
        ttk.Entry(opt_frame, textvariable=self.out_path_var, width=20).pack(side=tk.LEFT, padx=(0, 6))

        self.btn_verify = ttk.Button(opt_frame, text="3. Verify MP4", command=self._start_verify_only)
        self.btn_verify.pack(side=tk.RIGHT, padx=2)

        self.btn_render = ttk.Button(
            opt_frame,
            text="2. Generate Topic Vectors, Audit & Render MP4",
            command=self._start_full_render
        )
        self.btn_render.pack(side=tk.RIGHT, padx=2)

        reroll_frame = ttk.Frame(asset_box)
        reroll_frame.grid(row=3, column=0, columnspan=6, sticky=tk.EW, pady=(4, 2))
        ttk.Label(reroll_frame, text="Beat Inspector / Re-Roll:").pack(side=tk.LEFT, padx=(4, 6))
        self.scene_select_var = tk.StringVar(value="Run Stage 2 first to populate visual beats")
        self.scene_combo = ttk.Combobox(reroll_frame, textvariable=self.scene_select_var, state="readonly", width=42)
        self.scene_combo.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(reroll_frame, text="Custom Note:").pack(side=tk.LEFT, padx=(0, 4))
        self.reroll_note_var = tk.StringVar(value="")
        ttk.Entry(reroll_frame, textvariable=self.reroll_note_var, width=28).pack(side=tk.LEFT, padx=(0, 6))
        self.btn_reroll = ttk.Button(
            reroll_frame,
            text="Regenerate Selected Beat & Re-Render MP4",
            command=self._start_reroll_scene,
            state=tk.DISABLED
        )
        self.btn_reroll.pack(side=tk.LEFT, padx=2)
        self.btn_preview = ttk.Button(
            reroll_frame,
            text="Preview Selected Beat (PNG)",
            command=self._start_preview_beat,
            state=tk.DISABLED
        )
        self.btn_preview.pack(side=tk.LEFT, padx=2)

        asset_box.columnconfigure(1, weight=1)
        asset_box.columnconfigure(4, weight=1)

        # --- SECTION 4: PROGRESS BAR & LOG ---
        prog_frame = ttk.Frame(main_frame)
        prog_frame.pack(fill=tk.X, pady=(0, 4))
        self.progress_var = tk.IntVar(value=0)
        self.progressbar = ttk.Progressbar(prog_frame, variable=self.progress_var, maximum=100)
        self.progressbar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(prog_frame, textvariable=self.status_var, width=38).pack(side=tk.RIGHT)

        log_box = ttk.LabelFrame(main_frame, text=" 4. Real-Time 2-Pass Vector Pipeline, Anti-Repetition Audit & Verification Log ", padding=6)
        log_box.pack(fill=tk.BOTH, expand=True)
        self.log_widget = ScrolledText(log_box, font=("Consolas", 9), bg="#16181D", fg="#E2E8F0")
        self.log_widget.pack(fill=tk.BOTH, expand=True)

    # ==========================================
    # REFERENCE LIBRARY MANAGEMENT
    # ==========================================
    def _refresh_reference_library(self):
        files = scan_reference_library(self.ref_library_dir)
        options = ["None (Default Nalarka Flat-Vector Palette)"] + files
        self.ref_combo["values"] = options
        self.ref_folder_label_var.set(f"Folder: {os.path.abspath(self.ref_library_dir)} ({len(files)} files)")

        current = self.ref_choice_var.get()
        if current not in options:
            self.ref_choice_var.set(options[1] if len(options) > 1 else options[0])
        self.log(f"[REFERENCE LIBRARY] Scanned '{self.ref_library_dir}' -> {len(files)} reference file(s) available.")

    def _choose_ref_library_dir(self):
        folder = filedialog.askdirectory(title="Select Single Folder for Reference Motion Graphic Library")
        if folder:
            self.ref_library_dir = folder
            self._save_config()
            self._refresh_reference_library()

    def _import_file_to_library(self):
        path = filedialog.askopenfilename(
            title="Select Reference Video or Image to Copy into Reference Library",
            filetypes=[("Media Files", "*.mp4 *.mov *.mkv *.webm *.png *.jpg *.jpeg *.webp"), ("All Files", "*.*")]
        )
        if path and os.path.exists(path):
            Path(self.ref_library_dir).mkdir(parents=True, exist_ok=True)
            dest = os.path.join(self.ref_library_dir, os.path.basename(path))
            if os.path.abspath(path) != os.path.abspath(dest):
                shutil.copy2(path, dest)
            self._refresh_reference_library()
            self.ref_choice_var.set(os.path.basename(path))
            self._on_reference_selected()

    def _get_selected_reference_path(self) -> Optional[str]:
        choice = self.ref_choice_var.get().strip()
        if not choice or choice.startswith("None "):
            return None
        cand = os.path.join(self.ref_library_dir, choice)
        return cand if os.path.exists(cand) else None

    def _on_reference_selected(self):
        self._save_config()
        ref_path = self._get_selected_reference_path()
        threading.Thread(target=lambda: analyze_reference_video(ref_path, self.log), daemon=True).start()

    # ==========================================
    # LOGGING & CONFIG PERSISTENCE
    # ==========================================
    def log(self, msg: str):
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {msg}"
        logging.info(msg)
        self.log_queue.put(("log", line))

    def set_progress(self, pct: int, status_text: str):
        self.log_queue.put(("progress", (pct, status_text)))

    def _poll_log_queue(self):
        while not self.log_queue.empty():
            kind, payload = self.log_queue.get_nowait()
            if kind == "log":
                self.log_widget.insert(tk.END, payload + "\n")
                self.log_widget.see(tk.END)
            elif kind == "progress":
                pct, st = payload
                self.progress_var.set(pct)
                self.status_var.set(st)
        self.after(100, self._poll_log_queue)

    # ==========================================
    # PROVIDER SWITCHING, DURATION CAP & CONFIG PERSISTENCE
    # ==========================================
    @staticmethod
    def _provider_key_from_label(label: str) -> str:
        for key, text in PROVIDER_LABELS.items():
            if text == label:
                return key
        return PROVIDER_CUSTOM

    def _stash_provider_fields(self, key: str):
        self.provider_cfg[key].update({
            "base_url": self.base_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "coder_model": self.coder_model_var.get().strip(),
            "script_model": self.script_model_var.get().strip(),
        })

    def _apply_provider_fields(self, key: str):
        cfg = self.provider_cfg[key]
        self.provider_var.set(PROVIDER_LABELS[key])
        self.base_url_var.set(cfg["base_url"])
        self.api_key_var.set(cfg["api_key"])
        self.coder_model_var.set(cfg["coder_model"])
        self.script_model_var.set(cfg["script_model"])
        coder_list, script_list = self.model_lists[key]
        self.coder_combo["values"] = coder_list
        self.script_combo["values"] = script_list
        self._update_provider_hint()

    def _update_provider_hint(self):
        if self._active_provider == PROVIDER_OPENROUTER:
            self.provider_hint_var.set(
                "OpenRouter: key from openrouter.ai/keys. Model ids look like vendor/model. Click 'Fetch OpenRouter Models' for the live list."
            )
        else:
            self.provider_hint_var.set("Any OpenAI-compatible /v1 endpoint (default: oao.ai.id).")

    def _on_provider_changed(self):
        new_key = self._provider_key_from_label(self.provider_var.get())
        if new_key == self._active_provider:
            return
        self._stash_provider_fields(self._active_provider)
        self._active_provider = new_key
        self._apply_provider_fields(new_key)
        self._save_config()
        self.log(f"[PROVIDER] Switched to {PROVIDER_LABELS[new_key]} ({self.base_url_var.get()}).")

    def _get_max_duration(self) -> int:
        """Reads + normalises the duration cap. Call from the UI thread only."""
        try:
            val = int(float(self.max_dur_var.get().strip()))
        except (ValueError, AttributeError):
            val = DEFAULT_MAX_DURATION
        val = max(MIN_DURATION_CAP, min(HARD_MAX_DURATION, val))
        if self.max_dur_var.get().strip() != str(val):
            self.max_dur_var.set(str(val))
        return val

    def _on_max_duration_changed(self):
        d = self._get_max_duration()
        lo, hi = script_word_budget(d)
        beats_lo, beats_hi = int(math.ceil(d / 7.0)), int(math.ceil(d / 4.5))
        text = f"~{lo}-{hi} spoken words, {script_section_count(d)} sections, ~{beats_lo}-{beats_hi} visual beats."
        if d > MAX_DURATION:
            text += f"  EXTENDED MODE: beyond the {MAX_DURATION}s Nalarka spec. More AI calls and a longer render."
        else:
            text += "  Standard Nalarka spec."
        self.dur_hint_var.set(text)

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            providers = cfg.get("providers")
            if isinstance(providers, dict):
                for key, stored in providers.items():
                    if key in self.provider_cfg and isinstance(stored, dict):
                        for field in self.provider_cfg[key]:
                            if field in stored:
                                self.provider_cfg[key][field] = str(stored[field])
                active = cfg.get("ai_provider", PROVIDER_CUSTOM)
            else:
                # legacy single-endpoint config (pre-OpenRouter versions)
                legacy_url = str(cfg.get("base_url", PROVIDER_DEFAULT_BASE_URL[PROVIDER_CUSTOM]))
                active = PROVIDER_OPENROUTER if is_openrouter(legacy_url) else PROVIDER_CUSTOM
                self.provider_cfg[active].update({
                    "base_url": legacy_url,
                    "api_key": str(cfg.get("api_key", "")),
                    "coder_model": str(cfg.get("coder_model", cfg.get("model", self.provider_cfg[active]["coder_model"]))),
                    "script_model": str(cfg.get("script_model", self.provider_cfg[active]["script_model"])),
                })
            if active not in self.provider_cfg:
                active = PROVIDER_CUSTOM
            self._active_provider = active
            self._apply_provider_fields(active)

            self.tts_provider_var.set(cfg.get("tts_provider", self.tts_provider_var.get()))
            self.el_api_key_var.set(cfg.get("elevenlabs_api_key", ""))
            self.el_voice_var.set(cfg.get("elevenlabs_voice_id", ELEVENLABS_VOICE_PRESETS[0]))
            self.el_model_var.set(cfg.get("elevenlabs_model_id", "eleven_multilingual_v2"))
            self.el_stability_var.set(float(cfg.get("elevenlabs_stability", 0.50)))
            self.el_sim_var.set(float(cfg.get("elevenlabs_similarity", 0.75)))
            self.el_style_var.set(float(cfg.get("elevenlabs_style", 0.15)))
            self.ref_library_dir = cfg.get("ref_library_dir", DEFAULT_REF_LIBRARY_DIR)
            if cfg.get("selected_reference"):
                self.ref_choice_var.set(cfg.get("selected_reference"))
            self.max_dur_var.set(str(cfg.get("max_duration", DEFAULT_MAX_DURATION)))
            self.workers_var.set(int(cfg.get("render_workers", default_render_workers())))
            self.log(f"[CONFIG] Loaded configuration from {CONFIG_FILE}")
        except Exception as e:
            self.log(f"[CONFIG WARN] Could not read {CONFIG_FILE}: {e}")

    def _save_config(self):
        self._stash_provider_fields(self._active_provider)
        active = self.provider_cfg[self._active_provider]
        try:
            workers = int(self.workers_var.get())
        except (tk.TclError, ValueError):
            workers = default_render_workers()
        cfg = {
            "ai_provider": self._active_provider,
            "providers": self.provider_cfg,
            # mirrored for older versions of this tool that read a single endpoint
            "base_url": active["base_url"],
            "api_key": active["api_key"],
            "coder_model": active["coder_model"],
            "script_model": active["script_model"],
            "tts_provider": self.tts_provider_var.get().strip(),
            "elevenlabs_api_key": self.el_api_key_var.get().strip(),
            "elevenlabs_voice_id": self.el_voice_var.get().strip(),
            "elevenlabs_model_id": self.el_model_var.get().strip(),
            "elevenlabs_stability": float(self.el_stability_var.get()),
            "elevenlabs_similarity": float(self.el_sim_var.get()),
            "elevenlabs_style": float(self.el_style_var.get()),
            "ref_library_dir": self.ref_library_dir,
            "selected_reference": self.ref_choice_var.get().strip(),
            "max_duration": self._get_max_duration(),
            "render_workers": max(1, min(16, workers)),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        try:
            os.chmod(CONFIG_FILE, 0o600)   # the file holds API keys in plain text; keep it owner-only (no-op on Windows)
        except OSError:
            pass

    def _copy_clean_text_clipboard(self):
        raw_text = self.script_text.get("1.0", tk.END).strip()
        cleaned = prepare_text_for_elevenlabs(raw_text, add_ssml_breaks=False)
        self.clipboard_clear()
        self.clipboard_append(cleaned)
        self.log(f"[CLIPBOARD] Copied cleaned script ({len(cleaned)} chars, all [Tags] removed) to clipboard!")
        messagebox.showinfo("Copied Clean Script", "Clean voiceover text (without [Hook]/[Inti] tags) has been copied to your clipboard!")

    def _browse_vo(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.m4a *.ogg"), ("All Files", "*.*")])
        if path:
            self.vo_path_var.set(path)

    def _browse_bgm(self):
        path = filedialog.askopenfilename(filetypes=[("Audio Files", "*.mp3 *.wav *.m4a *.ogg"), ("All Files", "*.*")])
        if path:
            self.bgm_path_var.set(path)

    # ==========================================
    # WORKER THREADS
    # ==========================================
    def _test_ai_connection(self):
        self._save_config()
        threading.Thread(target=self._worker_test_ai, daemon=True).start()

    def _worker_test_ai(self):
        try:
            self.set_progress(15, f"Testing '{self.coder_model_var.get()}'...")
            res = call_ai_json(
                self.base_url_var.get(),
                self.api_key_var.get(),
                self.coder_model_var.get(),
                'Return ONLY a JSON object: {"status": "ok", "message": "Connection verified!"}',
                "Ping test",
                self.log
            )
            self.log(f"[LLM TEST PASSED] JSON Response: {res}")
            self.set_progress(100, "LLM Connection OK!")
        except Exception as e:
            self.log(f"[LLM TEST FAILED] {e}")
            self.set_progress(0, "LLM Connection Failed.")

    def _start_fetch_or_models(self):
        or_key = (
            self.api_key_var.get().strip()
            if self._active_provider == PROVIDER_OPENROUTER
            else self.provider_cfg[PROVIDER_OPENROUTER]["api_key"]
        )
        threading.Thread(target=self._worker_fetch_or_models, args=(or_key,), daemon=True).start()

    def _worker_fetch_or_models(self, or_key: str):
        try:
            self.set_progress(20, "Fetching OpenRouter model catalogue...")
            models = fetch_openrouter_models(or_key, self.log)
            ids = [m["id"] for m in models]
            free_count = sum(1 for m in models if m["free"])

            def _update():
                self.model_lists[PROVIDER_OPENROUTER] = (ids, ids)
                if self._active_provider == PROVIDER_OPENROUTER:
                    self.coder_combo["values"] = ids
                    self.script_combo["values"] = ids

            self.after(0, _update)
            self.log(f"[OPENROUTER] Loaded {len(ids)} models ({free_count} free). Free models usually end in ':free' and are rate-limited.")
            self.set_progress(100, f"Loaded {len(ids)} OpenRouter models.")
        except Exception as e:
            self.log(f"[OPENROUTER MODEL FETCH ERROR] {e}")
            self.set_progress(0, "Failed to fetch OpenRouter models.")

    def _start_fetch_el_voices(self):
        self._save_config()
        threading.Thread(target=self._worker_fetch_el_voices, daemon=True).start()

    def _worker_fetch_el_voices(self):
        try:
            self.set_progress(20, "Fetching voices from ElevenLabs...")
            voices = fetch_elevenlabs_voices(self.el_api_key_var.get(), self.log)
            combo_items = list(ELEVENLABS_VOICE_PRESETS)
            bagas_item = None
            for v in voices:
                label = f"{v['name']} [{v['category']}] ({v['voice_id']})"
                if label not in combo_items:
                    combo_items.append(label)
                if "bagas" in v["name"].lower():
                    bagas_item = label

            def _update_combo():
                self.el_voice_combo["values"] = combo_items
                if bagas_item:
                    self.el_voice_var.set(bagas_item)
                    self.log(f"[ELEVENLABS] Found Bagas voice: {bagas_item}")

            self.after(0, _update_combo)
            self.set_progress(100, f"Loaded {len(voices)} ElevenLabs voices.")
        except Exception as e:
            self.log(f"[ELEVENLABS VOICE FETCH ERROR] {e}")
            self.set_progress(0, "Failed to fetch ElevenLabs voices.")

    def _start_generate_script(self):
        self._save_config()
        topic = self.topic_var.get().strip()
        if not topic:
            messagebox.showwarning("Empty Topic", "Please enter a video topic first.")
            return
        self.btn_gen_script.config(state=tk.DISABLED)
        max_dur = self._get_max_duration()
        threading.Thread(target=self._worker_generate_script, args=(topic, max_dur), daemon=True).start()

    def _worker_generate_script(self, topic: str, max_dur: int):
        try:
            self.set_progress(15, f"Generating structured script via '{self.script_model_var.get()}' (cap {max_dur}s)...")
            data = generate_script(
                self.base_url_var.get(),
                self.api_key_var.get(),
                self.script_model_var.get(),
                topic,
                self.lang_var.get(),
                self.log,
                max_duration=max_dur
            )
            self.current_script_data = data
            tagged_text = data["tagged_text"]
            clean_text = data["clean_tts_text"]
            word_count = len(clean_text.split())
            est_vo, est_vid = estimate_video_seconds(word_count)

            self.log(f"[SCRIPT] Estimate: ~{word_count} spoken words ≈ {est_vo}s voiceover ≈ {est_vid}s total video (cap {max_dur}s)")
            if est_vid > max_dur:
                self.log(f"[SCRIPT WARN] Estimated video ({est_vid}s) is over the {max_dur}s cap. Shorten the script or raise the cap before rendering.")
            self.log(f"[STRUCTURED SCRIPT OUTPUT]:\n{tagged_text}\n")

            def _update_ui():
                self.script_text.delete("1.0", tk.END)
                self.script_text.insert(tk.END, tagged_text)
                over = "  (OVER CAP!)" if est_vid > max_dur else ""
                self.script_info_var.set(
                    f"Structured Script (~{word_count} spoken words | Est. Voice ~{est_vo}s | Est. Video ~{est_vid}s of {max_dur}s cap{over} — [Tags] stripped for TTS):"
                )

            self.after(0, _update_ui)
            self.set_progress(100, "Structured script ready!")
        except Exception:
            self.log(f"[SCRIPT ERROR]\n{traceback.format_exc()}")
            self.set_progress(0, "Script generation failed.")
        finally:
            self.after(0, lambda: self.btn_gen_script.config(state=tk.NORMAL))

    def _synthesize_voiceover_now(self, raw_editor_text: str, out_vo: str):
        provider = self.tts_provider_var.get()
        if "ElevenLabs" in provider:
            generate_elevenlabs_tts(
                raw_editor_text=raw_editor_text,
                output_path=out_vo,
                xi_api_key=self.el_api_key_var.get(),
                voice_id=self.el_voice_var.get(),
                model_id=self.el_model_var.get(),
                stability=float(self.el_stability_var.get()),
                similarity_boost=float(self.el_sim_var.get()),
                style=float(self.el_style_var.get()),
                use_speaker_boost=bool(self.el_boost_var.get()),
                add_ssml_breaks=bool(self.el_ssml_var.get()),
                log_fn=self.log,
                language_code="en" if "English" in self.lang_var.get() else "id"
            )
        else:
            edge_voice = "en-US-ChristopherNeural" if "English" in self.lang_var.get() else "id-ID-ArdiNeural"
            generate_edge_tts_fallback(raw_editor_text, out_vo, edge_voice, self.log)
        # remember which script this audio was made from (guards against rendering a stale voiceover)
        try:
            with open(out_vo + ".script.sha1", "w", encoding="utf-8") as f:
                f.write(script_fingerprint(raw_editor_text))
        except OSError as err:
            self.log(f"[TTS WARN] Could not write script fingerprint: {err}")

    def _start_generate_tts(self):
        self._save_config()
        raw_text = self.script_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showwarning("Empty Script", "The script editor is empty. Generate or type a script first.")
            return
        out_vo = self.vo_path_var.get().strip() or "Suara_Voiceover.mp3"
        self.btn_tts.config(state=tk.DISABLED)
        max_dur = self._get_max_duration()
        threading.Thread(target=self._worker_tts, args=(raw_text, out_vo, max_dur), daemon=True).start()

    def _worker_tts(self, raw_text: str, out_vo: str, max_dur: int):
        try:
            check_script_fits(raw_text, max_dur, self.log)   # refuse BEFORE spending TTS credits
            self.set_progress(25, f"Synthesizing voiceover via {self.tts_provider_var.get()}...")
            self._synthesize_voiceover_now(raw_text, out_vo)
            dur = get_audio_duration(out_vo, self.log)
            total_dur = math.ceil(dur + INTRO_OUTRO_SECONDS)
            self.log(f"[AUDIO INFO] Voiceover = {dur:.2f}s -> Locked Video Target Duration = {total_dur}s")
            if total_dur > max_dur:
                self.log(f"[AUDIO WARN] {total_dur}s exceeds the {max_dur}s cap. Raise the cap in Section 2A or shorten the script before rendering.")
            self.set_progress(100, f"Voiceover Ready ({dur:.1f}s)")
        except Exception as e:
            self.log(f"[TTS ERROR]\n{traceback.format_exc()}")
            self.set_progress(0, "TTS generation failed.")
            self.after(0, lambda err=str(e): messagebox.showerror("TTS Error", err))
        finally:
            self.after(0, lambda: self.btn_tts.config(state=tk.NORMAL))

    def _resolve_dimensions(self, ref_profile: dict) -> Tuple[int, int]:
        choice = self.orient_var.get()
        if "1080x1920" in choice:
            return 1080, 1920
        elif "1920x1080" in choice:
            return 1920, 1080
        else:
            return int(ref_profile.get("width", 1080)), int(ref_profile.get("height", 1920))

    def _populate_scene_inspector(self, plan: dict):
        scenes = plan.get("scenes", [])
        items = [
            f"Beat #{i+1:02d} [{sc['start']}s-{sc['end']}s] ({sc.get('bg_color', 'cream')}): {sc.get('summary', '')}"
            for i, sc in enumerate(scenes)
        ]
        self.scene_combo["values"] = items
        if items:
            self.scene_select_var.set(items[0])
            self.btn_reroll.config(state=tk.NORMAL)
            self.btn_preview.config(state=tk.NORMAL)

    def _start_preview_beat(self):
        if not self.current_plan or not self.current_plan.get("scenes"):
            return
        m = re.search(r"Beat #(\d+)", self.scene_select_var.get())
        if not m:
            return
        beat_idx = int(m.group(1)) - 1
        ref_profile = analyze_reference_video(self._get_selected_reference_path(), self.log)
        width, height = self._resolve_dimensions(ref_profile)   # read Tk variables on the UI thread
        threading.Thread(target=self._worker_preview_beat, args=(beat_idx, width, height), daemon=True).start()

    def _worker_preview_beat(self, beat_idx: int, width: int, height: int):
        try:
            self.set_progress(40, f"Rendering preview of Beat #{beat_idx + 1:02d}...")
            out_png = os.path.abspath(f"preview_beat_{beat_idx + 1:02d}.png")
            render_beat_preview_sheet(self.current_plan, self.current_namespace, self.current_words, beat_idx, width, height, out_png)
            self.log(f"[PREVIEW] Beat #{beat_idx + 1:02d} contact sheet saved: {out_png}")
            self.set_progress(100, f"Preview ready (Beat #{beat_idx + 1:02d})")
            webbrowser.open(Path(out_png).as_uri())
        except Exception:
            self.log(f"[PREVIEW ERROR]\n{traceback.format_exc()}")
            self.set_progress(0, "Beat preview failed.")

    def _start_reroll_scene(self):
        if not self.current_plan or not self.current_plan.get("scenes"):
            return
        sel = self.scene_select_var.get()
        m = re.search(r"Beat #(\d+)", sel)
        if not m:
            return
        beat_idx = int(m.group(1)) - 1
        self.btn_reroll.config(state=tk.DISABLED)
        self.btn_render.config(state=tk.DISABLED)
        max_dur = self._get_max_duration()
        workers = self._get_workers()
        threading.Thread(target=self._worker_reroll_scene, args=(beat_idx, max_dur, workers), daemon=True).start()

    def _get_workers(self) -> int:
        try:
            return max(1, min(16, int(self.workers_var.get())))
        except (tk.TclError, ValueError):
            return default_render_workers()

    def _worker_reroll_scene(self, beat_idx: int, max_dur: int, workers: int):
        try:
            beat = self.current_plan["scenes"][beat_idx]
            user_note = self.reroll_note_var.get().strip() or "Draw a completely fresh, detailed multi-part flat-vector illustration for this beat."
            if self.reroll_note_var.get().strip():
                beat["visual_blueprint"] = f"{beat.get('visual_blueprint', '')} ({user_note})"

            self.set_progress(25, f"Regenerating Beat #{beat_idx+1:02d} via '{self.coder_model_var.get()}'...")
            new_src = rewrite_single_beat_function_with_ai(
                self.base_url_var.get(),
                self.api_key_var.get(),
                self.coder_model_var.get(),
                self.topic_var.get().strip(),
                beat_idx,
                beat,
                user_note,
                self.log
            )
            if new_src and register_beat_source(self.current_plan, self.current_namespace, f"beat_{beat_idx+1:02d}", new_src, self.log):
                write_scene_module_file(self.current_plan)   # keep generated_scenes.py in sync
            else:
                self.log(f"[REROLL WARN] No valid replacement for Beat #{beat_idx+1:02d}; re-rendering with the previous drawing.")

            ref_profile = analyze_reference_video(self._get_selected_reference_path(), self.log)
            width, height = self._resolve_dimensions(ref_profile)

            vo_path = self.vo_path_var.get().strip()
            total_dur = int(round(float(self.current_plan["scenes"][-1]["end"])))
            bgm_path = self.bgm_path_var.get().strip() or ("synthetic_ambient_bgm.wav" if os.path.exists("synthetic_ambient_bgm.wav") else None)
            out_mp4 = self.out_path_var.get().strip() or "output_motion_graphic.mp4"

            render_video_pipeline(
                plan=self.current_plan,
                namespace=self.current_namespace,
                words=self.current_words,
                vo_path=vo_path,
                bgm_path=bgm_path,
                output_path=out_mp4,
                total_dur=total_dur,
                width=width,
                height=height,
                log_fn=self.log,
                progress_fn=self.set_progress,
                workers=workers
            )
            run_verification_suite(out_mp4, total_dur, width, height, self.log, max_duration=max(max_dur, total_dur))
            self.set_progress(100, f"Beat #{beat_idx+1:02d} Re-Rendered!")
        except Exception as e:
            self.log(f"[REROLL ERROR]\n{traceback.format_exc()}")
            self.set_progress(0, "Beat re-roll failed.")
            self.after(0, lambda err=str(e): messagebox.showerror("Beat Re-Roll Error", err))
        finally:
            self.after(0, lambda: self.btn_reroll.config(state=tk.NORMAL))
            self.after(0, lambda: self.btn_render.config(state=tk.NORMAL))

    def _start_verify_only(self):
        out_mp4 = self.out_path_var.get().strip() or "output_motion_graphic.mp4"
        if not os.path.exists(out_mp4):
            messagebox.showwarning("MP4 Not Found", f"File '{out_mp4}' does not exist yet.")
            return
        max_dur = self._get_max_duration()
        threading.Thread(target=self._worker_verify_only, args=(out_mp4, max_dur), daemon=True).start()

    def _worker_verify_only(self, out_mp4: str, max_dur: int):
        try:
            self.set_progress(50, "Running 7-Point Verification Suite...")
            ref_profile = analyze_reference_video(self._get_selected_reference_path(), self.log)
            w, h = self._resolve_dimensions(ref_profile)
            res = run_verification_suite(out_mp4, None, w, h, self.log, max_duration=max_dur)
            self.set_progress(100, f"Verified: {res['passed']}/{res['total']} Passed!")
            self.after(0, lambda: messagebox.showinfo("Verification Passed", f"All {res['passed']}/{res['total']} checks passed!\nDuration: {res['duration']:.3f}s\nFrames: {res['frames']}"))
        except Exception as e:
            self.log(f"[VERIFICATION FAILED]\n{traceback.format_exc()}")
            self.set_progress(0, "Verification failed.")
            self.after(0, lambda err=str(e): messagebox.showerror("Verification Error", err))

    def _start_full_render(self):
        self._save_config()
        vo_path = self.vo_path_var.get().strip()
        if not vo_path:
            messagebox.showwarning("No Voiceover File", "Please specify a voiceover audio file path.")
            return
        self.btn_render.config(state=tk.DISABLED)
        max_dur = self._get_max_duration()
        workers = self._get_workers()
        threading.Thread(target=self._worker_full_render, args=(max_dur, workers), daemon=True).start()

    def _worker_full_render(self, max_dur: int, workers: int):
        try:
            vo_path = self.vo_path_var.get().strip()
            raw_script_editor = self.script_text.get("1.0", tk.END).strip()
            _, parsed_sections = parse_tagged_script(raw_script_editor)
            if max_dur > MAX_DURATION:
                self.log(f"[EXTENDED MODE] Duration cap {max_dur}s (> {MAX_DURATION}s Nalarka spec). {workers} render worker(s).")

            # Guard: an audio file generated from a DIFFERENT script would silently mismatch the storyboard.
            sidecar = vo_path + ".script.sha1"
            if raw_script_editor and os.path.exists(vo_path) and os.path.exists(sidecar):
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        saved_fp = f.read().strip()
                except OSError:
                    saved_fp = ""
                if saved_fp and saved_fp != script_fingerprint(raw_script_editor):
                    raise ValueError(
                        f"HARD STOP: '{vo_path}' was generated from a different script than the one in the editor. "
                        "Click 'Generate Voiceover Audio Now' to regenerate it, or delete "
                        f"'{os.path.basename(sidecar)}' if you want to keep using this audio as is."
                    )

            if not os.path.exists(vo_path):
                if not raw_script_editor:
                    raise FileNotFoundError(f"Voiceover file '{vo_path}' not found and script editor is empty.")
                self.log(f"[AUTO-TTS] '{vo_path}' not found. Synthesizing via {self.tts_provider_var.get()}...")
                check_script_fits(raw_script_editor, max_dur, self.log)   # refuse BEFORE spending TTS credits
                self.set_progress(8, "Generating voiceover audio...")
                self._synthesize_voiceover_now(raw_script_editor, vo_path)

            self.set_progress(15, "Measuring voiceover duration (ffprobe)...")
            vo_dur = get_audio_duration(vo_path, self.log)
            total_dur = math.ceil(vo_dur + INTRO_OUTRO_SECONDS)
            total_frames = total_dur * FPS
            self.log(f"[DURATION] Voiceover = {vo_dur:.2f}s | Locked Video Target = {total_dur}s ({total_frames} frames @ {FPS}fps)")

            if total_dur > max_dur:
                raise ValueError(
                    f"HARD STOP: Calculated video duration ({total_dur}s) exceeds the {max_dur}s cap! "
                    "Shorten the script or voiceover, or raise 'Max video duration' in Section 2A."
                )

            self.set_progress(22, "Loading Selected Reference from Library...")
            ref_path = self._get_selected_reference_path()
            ref_profile = analyze_reference_video(ref_path, self.log)
            width, height = self._resolve_dimensions(ref_profile)

            self.set_progress(32, "Extracting word timestamps (Faster-Whisper)...")
            words = extract_word_timestamps(
                vo_path, self.whisper_var.get(), self.log,
                language="en" if "English" in self.lang_var.get() else "id"
            )
            self.current_words = words

            coder_model = self.coder_model_var.get().strip()
            topic_str = self.topic_var.get().strip()
            self.set_progress(45, f"Running 2-Pass Storyboard & Vector Artist via '{coder_model}'...")

            plan, namespace = generate_two_pass_storyboard_and_code(
                self.base_url_var.get(),
                self.api_key_var.get(),
                coder_model,
                topic_str,
                parsed_sections,
                words,
                total_dur,
                ref_profile,
                self.log
            )

            self.set_progress(62, "Auditing vector complexity, motion & visual uniqueness...")
            audit_and_fix_beats(
                plan=plan,
                namespace=namespace,
                words=words,
                width=width,
                height=height,
                base_url=self.base_url_var.get(),
                api_key=self.api_key_var.get(),
                model=coder_model,
                topic=topic_str,
                log_fn=self.log,
                max_retries=2,
                progress_fn=self.set_progress
            )

            self.current_plan = plan
            self.current_namespace = namespace
            self.after(0, lambda: self._populate_scene_inspector(plan))

            with open("timeline_plan.json", "w", encoding="utf-8") as f:
                json.dump(plan, f, indent=2, ensure_ascii=False)

            bgm_path = self.bgm_path_var.get().strip() or None
            if (not bgm_path or not os.path.exists(bgm_path)) and self.synth_bgm_var.get():
                bgm_path = generate_synthetic_ambient_bgm(
                    "synthetic_ambient_bgm.wav",
                    total_dur,
                    ref_profile.get("recommended_bpm", 116),
                    self.log
                )

            out_mp4 = self.out_path_var.get().strip() or "output_motion_graphic.mp4"

            render_video_pipeline(
                plan=plan,
                namespace=namespace,
                words=words,
                vo_path=vo_path,
                bgm_path=bgm_path,
                output_path=out_mp4,
                total_dur=total_dur,
                width=width,
                height=height,
                log_fn=self.log,
                progress_fn=self.set_progress,
                workers=workers
            )

            self.set_progress(96, "Running 7-Point Verification Suite...")
            ver_res = run_verification_suite(out_mp4, total_dur, width, height, self.log, max_duration=max_dur)
            self.set_progress(100, f"Completed & Verified! ({out_mp4})")
            self.after(
                0,
                lambda: messagebox.showinfo(
                    "Render & Verification Complete",
                    f"MP4 successfully rendered with Topic-Specific Procedural Vectors and passed {ver_res['passed']}/{ver_res['total']} tests!\n\n"
                    f"Output: {out_mp4}\n"
                    f"Duration: {ver_res['duration']:.3f}s ({ver_res['frames']} frames @ 30fps)\n"
                    f"Pure Python Vector Code: generated_scenes.py\n"
                    f"Visual Sheet: check/verification_contact_sheet.jpg"
                )
            )

        except Exception as e:
            self.log(f"[PIPELINE ERROR]\n{traceback.format_exc()}")
            self.set_progress(0, "Error occurred (check log).")
            self.after(0, lambda err=str(e): messagebox.showerror("Pipeline Error", err))
        finally:
            self.after(0, lambda: self.btn_render.config(state=tk.NORMAL))


if __name__ == "__main__":
    multiprocessing.freeze_support()   # required for spawned render workers on Windows / frozen builds
    app = NalarkaStudioApp()
    app.mainloop()