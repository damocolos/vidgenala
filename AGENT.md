# AGENT.md — Nalarka Local Motion Graphics Studio (v8.0)

## 1. Project Objective & Core Philosophy
This repository hosts **Nalarka Motion Graphics Studio (`nalarka_local.py` / `main.py`)**, a deterministic local Python video rendering pipeline that replicates and extends the **`motion-graphics-by-nalarka`** explainer video skill without incurring cloud sandbox VM execution costs.

**Core Workflow:**
`Topic Input` $\rightarrow$ `Stage 1: AI Structured Script ([Hook]..[Penutup])` $\rightarrow$ `Stage 2: Voiceover Generation (ElevenLabs API / Web MP3 / Edge-TTS)` $\rightarrow$ `Stage 3: Faster-Whisper Word Alignment` $\rightarrow$ `Stage 4: FFprobe Duration Lock & Reference Library Style Extraction` $\rightarrow$ `Stage 5: Two-Pass AI Storyboard & Batched Pure-Python Vector Generation + Self-Correction Audit Loop` $\rightarrow$ `Stage 6: Deterministic Pillow-to-FFmpeg Rawvideo Pipe & Sidechain Audio Mixing` $\rightarrow$ `Stage 7: 7-Point Automated FFprobe/FFmpeg Verification Suite`.

---

## 2. Strict Technical & Audio-Visual Specifications

### 2.1 Video Duration Modes (Hard Locked to Audio)
Total video duration (`DUR`) is **never guessed from word count**. It is calculated deterministically from the actual voiceover file measured via `ffprobe`:
$$\text{DUR} = \lceil \text{VO\_DUR} + 2.5 \rceil \quad (\text{1.0s Intro} + \text{Voiceover} + \text{1.5s Outro})$$
$$\text{TOTAL\_FRAMES} = \text{DUR} \times 30 \quad (\text{Constant 30 FPS})$$

| Parameter | Mode 1: `1. Short (< 1 Minute | Max 60s)` | Mode 2: `2. Medium (1 - 2 Minutes | 61s - 120s)` |
| :--- | :--- | :--- |
| **Hard Ceiling** | $\le 60\text{ seconds}$ (`1,800 frames` max) | $61\text{ – }120\text{ seconds}$ (`3,600 frames` max) |
| **Script Word Count** | `70 – 125 words` ($\approx 28\text{s}–50\text{s}$ VO) | `165 – 255 words` ($\approx 64\text{s}–105\text{s}$ VO) |
| **Script Sections** | 5 sections: `[Hook]`, `[Inti 1..3]`, `[Penutup]` | 7 sections: `[Hook]`, `[Inti 1..5]`, `[Penutup]` |
| **Visual Beat Count** | `9 – 15 beats` ($\approx 2.6\text{s}$ avg per beat) | `18 – 30 beats` ($\approx 3.2\text{s}$ avg per beat) |
| **Codec & Format** | MP4 (`libx264`, `yuv420p`, `crf 18`, `30/1` CFR) + Audio (`aac`, `192k`, `48000 Hz`) | Same |

> **Hard Stop Rule:** Never cut, trim, or speed up the user's voiceover (`atempo` is forbidden). If `DUR` exceeds the active mode's ceiling (`60s` or `120s`), halt before rendering and prompt the user to switch modes or shorten the script.

---

## 3. Visual & Motion Design Rules (The "Nalarka Standard")

When modifying prompts or rendering logic in this repository, **strictly enforce** these visual rules derived from production benchmarks:

1. **NO Bottom Karaoke Subtitle Bar:**
   * Do **not** render a small word-by-word subtitle dock at the bottom of the screen.
   * The **main poster typography itself** is timed to the spoken words using staggered `delay` parameters (`0.0s`, `0.16s`, `0.32s`) synchronized with Faster-Whisper timestamps.
2. **NO Topic-Specific Hardcoded Presets or Fake Infographics:**
   * Never fall back to hardcoded topic illustrations from previous videos (e.g., no yawning faces, chili peppers, or car crashes on unrelated topics).
   * Never invent fake bar charts (`DEWASA/REMAJA`) or generic `A-B-C-D-E` network nodes unless the script explicitly discusses statistical data.
   * Every beat must draw a **concrete, recognizable physical object or mechanism** matching its `visual_blueprint`.
3. **Lineless Flat-Vector Aesthetic on Speckled Paper:**
   * Illustrations are built by layering colorful, lineless flat-vector shapes (`rect`, `circle`, `ellipse`, `poly`, `arc`, `bezier`, `wave_line`, `blob`) rather than thin black wireframe outlines.
   * Canvases use `get_speckled_paper_canvas()` with organic multi-colored paper speckles.
4. **Dynamic Layout & Full-Bleed Color Rotation:**
   * Rotate across the 4 core layouts so consecutive beats never look repetitive:
     * `"triptych_dots"`: Top 3-dot sequence indicator (`● ● ●` via `dot_index: 1|2|3`) + upper-middle illustration (`stage_cy = 43%`) + 2 giant lines of text at bottom (`y = 73%`). Ideal for 3-part hooks or symptom lists.
     * `"top_text_bottom_art"`: 2–3 staggered giant lines at top (`y = 14%`) + illustration stage in lower half (`stage_cy = 60%`).
     * `"top_art_bottom_text"`: Upper-middle illustration (`stage_cy = 43%`) + 2 giant lines at bottom (`y = 73%`).
     * `"statement_pill"`: Full-screen 3-word bold statement (Top giant word + Tilted center pill + Bottom giant word) + accent illustration at `stage_cy = 77%`.
   * Rotate `bg_color` across `"cream"` (`#F2EADA`), `"orange_bg"` (`#EE8631`), and `"green_bg"` (`#38844C`). Use `"orange_bg"` or `"green_bg"` on climax questions and bold contrast statements.

---

## 4. System Architecture & Key Modules

### 4.1 Multi-Provider AI Switcher (`Section 1`)
* Supports instant switching between **`OpenRouter (openrouter.ai)`** and **`OAO.ai.id / Custom OpenAI`**.
* Stores separate credentials and model selections under `provider_profiles` in `config_nalarka.json`.
* **Streaming & Anti-Timeout Protection (`call_ai_stream_raw`):**
  * Always uses `stream=True` with live character-count logging to prevent Cloudflare `HTTP 524` (120-second proxy read timeouts).
  * Automatically extracts `delta.content` or `delta.reasoning_content` and includes a non-streaming fallback if a proxy strips stream chunks.
* **Recommended Models:**
  * *OpenRouter Vector Artist (Stage 5)*: `anthropic/claude-sonnet-4`, `anthropic/claude-3.7-sonnet`, `deepseek/deepseek-chat-v3-0324`.
  * *OAO.ai.id Vector Artist (Stage 5)*: `deepseek-v4-pro`, `kimi-k2.7-code`, `claude-fable-5`.
  * *Script Generation (Stage 1)*: `deepseek/deepseek-chat-v3-0324` or `deepseek-v4-flash`.

### 4.2 Structured Script & ElevenLabs TTS Sanitizer (`Section 2A & 2B`)
* Scripts in the GUI editor use bracketed section tags separated by double newlines:
  ```text
  [Hook] ...

  [Inti 1] ...

  [Penutup] ...
  ```
* **`prepare_text_for_elevenlabs()`**: Automatically strips all `[...]` tags via regex before sending the payload to ElevenLabs (`POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`) or `edge-tts`, preserving `\n\n` paragraph breaks for natural breathing cadence.
* **Free-Tier Compatibility**: Includes ElevenLabs Premade Voice IDs (`Brian`, `Daniel`, `George`, `Bill`, `Adam`) which work on the Free API tier without triggering `HTTP 402 paid_plan_required`, plus a **`Copy Clean Text`** button for generating Community Library voices (like *Bagas*) on the ElevenLabs web UI.

### 4.3 Single-Folder Reference Library (`Section 3`)
* Scans `./references_library/` (or a custom user-selected directory) for `.mp4`, `.mov`, `.png`, `.jpg`, and `.webp` files.
* Users select references directly from a single dropdown (`Reference Library`) without browsing the filesystem repeatedly.

### 4.4 Two-Pass Storyboard & Batched Vector Code Engine (`Stage 5`)
To prevent token truncation and timeouts (especially on 20–30 beat Medium videos), Stage 5 is split into two passes:
1. **Pass 1 (`PASS1_STORYBOARD_PROMPT` $\rightarrow$ JSON):**
   Plans all visual beats (`start`, `end`, `bg_color`, `layout`, `dot_index`, `lines`, and a concrete `visual_blueprint` describing the physical objects and motion).
2. **Pass 2 (`PASS2_BATCH_VECTOR_PROMPT` $\rightarrow$ Batched Pure Python Code):**
   Generates pure Python functions `def beat_01(v):` through `def beat_N(v):` in **batches of 4 beats per API call**, saving the combined module to `generated_scenes.py`.

### 4.5 `VectorKit` (`v`) Coordinate System & Primitives
Inside `generated_scenes.py`, each `def beat_XX(v):` receives a `VectorKit` instance `v`:
* **Origin `(0, 0)`**: Automatically centered at the beat's illustration stage (`v.stage_cx, v.stage_cy`).
* **Units**: Virtual 1080p pixels relative to `(0, 0)` (safe bounds: `x` in `-360 .. +360`, `y` in `-260 .. +260`), scaled automatically by `v.S` for Portrait (`1080x1920`) or Landscape (`1920x1080`).
* **Core Primitives**:
  * Stage & Shapes: `v.backdrop_circle()`, `v.circle()`, `v.ellipse()`, `v.rect()`, `v.poly()`, `v.blob()`, `v.starburst()`, `v.droplet()`, `v.ring_gauge()`, `v.label_pill()`.
  * Lines & Waves: `v.line()`, `v.arc()`, `v.bezier()`, `v.arrow()`, `v.wave_line()`, `v.concentric_waves()`, `v.waveform_bars()`.
  * Motion Helpers: `v.lt` (elapsed beat time), `v.p` (`0.0..1.0` beat progress), `v.ease(delay, dur)`, `v.spring(delay, dur)`, `v.elastic(delay, dur)`, `v.wave(freq, amp, phase)`, `v.pulse(freq, lo, hi)`.

### 4.6 Self-Correction Audit Loop (`audit_and_fix_beats`)
Before rendering the full MP4, every beat function in `generated_scenes.py` is rendered at 3 sample timestamps (`early`, `mid`, `late`) and audited against 4 criteria:
1. **Zero Runtime Errors**: Catches any Python syntax/name/math exceptions.
2. **Shape Complexity (`v.shape_count >= 5`)**: Rejects overly simplistic 1–2 shape placeholders.
3. **Motion Liveliness (`motion_score >= 0.20`)**: Measures pixel delta across `early -> mid -> late` frames to guarantee active animation.
4. **Anti-Repetition (`unique_diff >= 6.0`)**: Compares the stage crop against the previous beat; if two consecutive beats look too similar, `rewrite_single_beat_function_with_ai()` automatically rewrites the beat.
* Users can also manually re-roll any individual beat via the GUI **Beat Inspector / Re-Roll** bar.

### 4.7 Deterministic FFmpeg Pipe & 7-Point Verification Suite (`Stages 6 & 7`)
* **Render Pipe (`render_video_pipeline`)**: Streams raw RGB24 bytes frame-by-frame from Pillow directly to `ffmpeg` stdin while mixing voiceover (`adelay=1000:all=1`) and background music (user BGM or `generate_synthetic_ambient_bgm()` 48kHz stereo WAV) using `sidechaincompress` audio ducking and `alimiter`.
* **7-Point Verification (`run_verification_suite`)**:
  1. Codec & Pixel Format (`h264` + `aac`, `yuv420p`).
  2. Resolution & Aspect Ratio match (`1080x1920` or `1920x1080`).
  3. Constant Frame Rate (`r_frame_rate == 30/1` and `avg_frame_rate == 30/1`).
  4. Exact Container Duration ($\le 60\text{s}$ or $\le 120\text{s}$ mode ceiling, within $\pm 0.15\text{s}$ of locked target).
  5. Exact Decoded Frame Count (`nb_read_frames == DUR * 30`).
  6. Zero Black Frames (`blackdetect`) & non-empty `check/last_frame.png`.
  7. Audio Presence & Anti-Clipping (`volumedetect` mean $> -45\text{ dB}$, max $\le 0.0\text{ dB}$) + generates `check/verification_contact_sheet.jpg`.

---

## 5. Key Files in Workspace
* `nalarka_local.py` / `main.py` — Main GUI application and 7-stage rendering orchestrator.
* `config_nalarka.json` — Persistent multi-provider profiles, API keys, ElevenLabs settings, and reference library path.
* `references_library/` — Single-folder directory for reference `.mp4`/`.png`/`.jpg` files.
* `timeline_plan.json` — Pass 1 AI Storyboard & Visual Blueprint output.
* `generated_scenes.py` — Pass 2 AI-generated pure Python flat-vector beat functions (`beat_01`..`beat_N`).
* `check/verification_contact_sheet.jpg` & `check/last_frame.png` — Visual QA artifacts generated during Stage 7 verification.
* `nalarka_debug.log` & `ffmpeg_render.log` — Detailed diagnostic logs.

---

## 6. Guidelines for Future AI Agents Modifying This Codebase
1. **Never break the deterministic frame count**: `total_frames` passed to `-frames:v` and `-t` in FFmpeg must always equal `math.ceil(vo_dur + 2.5) * 30`.
2. **Keep Pass 2 batched**: Always generate Python vector functions in small batches (4–5 beats per request) with `stream=True` so 2-minute videos (up to 30 beats) never hit token limits or Cloudflare timeouts.
3. **Preserve `VectorKit` backward compatibility**: When adding new drawing primitives to `VectorKit`, use stage-relative coordinates (`self.sx(x)`, `self.sy(y)`, `self.ss(s)`) and increment `self.shape_count` so the self-correction audit loop tracks complexity accurately.