import os
import re
import sys
import json
import math
import time
import wave
import queue
import shutil
import asyncio
import logging
import threading
import subprocess
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from openai import OpenAI

# ==========================================
# CONSTANTS & DASHBOARD MODEL PRESETS
# ==========================================
CONFIG_FILE = "config_nalarka.json"
LOG_FILE = "nalarka_debug.log"
FFMPEG_LOG_FILE = "ffmpeg_render.log"
DEFAULT_REF_LIBRARY_DIR = "references_library"
FPS = 30
MAX_DURATION = 60

OAO_VISUAL_CODER_MODELS = [
    "claude-fable-5",
    "claude-opus-5.5",
    "deepseek-v4-pro",
    "kimi-k2.7-code",
    "gpt-6-sol",
    "gpt-6-astra",
    "glm-5.3",
    "grok-4.7",
    "qwen3.8-27b",
    "gemini-3.8-flash",
]

OAO_SCRIPT_MODELS = [
    "claude-fable-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "gemini-3.8-flash",
    "gpt-6-luna",
    "claude-haiku-5.5",
    "glm-5.2",
]

ELEVENLABS_VOICE_PRESETS = [
    "Brian - Deep Documentary [Free API] (nPczCjzI2devNBz1zQrb)",
    "Daniel - Steady Explainer [Free API] (onwK4e9ZLuTAKqWW03F9)",
    "George - Warm Storyteller [Free API] (JBFqnCBsd6RMkjVDRZzb)",
    "Bill - Smooth Narrator [Free API] (pqHfZKP75CvOlQylNhV4)",
    "Adam - Punchy Shorts [Free API] (pNInz6obpgDQGcFmaJgB)",
]

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)


# ==========================================
# HELPER: COLOUR & EASING MATHEMATICS
# ==========================================
def hex_to_rgb(hex_str: str, default: Tuple[int, int, int] = (242, 234, 218)) -> Tuple[int, int, int]:
    if not isinstance(hex_str, str):
        return default
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join([c * 2 for c in h])
    if len(h) != 6:
        return default
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return default


def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(
        max(0, min(255, int(rgb[0]))),
        max(0, min(255, int(rgb[1]))),
        max(0, min(255, int(rgb[2])))
    )


def clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(val)))


def ease_out_cubic(x: float) -> float:
    x = clamp(x)
    return 1.0 - pow(1.0 - x, 3)


def ease_in_out_cubic(x: float) -> float:
    x = clamp(x)
    return 4.0 * x * x * x if x < 0.5 else 1.0 - pow(-2.0 * x + 2.0, 3) / 2.0


def ease_out_back(x: float, overshoot: float = 1.65) -> float:
    x = clamp(x)
    c3 = overshoot + 1.0
    return 1.0 + c3 * pow(x - 1.0, 3) + overshoot * pow(x - 1.0, 2)


def ease_out_elastic(x: float) -> float:
    x = clamp(x)
    if x == 0.0 or x == 1.0:
        return x
    c4 = (2.0 * math.pi) / 3.0
    return pow(2.0, -10.0 * x) * math.sin((x * 10.0 - 0.75) * c4) + 1.0


def rotate_point(px: float, py: float, cx: float, cy: float, angle_deg: float) -> Tuple[int, int]:
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    nx = cos_a * (px - cx) - sin_a * (py - cy) + cx
    ny = sin_a * (px - cx) + cos_a * (py - cy) + cy
    return int(nx), int(ny)


def sample_bezier_curve(control_pts: List[Tuple[float, float]], steps: int = 28) -> List[Tuple[int, int]]:
    """Evaluates a De Casteljau Bezier curve of any degree (quadratic, cubic, or higher)."""
    if len(control_pts) < 2:
        return [(int(p[0]), int(p[1])) for p in control_pts]
    out = []
    n = len(control_pts) - 1
    for i in range(steps + 1):
        u = i / float(steps)
        pts = [(float(p[0]), float(p[1])) for p in control_pts]
        for r in range(1, n + 1):
            pts = [
                ((1.0 - u) * pts[k][0] + u * pts[k + 1][0], (1.0 - u) * pts[k][1] + u * pts[k + 1][1])
                for k in range(n - r + 1)
            ]
        out.append((int(pts[0][0]), int(pts[0][1])))
    return out


# ==========================================
# HELPER: STRUCTURED SCRIPT PARSER & CLEANER
# ==========================================
def format_sections_to_tagged_text(script_data: dict) -> str:
    blocks = []
    hook = str(script_data.get("hook") or "").strip()
    if hook:
        blocks.append(f"[Hook] {hook}")

    for i in range(1, 6):
        val = str(script_data.get(f"inti_{i}") or script_data.get(f"point_{i}") or "").strip()
        if val:
            blocks.append(f"[Inti {i}] {val}")

    penutup = str(script_data.get("penutup") or script_data.get("outro") or "").strip()
    if penutup:
        blocks.append(f"[Penutup] {penutup}")

    if not blocks and script_data.get("full_text"):
        return str(script_data["full_text"]).strip()

    return "\n\n".join(blocks)


def parse_tagged_script(raw_text: str) -> Tuple[str, List[Dict[str, str]]]:
    sections: List[Dict[str, str]] = []
    clean_paragraphs: List[str] = []

    pattern = re.compile(r"^\s*\[([^\]]+)\]\s*(.+)$", flags=re.DOTALL)
    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n|\n(?=\s*\[)", raw_text.strip()) if b.strip()]

    for block in raw_blocks:
        m = pattern.match(block)
        if m:
            tag_name = m.group(1).strip()
            body = re.sub(r"\[[^\]]+\]", "", m.group(2)).strip()
        else:
            tag_name = "Narasi"
            body = re.sub(r"\[[^\]]+\]", "", block).strip()

        body_single_line = re.sub(r"\s+", " ", body).strip()
        if body_single_line:
            sections.append({"tag": tag_name, "text": body_single_line})
            clean_paragraphs.append(body_single_line)

    clean_tts_text = "\n\n".join(clean_paragraphs)
    return clean_tts_text, sections


def prepare_text_for_elevenlabs(raw_editor_text: str, add_ssml_breaks: bool = False) -> str:
    clean_tts_text, _ = parse_tagged_script(raw_editor_text)
    if add_ssml_breaks:
        paragraphs = [p.strip() for p in clean_tts_text.split("\n\n") if p.strip()]
        return ' <break time="0.4s" />\n\n'.join(paragraphs)
    return clean_tts_text


# ==========================================
# HELPER: SAFE JSON & PYTHON CODE EXTRACTORS
# ==========================================
def extract_json_safely(raw_text: str, log_fn) -> dict:
    if not raw_text or not raw_text.strip():
        raise ValueError("AI returned an empty response. Check your Base URL, API Key, and Model name.")

    cleaned = raw_text.strip()
    cleaned = re.sub(r"<think>.*?</think>", "", cleaned, flags=re.DOTALL).strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start_idx = cleaned.find("{")
    end_idx = cleaned.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = cleaned[start_idx : end_idx + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as err:
            log_fn(f"[JSON ERROR] Failed to parse extracted JSON:\n{candidate[:800]}")
            raise ValueError(f"Malformed JSON from AI: {err}") from err

    log_fn(f"[RAW AI ERROR] Response is not valid JSON:\n{raw_text[:800]}")
    raise ValueError("No valid JSON object '{...}' found in AI response.")


def extract_python_code_safely(raw_text: str) -> str:
    if not raw_text or not raw_text.strip():
        raise ValueError("AI returned an empty code response.")
    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()
    # Look for ```python ... ``` block first
    m = re.search(r"```(?:python)?\s*\n(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    cleaned = re.sub(r"^```(?:python)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def call_ai_json(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_fn,
    temperature: float = 0.40
) -> dict:
    log_fn(f"[AI JSON] Calling endpoint (streaming): {base_url} | Model: {model}")
    client = OpenAI(base_url=base_url.strip(), api_key=api_key.strip(), timeout=300.0, max_retries=1)
    t0 = time.time()

    chunks = []
    try:
        stream = client.chat.completions.create(
            model=model.strip(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            if getattr(chunk, "choices", None):
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    chunks.append(content)
    except Exception as stream_err:
        log_fn(f"[AI JSON WARN] Streaming interrupted from '{model}' ({stream_err}). Attempting non-streaming fallback...")

    raw_content = "".join(chunks).strip()

    # Fallback to non-streaming if stream failed or returned empty/insufficient content
    if not raw_content or len(raw_content) < 30:
        log_fn(f"[AI JSON WARN] Response empty/truncated. Retrying '{model}' with stream=False...")
        resp_retry = client.chat.completions.create(
            model=model.strip(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            stream=False,
        )
        raw_content = getattr(resp_retry.choices[0].message, "content", "") or ""

    elapsed = round(time.time() - t0, 2)
    log_fn(f"[AI JSON] Content received from '{model}' in {elapsed}s ({len(raw_content)} chars).")
    logging.debug(f"RAW AI JSON ({model}):\n{raw_content}")
    return extract_json_safely(raw_content, log_fn)


def call_ai_python_code(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_fn,
    temperature: float = 0.35
) -> str:
    log_fn(f"[AI CODE] Streaming Pure Python Flat-Vector Code via '{model}'...")
    client = OpenAI(base_url=base_url.strip(), api_key=api_key.strip(), timeout=360.0, max_retries=1)
    t0 = time.time()

    chunks = []
    try:
        stream = client.chat.completions.create(
            model=model.strip(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            if getattr(chunk, "choices", None):
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    chunks.append(content)
    except Exception as stream_err:
        log_fn(f"[AI CODE WARN] Streaming interrupted from '{model}' ({stream_err}). Attempting non-streaming fallback...")

    raw_content = "".join(chunks).strip()
    
    # Fallback jika model masih memulangkan respons kosong
    if not raw_content or len(raw_content) < 50:
        log_fn(f"[AI CODE WARN] Model '{model}' returned empty/too short response. Retrying with stream=False...")
        resp_retry = client.chat.completions.create(
            model=model.strip(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            stream=False,
        )
        raw_content = getattr(resp_retry.choices[0].message, "content", "") or ""

    elapsed = round(time.time() - t0, 2)
    log_fn(f"[AI CODE] Pure Python Vector Module received in {elapsed}s ({len(raw_content)} chars).")
    return extract_python_code_safely(raw_content)

# ==========================================
# STAGE 1: GENERATE STRUCTURED VOICEOVER SCRIPT
# ==========================================
def generate_script(base_url: str, api_key: str, model: str, topic: str, script_lang: str, log_fn) -> dict:
    lang_instruction = (
        "Tulis naskah voiceover dalam Bahasa Indonesia lisan yang natural, mengalir, dan kaya gambaran objek visual nyata."
        if "Indonesian" in script_lang
        else "Write the voiceover script in natural, vivid spoken English."
    )
    system_prompt = (
        "Kamu adalah penulis naskah motion graphics explainer senior bergaya Nalarka.\n"
        f"{lang_instruction}\n"
        "Aturan ketat:\n"
        "1. Total panjang gabungan seluruh bagian WAJIB antara 70 hingga 125 kata (~2.5 kata/detik, maksimal total video <= 60 detik).\n"
        "2. Bagi naskah menjadi 5 bagian yang jelas:\n"
        "   - `hook`: Kalimat pembuka 3 detik pertama yang menyebutkan 2-3 reaksi/kejadian nyata lalu ditutup pertanyaan memikat.\n"
        "   - `inti_1`: Fakta utama atau mekanisme fisik/biologis/psikologis pertama secara konkret.\n"
        "   - `inti_2`: Mekanisme kedua atau perbandingan dua jalur/faktor secara visual.\n"
        "   - `inti_3`: Fakta penelitian atau paradoks menarik.\n"
        "   - `penutup`: Kesimpulan yang membalik cara pandang penonton dan kalimat penutup yang menancap di ingatan.\n"
        "3. Kembalikan HANYA JSON valid dengan struktur persis ini:\n"
        '{\n'
        '  "hook": "...",\n'
        '  "inti_1": "...",\n'
        '  "inti_2": "...",\n'
        '  "inti_3": "...",\n'
        '  "penutup": "..."\n'
        '}'
    )
    data = call_ai_json(base_url, api_key, model, system_prompt, f"Topik: {topic}", log_fn)

    structured = {
        "hook": str(data.get("hook") or "").strip(),
        "inti_1": str(data.get("inti_1") or data.get("point_1") or "").strip(),
        "inti_2": str(data.get("inti_2") or data.get("point_2") or "").strip(),
        "inti_3": str(data.get("inti_3") or data.get("point_3") or "").strip(),
        "penutup": str(data.get("penutup") or data.get("outro") or "").strip(),
    }
    structured["tagged_text"] = format_sections_to_tagged_text(structured)
    clean_text, sections = parse_tagged_script(structured["tagged_text"])
    structured["clean_tts_text"] = clean_text
    structured["sections"] = sections
    return structured


# ==========================================
# STAGE 2: ELEVENLABS API v1 & EDGE-TTS INTEGRATION
# ==========================================
def fetch_elevenlabs_voices(xi_api_key: str, log_fn) -> List[Dict[str, str]]:
    if not xi_api_key or not xi_api_key.strip():
        raise ValueError("ElevenLabs API Key is empty.")

    url = "https://api.elevenlabs.io/v1/voices"
    req = urllib.request.Request(
        url,
        headers={"xi-api-key": xi_api_key.strip(), "Accept": "application/json"},
        method="GET"
    )
    log_fn("[ELEVENLABS] Fetching voice list from GET /v1/voices ...")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        voices = data.get("voices", [])
        return [
            {
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", "Unknown"),
                "category": v.get("category", "premade")
            }
            for v in voices
        ]


def generate_elevenlabs_tts(
    raw_editor_text: str,
    output_path: str,
    xi_api_key: str,
    voice_id: str,
    model_id: str,
    stability: float,
    similarity_boost: float,
    style: float,
    use_speaker_boost: bool,
    add_ssml_breaks: bool,
    log_fn
):
    if not xi_api_key or not xi_api_key.strip():
        raise ValueError("ElevenLabs API Key is missing! Please enter your xi-api-key in Section 2B.")

    clean_voice_id = voice_id.strip()
    if "(" in clean_voice_id and clean_voice_id.endswith(")"):
        clean_voice_id = clean_voice_id.split("(")[-1].rstrip(")").strip()

    cleaned_text = prepare_text_for_elevenlabs(raw_editor_text, add_ssml_breaks=add_ssml_breaks)
    if not cleaned_text:
        raise ValueError("Script text is empty after removing section tags.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{clean_voice_id}?output_format=mp3_44100_128"
    payload = {
        "text": cleaned_text,
        "model_id": model_id.strip(),
        "voice_settings": {
            "stability": round(float(stability), 2),
            "similarity_boost": round(float(similarity_boost), 2),
            "style": round(float(style), 2),
            "use_speaker_boost": bool(use_speaker_boost)
        }
    }
    if any(k in model_id.lower() for k in ("flash", "turbo")):
        payload["language_code"] = "id"

    headers = {
        "xi-api-key": xi_api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }

    req_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=req_bytes, headers=headers, method="POST")

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio_bytes = resp.read()
            with open(output_path, "wb") as f:
                f.write(audio_bytes)
        log_fn(f"[ELEVENLABS SUCCESS] Saved {len(audio_bytes)} bytes to '{output_path}' in {round(time.time()-t0, 2)}s.")
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8", errors="replace")
        log_fn(f"[ELEVENLABS HTTP ERROR {err.code}] {err_body}")
        raise RuntimeError(f"ElevenLabs TTS failed (HTTP {err.code}): {err_body}") from err


async def _edge_tts_save(text: str, output_path: str, voice: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate="+2%", pitch="-3Hz")
    await communicate.save(output_path)


def generate_edge_tts_fallback(raw_editor_text: str, output_path: str, voice: str, log_fn):
    cleaned_text = prepare_text_for_elevenlabs(raw_editor_text, add_ssml_breaks=False)
    log_fn(f"[EDGE-TTS] Synthesizing cleaned text ({len(cleaned_text)} chars) with voice '{voice}'...")
    asyncio.run(_edge_tts_save(cleaned_text, output_path, voice))
    log_fn(f"[EDGE-TTS] Saved voiceover to: {output_path}")


def generate_synthetic_ambient_bgm(output_wav: str, duration_sec: int, bpm: int, log_fn) -> str:
    sr = 48000
    total_samples = sr * (duration_sec + 2)
    t = np.linspace(0, duration_sec + 2, total_samples, endpoint=False)
    freqs = [110.0, 164.81, 220.0, 261.63, 329.63, 392.00]
    amps = [0.22, 0.16, 0.18, 0.14, 0.10, 0.08]
    signal_l = np.zeros_like(t)
    signal_r = np.zeros_like(t)

    for idx, (f, a) in enumerate(zip(freqs, amps)):
        mod = 1.0 + 0.15 * np.sin(2.0 * np.pi * (0.12 + idx * 0.04) * t)
        detune = 1.002 if idx % 2 == 0 else 0.998
        signal_l += a * mod * np.sin(2.0 * np.pi * f * t)
        signal_r += a * mod * np.sin(2.0 * np.pi * (f * detune) * t + 0.4)

    beat_freq = bpm / 60.0
    beat_env = np.power(0.5 * (1.0 + np.sin(2.0 * np.pi * beat_freq * t - np.pi / 2.0)), 4.0)
    pulse = 0.15 * beat_env * np.sin(2.0 * np.pi * 82.41 * t)
    signal_l += pulse
    signal_r += pulse

    peak = max(np.max(np.abs(signal_l)), np.max(np.abs(signal_r)), 1e-6)
    stereo = np.column_stack(((signal_l / peak) * 0.45, (signal_r / peak) * 0.45))
    pcm16 = np.int16(np.clip(stereo * 32767, -32768, 32767))

    with wave.open(output_wav, "w") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return output_wav


# ==========================================
# STAGE 3 & 4: AUDIO ANALYSIS & SINGLE-FOLDER REFERENCE LIBRARY
# ==========================================
def get_audio_duration(audio_path: str, log_fn) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path]
    dur = float(subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip())
    log_fn(f"[FFPROBE] Exact voiceover duration: {dur:.3f}s")
    return dur


def extract_word_timestamps(audio_path: str, whisper_size: str, log_fn) -> List[dict]:
    from faster_whisper import WhisperModel
    log_fn(f"[WHISPER] Loading Faster-Whisper model ('{whisper_size}', int8 CPU)...")
    t0 = time.time()
    whisper_model = WhisperModel(whisper_size, device="cpu", compute_type="int8")
    segments, _ = whisper_model.transcribe(audio_path, word_timestamps=True)

    words = []
    for seg in segments:
        if seg.words:
            for w in seg.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start + 1.0, 3),
                    "end": round(w.end + 1.0, 3)
                })
    log_fn(f"[WHISPER] Extracted {len(words)} word-level timestamps in {round(time.time() - t0, 2)}s.")
    return words


def scan_reference_library(library_dir: str) -> List[str]:
    lib_path = Path(library_dir)
    lib_path.mkdir(parents=True, exist_ok=True)
    valid_exts = (".mp4", ".mov", ".mkv", ".webm", ".png", ".jpg", ".jpeg", ".webp")
    return sorted([
        f.name for f in lib_path.iterdir()
        if f.is_file() and f.suffix.lower() in valid_exts and not f.name.startswith(".")
    ])


def analyze_reference_video(ref_file_path: Optional[str], log_fn) -> dict:
    default_palette = {
        "cream": "#F2EADA",
        "orange_bg": "#EE8631",
        "green_bg": "#38844C",
        "blue_bg": "#2680C2",
        "dark": "#181818",
        "red_hot": "#EC4C28",
        "yellow_stage": "#F6C23E",
        "blue_soft": "#B3D9EE",
        "blue_tear": "#2680C2",
        "mint_soft": "#B5DEC8",
        "pink_organ": "#F38375",
        "pink_soft": "#F5A3A2",
        "purple_soft": "#C8B6E2",
        "skin": "#F7C6A3",
        "skin_dark": "#E0A37B",
        "white": "#FFFFFF",
        "grey": "#7F7B74",
        "stone": "#6E6A63",
        "stone_dark": "#4A4742"
    }
    default_profile = {
        "has_reference": False,
        "ref_name": "Default Nalarka Flat-Vector Palette",
        "width": 1080,
        "height": 1920,
        "orientation": "portrait",
        "avg_cut_seconds": 2.4,
        "recommended_bpm": 116,
        "palette": default_palette
    }

    if not ref_file_path or not os.path.exists(ref_file_path):
        return default_profile

    ref_path_obj = Path(ref_file_path)
    try:
        ext = ref_path_obj.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            im = Image.open(ref_file_path).convert("RGB")
            ref_w, ref_h = im.size
        else:
            probe_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "json", ref_file_path
            ]
            probe_json = json.loads(subprocess.check_output(probe_cmd, text=True))
            stream = probe_json.get("streams", [{}])[0]
            ref_w = int(stream.get("width", 1080))
            ref_h = int(stream.get("height", 1920))

        orientation = "portrait" if ref_h >= ref_w else "landscape"
        return {
            "has_reference": True,
            "ref_name": ref_path_obj.name,
            "width": 1080 if orientation == "portrait" else 1920,
            "height": 1920 if orientation == "portrait" else 1080,
            "orientation": orientation,
            "avg_cut_seconds": 2.4,
            "recommended_bpm": 116,
            "palette": default_palette
        }
    except Exception:
        return default_profile


# ==========================================
# STAGE 5 & 6: UNIVERSAL FLAT-VECTOR ENGINE (`VectorKit` / `v`)
# ==========================================
_PAPER_CACHE: Dict[Tuple[int, int, str], Image.Image] = {}


def get_speckled_paper_canvas(width: int, height: int, bg_hex: str) -> Image.Image:
    key = (width, height, bg_hex)
    if key in _PAPER_CACHE:
        return _PAPER_CACHE[key].copy()

    bg_rgb = hex_to_rgb(bg_hex, (242, 234, 218))
    base = Image.new("RGB", (width, height), bg_rgb)
    draw = ImageDraw.Draw(base)

    rng = np.random.default_rng(seed=77)
    num_specks = int((width * height) / 9500)
    xs = rng.integers(0, width, size=num_specks)
    ys = rng.integers(0, height, size=num_specks)
    radii = rng.choice([2, 2, 3, 3, 4], size=num_specks)

    speck_colors = [
        (215, 130, 75),
        (95, 145, 110),
        (165, 155, 140),
        (190, 95, 80),
    ]
    for i, (x, y, r) in enumerate(zip(xs, ys, radii)):
        sc = speck_colors[i % len(speck_colors)]
        blended = (
            int(bg_rgb[0] * 0.45 + sc[0] * 0.55),
            int(bg_rgb[1] * 0.45 + sc[1] * 0.55),
            int(bg_rgb[2] * 0.45 + sc[2] * 0.55),
        )
        draw.ellipse([int(x - r), int(y - r), int(x + r), int(y + r)], fill=blended)

    _PAPER_CACHE[key] = base
    return base.copy()


def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/Library/Fonts/Arial Black.ttf",
        "C:/Windows/Fonts/ariblk.ttf" if bold else "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/impact.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, max(10, int(size)))
            except Exception:
                continue
    return ImageFont.load_default()


class VectorKit:
    """
    Universal Flat-Vector Drawing Engine (`v`) for ANY topic.
    - ZERO hardcoded topic presets (no yawning face or chili fallback!).
    - Uses a super intuitive Local Stage Coordinate System where `(0, 0)` is the center of the
      illustration stage (`self.stage_cx, self.stage_cy`), and coordinates are in virtual pixels
      (`-450 .. +450`), automatically scaled by `self.S`.
    - Typography & Layout headers (`three_dots`, `poster_lines`, `statement_pill_poster`) are
      rendered automatically from the Beat Storyboard JSON so the Python code focuses 100% on
      building rich custom vector art!
    """
    def __init__(self, width: int, height: int, t: float, beat: dict, words: List[dict], palette: dict):
        self.W = width
        self.H = height
        self.S = min(width, height) / 1080.0
        self.t = float(t)
        self.start = float(beat.get("start", 0.0))
        self.end = float(beat.get("end", 2.5))
        self.dur = max(0.25, self.end - self.start)
        self.lt = max(0.0, self.t - self.start)
        self.p = clamp(self.lt / self.dur, 0.0, 1.0)
        self.beat = beat
        self.palette = palette
        self.shape_count = 0

        bg_key = str(beat.get("bg_color", "cream")).lower().strip()
        bg_hex = self._resolve_hex(bg_key)
        self.img = get_speckled_paper_canvas(width, height, bg_hex)
        self.draw = ImageDraw.Draw(self.img, "RGBA")

        # Determine Stage Center `(stage_cx, stage_cy)` based on beat layout
        layout = str(beat.get("layout", "top_text_bottom_art")).lower()
        self.stage_cx = width // 2
        if layout in ("triptych_dots", "top_art_bottom_text"):
            self.stage_cy = int(height * 0.43)
        elif layout == "statement_pill":
            self.stage_cy = int(height * 0.77)
        else:
            # Default: Top title + center/lower illustration stage
            self.stage_cy = int(height * 0.60)

    def _resolve_hex(self, key_or_hex: str) -> str:
        mapping = {
            "cream": self.palette.get("cream", "#F2EADA"),
            "bg": self.palette.get("cream", "#F2EADA"),
            "orange_bg": self.palette.get("orange_bg", "#EE8631"),
            "orange": self.palette.get("orange_bg", "#EE8631"),
            "green_bg": self.palette.get("green_bg", "#38844C"),
            "green": self.palette.get("green_bg", "#38844C"),
            "blue_bg": self.palette.get("blue_bg", "#2680C2"),
            "dark": self.palette.get("dark", "#181818"),
            "black": "#181818",
            "red": self.palette.get("red_hot", "#EC4C28"),
            "red_hot": self.palette.get("red_hot", "#EC4C28"),
            "yellow": self.palette.get("yellow_stage", "#F6C23E"),
            "yellow_stage": self.palette.get("yellow_stage", "#F6C23E"),
            "blue_soft": self.palette.get("blue_soft", "#B3D9EE"),
            "blue": self.palette.get("blue_tear", "#2680C2"),
            "blue_tear": self.palette.get("blue_tear", "#2680C2"),
            "mint": self.palette.get("mint_soft", "#B5DEC8"),
            "mint_soft": self.palette.get("mint_soft", "#B5DEC8"),
            "pink": self.palette.get("pink_organ", "#F38375"),
            "pink_organ": self.palette.get("pink_organ", "#F38375"),
            "pink_soft": self.palette.get("pink_soft", "#F5A3A2"),
            "purple": self.palette.get("purple_soft", "#C8B6E2"),
            "skin": self.palette.get("skin", "#F7C6A3"),
            "skin_dark": self.palette.get("skin_dark", "#E0A37B"),
            "white": "#FFFFFF",
            "grey": "#7F7B74",
            "stone": "#6E6A63",
            "stone_dark": "#4A4742",
            "brown": "#6E3B1E",
        }
        return mapping.get(str(key_or_hex).lower().strip(), str(key_or_hex))

    def col(self, key_or_hex: str, alpha: int = 255) -> Tuple[int, int, int, int]:
        rgb = hex_to_rgb(self._resolve_hex(key_or_hex), (24, 24, 24))
        return (rgb[0], rgb[1], rgb[2], int(clamp(alpha, 0, 255)))

    # --- Stage Coordinate Converters (`(0,0)` = Stage Center, units in virtual 1080p pixels) ---
    def sx(self, x: float) -> int:
        return int(self.stage_cx + x * self.S)

    def sy(self, y: float) -> int:
        return int(self.stage_cy + y * self.S)

    def ss(self, s: float) -> int:
        return max(1, int(s * self.S))

    # --- Physics & Animation Timing Helpers ---
    def ease(self, delay: float = 0.0, dur: float = 0.45) -> float:
        return ease_out_cubic((self.lt - delay) / max(0.05, dur))

    def spring(self, delay: float = 0.0, dur: float = 0.48, overshoot: float = 1.65) -> float:
        return ease_out_back((self.lt - delay) / max(0.05, dur), overshoot=overshoot)

    def elastic(self, delay: float = 0.0, dur: float = 0.60) -> float:
        return ease_out_elastic((self.lt - delay) / max(0.05, dur))

    def wave(self, freq: float = 2.0, amp: float = 15.0, phase: float = 0.0) -> float:
        return math.sin(self.lt * freq * 2.0 * math.pi + phase) * amp

    def pulse(self, freq: float = 2.5, lo: float = 0.94, hi: float = 1.06, phase: float = 0.0) -> float:
        mid = (lo + hi) * 0.5
        amp = (hi - lo) * 0.5
        return mid + math.sin(self.lt * freq * 2.0 * math.pi + phase) * amp

    def _fit_font(self, text: str, target_px: int, max_w_ratio: float = 0.88) -> ImageFont.FreeTypeFont:
        sz = int(target_px * self.S)
        max_w = int(self.W * max_w_ratio)
        while sz > 18:
            f = load_font(sz, bold=True)
            bb = self.draw.textbbox((0, 0), text, font=f)
            if (bb[2] - bb[0]) <= max_w:
                return f
            sz -= 5
        return load_font(18, bold=True)

    # ==========================================
    # 1. UNIVERSAL FLAT-VECTOR SHAPE PRIMITIVES
    # ==========================================
    def backdrop_circle(self, r: float = 340, color: str = "yellow_stage", x: float = 0, y: float = 0, delay: float = 0.0):
        """Draws a soft circular stage backdrop centered at `(x, y)` relative to stage center."""
        p = self.spring(delay, 0.48, overshoot=1.3)
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy, pr = self.sx(x), self.sy(y), int(self.ss(r) * p)
        self.draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=self.col(color))

    def circle(
        self,
        x: float,
        y: float,
        r: float,
        color: str = "red_hot",
        alpha: int = 255,
        outline: Optional[str] = None,
        width: float = 0,
        delay: float = 0.0
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pr = int(self.ss(r) * p)
        out_c = self.col(outline, alpha) if outline else None
        self.draw.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=self.col(color, alpha) if color else None, outline=out_c, width=self.ss(width) if width > 0 else 0)

    def ellipse(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        color: str = "pink_organ",
        angle: float = 0.0,
        alpha: int = 255,
        delay: float = 0.0
    ):
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = max(2, int(self.ss(w) * p)), max(2, int(self.ss(h) * p))
        if abs(angle) < 0.5:
            self.draw.ellipse([cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2], fill=self.col(color, alpha))
        else:
            pad = 20
            layer = Image.new("RGBA", (pw + pad * 2, ph + pad * 2), (0, 0, 0, 0))
            ldraw = ImageDraw.Draw(layer)
            ldraw.ellipse([pad, pad, pad + pw, pad + ph], fill=self.col(color, alpha))
            rot = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
            self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
            self.draw = ImageDraw.Draw(self.img, "RGBA")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        radius: float = 20,
        color: str = "white",
        angle: float = 0.0,
        alpha: int = 255,
        shadow: bool = False,
        delay: float = 0.0
    ):
        """Draws a flat-vector rounded rectangle centered at `(x, y)` with optional rotation and soft shadow."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = max(2, int(self.ss(w) * p)), max(2, int(self.ss(h) * p))
        rad = min(pw // 2, ph // 2, self.ss(radius))

        if abs(angle) < 0.5:
            x1, y1, x2, y2 = cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2
            if shadow:
                so = self.ss(10)
                self.draw.rounded_rectangle([x1 + so, y1 + so, x2 + so, y2 + so], radius=rad, fill=(20, 20, 20, 45))
            self.draw.rounded_rectangle([x1, y1, x2, y2], radius=rad, fill=self.col(color, alpha))
        else:
            pad = 24
            layer = Image.new("RGBA", (pw + pad * 2, ph + pad * 2), (0, 0, 0, 0))
            ldraw = ImageDraw.Draw(layer)
            ldraw.rounded_rectangle([pad, pad, pad + pw, pad + ph], radius=rad, fill=self.col(color, alpha))
            rot = layer.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)
            self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
            self.draw = ImageDraw.Draw(self.img, "RGBA")

    def poly(
        self,
        points: List[Tuple[float, float]],
        color: str = "red_hot",
        alpha: int = 255,
        dx: float = 0.0,
        dy: float = 0.0,
        scale: float = 1.0,
        angle: float = 0.0,
        delay: float = 0.0
    ):
        """Draws a custom flat-vector polygon with local translation, scale, and rotation."""
        p = self.spring(delay, 0.45) if delay > 0 else 1.0
        if p <= 0.02 or len(points) < 3:
            return
        self.shape_count += 1
        sc = scale * p
        transformed = []
        for px, py in points:
            tx, ty = px * sc, py * sc
            if abs(angle) > 0.1:
                rad = math.radians(angle)
                rx = tx * math.cos(rad) - ty * math.sin(rad)
                ry = tx * math.sin(rad) + ty * math.cos(rad)
                tx, ty = rx, ry
            transformed.append((self.sx(tx + dx), self.sy(ty + dy)))
        self.draw.polygon(transformed, fill=self.col(color, alpha))

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 12,
        color: str = "dark",
        alpha: int = 255,
        progress: float = 1.0,
        dashed: bool = False,
        dash_speed: float = 140.0
    ):
        pr = clamp(progress, 0.0, 1.0)
        if pr <= 0.01:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        ex = int(sx1 + (sx2 - sx1) * pr)
        ey = int(sy1 + (sy2 - sy1) * pr)
        w_px = self.ss(width)
        c = self.col(color, alpha)

        if not dashed:
            self.draw.line([(sx1, sy1), (ex, ey)], fill=c, width=w_px, joint="curve")
            r_cap = w_px // 2
            self.draw.ellipse([sx1 - r_cap, sy1 - r_cap, sx1 + r_cap, sy1 + r_cap], fill=c)
            self.draw.ellipse([ex - r_cap, ey - r_cap, ex + r_cap, ey + r_cap], fill=c)
        else:
            dist = max(1.0, math.hypot(ex - sx1, ey - sy1))
            ux, uy = (ex - sx1) / dist, (ey - sy1) / dist
            dash_len = self.ss(28)
            gap_len = self.ss(22)
            step = dash_len + gap_len
            offset = (self.lt * dash_speed * self.S) % step
            cur = -offset
            while cur < dist:
                d_start = max(0.0, cur)
                d_end = min(dist, cur + dash_len)
                if d_end > d_start + 2:
                    p1 = (int(sx1 + ux * d_start), int(sy1 + uy * d_start))
                    p2 = (int(sx1 + ux * d_end), int(sy1 + uy * d_end))
                    self.draw.line([p1, p2], fill=c, width=w_px)
                cur += step

    def arc(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        start: float = 0,
        end: float = 180,
        width: float = 14,
        color: str = "dark",
        alpha: int = 255
    ):
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = self.ss(w), self.ss(h)
        self.draw.arc([cx - pw // 2, cy - ph // 2, cx + pw // 2, cy + ph // 2], start=start, end=end, fill=self.col(color, alpha), width=self.ss(width))

    def bezier(
        self,
        points: List[Tuple[float, float]],
        width: float = 14,
        color: str = "red_hot",
        fill: Optional[str] = None,
        alpha: int = 255,
        progress: float = 1.0
    ):
        """Draws a smooth curved Bezier path or filled organic shape from control points."""
        if len(points) < 2:
            return
        self.shape_count += 1
        screen_pts = [(self.sx(px), self.sy(py)) for px, py in points]
        curve = sample_bezier_curve(screen_pts, steps=32)
        n_keep = max(2, int(len(curve) * clamp(progress, 0.0, 1.0)))
        sliced = curve[:n_keep]
        if fill and len(sliced) >= 3:
            self.draw.polygon(sliced, fill=self.col(fill, alpha))
        if width > 0:
            self.draw.line(sliced, fill=self.col(color, alpha), width=self.ss(width), joint="curve")

    def wave_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        amp: float = 22.0,
        freq: float = 3.5,
        speed: float = 8.0,
        width: float = 10.0,
        color: str = "blue_tear",
        alpha: int = 255,
        progress: float = 1.0
    ):
        """Draws an animated sine wave traveling between `(x1, y1)` and `(x2, y2)` (ideal for soundwaves, bone vibration, heat, signals)."""
        pr = clamp(progress, 0.0, 1.0)
        if pr <= 0.02:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        dx, dy = sx2 - sx1, sy2 - sy1
        dist = max(1.0, math.hypot(dx, dy))
        ux, uy = dx / dist, dy / dist
        nx, ny = -uy, ux  # perpendicular normal vector

        steps = 40
        max_step = max(2, int(steps * pr))
        amp_px = amp * self.S
        pts = []
        for i in range(max_step + 1):
            u = i / float(steps)
            env = math.sin(u * math.pi)  # smooth taper at endpoints
            w_off = math.sin(u * freq * 2.0 * math.pi - self.lt * speed) * amp_px * env
            px = int(sx1 + dx * u + nx * w_off)
            py = int(sy1 + dy * u + ny * w_off)
            pts.append((px, py))
        if len(pts) >= 2:
            self.draw.line(pts, fill=self.col(color, alpha), width=self.ss(width), joint="curve")

    def concentric_waves(
        self,
        x: float,
        y: float,
        min_r: float = 45,
        max_r: float = 240,
        count: int = 3,
        color: str = "red_hot",
        width: float = 10,
        start_angle: float = 0,
        end_angle: float = 360
    ):
        """Draws expanding sonar / sound / acoustic / shockwave rings or directional arcs."""
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        r0, r1 = self.ss(min_r), self.ss(max_r)
        for i in range(max(1, int(count))):
            rp = (self.lt * 0.75 + i / float(max(1, count))) % 1.0
            rr = int(r0 + (r1 - r0) * rp)
            alpha = int(235 * (1.0 - rp))
            if abs(end_angle - start_angle) >= 359:
                self.draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=self.col(color, alpha), width=self.ss(width))
            else:
                self.draw.arc([cx - rr, cy - rr, cx + rr, cy + rr], start=start_angle, end=end_angle, fill=self.col(color, alpha), width=self.ss(width))

    def waveform_bars(
        self,
        x: float,
        y: float,
        w: float = 380,
        h: float = 130,
        count: int = 13,
        color: str = "red_hot",
        bg_color: Optional[str] = None,
        speed: float = 9.0
    ):
        """Draws animated audio / voice-note equalizer bars."""
        self.shape_count += 2
        if bg_color:
            self.rect(x, y, w + 60, h + 50, radius=28, color=bg_color, shadow=True)
        cx, cy = self.sx(x), self.sy(y)
        pw, ph = self.ss(w), self.ss(h)
        n = max(3, int(count))
        gap = self.ss(10)
        bw = max(6, (pw - gap * (n - 1)) // n)
        start_x = cx - pw // 2

        for i in range(n):
            wave_val = 0.25 + 0.75 * abs(math.sin(self.lt * speed + i * 0.65))
            bh = max(bw, int(ph * wave_val * self.ease(i * 0.02, 0.35)))
            bx = start_x + i * (bw + gap)
            self.draw.rounded_rectangle([bx, cy - bh // 2, bx + bw, cy + bh // 2], radius=bw // 2, fill=self.col(color))

    def blob(
        self,
        x: float,
        y: float,
        r: float = 135,
        lobes: int = 7,
        color: str = "pink_soft",
        delay: float = 0.0
    ):
        """Draws an organic multi-lobed cloud / brain / cell / organ silhouette."""
        p = self.spring(delay, 0.48)
        if p <= 0.03:
            return
        self.shape_count += 2
        cx, cy = self.sx(x), self.sy(y)
        base_r = int(self.ss(r) * p)
        c = self.col(color)
        self.draw.ellipse([cx - base_r, cy - int(base_r * 0.85), cx + base_r, cy + int(base_r * 0.85)], fill=c)
        for i in range(max(4, int(lobes))):
            ang = (2.0 * math.pi * i) / lobes + math.sin(self.lt * 2.0 + i) * 0.05
            lx = cx + int(math.cos(ang) * base_r * 0.62)
            ly = cy + int(math.sin(ang) * base_r * 0.52)
            lr = int(base_r * 0.58)
            self.draw.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=c)

    def arrow(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 14,
        color: str = "red_hot",
        delay: float = 0.1,
        dur: float = 0.45
    ):
        p = self.ease(delay, dur)
        if p <= 0.04:
            return
        self.shape_count += 1
        sx1, sy1 = self.sx(x1), self.sy(y1)
        sx2, sy2 = self.sx(x2), self.sy(y2)
        cx = int(sx1 + (sx2 - sx1) * p)
        cy = int(sy1 + (sy2 - sy1) * p)
        c = self.col(color)
        self.draw.line([(sx1, sy1), (cx, cy)], fill=c, width=self.ss(width))
        if p > 0.2:
            ang = math.atan2(sy2 - sy1, sx2 - sx1)
            hlen = self.ss(width * 2.3)
            p1 = (int(cx - hlen * math.cos(ang - 0.55)), int(cy - hlen * math.sin(ang - 0.55)))
            p2 = (int(cx - hlen * math.cos(ang + 0.55)), int(cy - hlen * math.sin(ang + 0.55)))
            self.draw.polygon([(cx, cy), p1, p2], fill=c)

    def starburst(
        self,
        x: float,
        y: float,
        r_out: float = 110,
        r_in: float = 55,
        points: int = 10,
        color: str = "yellow_stage",
        speed: float = 0.6,
        delay: float = 0.0
    ):
        p = self.elastic(delay, 0.5) if delay > 0 else 1.0
        if p <= 0.03:
            return
        self.shape_count += 1
        cx, cy = self.sx(x), self.sy(y)
        ro, ri = self.ss(r_out) * p, self.ss(r_in) * p
        pts = []
        step = math.pi / max(3, int(points))
        for i in range(int(points) * 2):
            r = ro if i % 2 == 0 else ri
            ang = i * step + self.lt * speed
            pts.append((int(cx + math.cos(ang) * r), int(cy + math.sin(ang) * r)))
        self.draw.polygon(pts, fill=self.col(color))

    def droplet(self, x: float, y: float, r: float = 32, color: str = "blue_tear", angle: float = 0.0, delay: float = 0.0):
        """Draws a teardrop / sweat drop / flame petal centered at `(x, y)`."""
        p = self.spring(delay, 0.42) if delay > 0 else 1.0
        if p <= 0.03:
            return
        self.shape_count += 1
        pts = [(-r, 0), (0, -r * 2.1), (r, 0)]
        self.circle(x, y, r * p, color=color)
        self.poly(pts, color=color, dx=x, dy=y, scale=p, angle=angle)

    def ring_gauge(
        self,
        x: float,
        y: float,
        r: float = 150,
        pct: float = 0.78,
        color: str = "red_hot",
        bg_color: str = "white",
        label: str = "78%",
        delay: float = 0.05
    ):
        p = self.spring(delay, 0.5)
        if p <= 0.04:
            return
        self.shape_count += 2
        cx, cy = self.sx(x), self.sy(y)
        pr = int(self.ss(r) * p)
        bbox = [cx - pr, cy - pr, cx + pr, cy + pr]
        self.draw.ellipse(bbox, fill=self.col(bg_color))
        sweep = int(360.0 * clamp(pct, 0.0, 1.0) * self.ease(delay + 0.05, 0.65))
        self.draw.pieslice(bbox, start=-90, end=-90 + sweep, fill=self.col(color))
        ir = int(pr * 0.62)
        self.draw.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], fill=self.col(self.beat.get("bg_color", "cream")))
        if label and p > 0.45:
            f = self._fit_font(str(label), int(r * 0.45), max_w_ratio=0.35)
            self.draw.text((cx, cy), str(label), font=f, fill=self.col("dark"), anchor="mm")

    def label_pill(
        self,
        x: float,
        y: float,
        text: str = "LABEL",
        bg: str = "white",
        fg: str = "red_hot",
        size: int = 38,
        pointer: bool = False,
        delay: float = 0.15
    ):
        """Draws a floating callout pill or speech bubble (`pointer=True`) at `(x, y)` relative to stage center."""
        p = self.spring(delay, 0.42, overshoot=1.7)
        if p <= 0.05 or not text:
            return
        self.shape_count += 1
        cx = self.sx(x)
        cy = self.sy(y) + int((1.0 - p) * 30 * self.S)
        txt = str(text).upper().strip()
        f = self._fit_font(txt, size, max_w_ratio=0.55)
        bb = self.draw.textbbox((0, 0), txt, font=f)
        bw = int(((bb[2] - bb[0]) + 52 * self.S) * min(1.05, p))
        bh = int(((bb[3] - bb[1]) + 32 * self.S) * min(1.05, p))

        x1, y1, x2, y2 = cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2
        self.draw.rounded_rectangle([x1, y1, x2, y2], radius=bh // 2 if not pointer else int(22 * self.S), fill=self.col(bg))
        if pointer:
            tw, th = self.ss(16), self.ss(24)
            self.draw.polygon([(cx - tw, y2 - 2), (cx + tw, y2 - 2), (cx, y2 + th)], fill=self.col(bg))
        if p > 0.4:
            self.draw.text((cx, cy), txt, font=f, fill=self.col(fg), anchor="mm")

    # ==========================================
    # 2. AUTOMATIC STORYBOARD LAYOUT & TYPOGRAPHY RENDERER
    # ==========================================
    def render_beat_typography(self):
        """Renders the beat's main poster typography & sequence indicators automatically from the Storyboard JSON."""
        layout = str(self.beat.get("layout", "top_text_bottom_art")).lower()
        dots_idx = int(self.beat.get("dot_index", 0) or 0)
        lines = self.beat.get("lines", [])

        if dots_idx in (1, 2, 3):
            cy = int(self.H * 0.075)
            cx = self.W // 2
            spacing = int(140 * self.S)
            for i in range(1, 4):
                dx = cx + (i - 2) * spacing
                is_act = (i == dots_idx)
                r = int((26 if is_act else 22) * self.S * (self.spring(0.0, 0.35) if is_act else 1.0))
                c = self.col("red_hot") if is_act else self.col("grey", 200)
                self.draw.ellipse([dx - r, cy - r, dx + r, cy + r], fill=c)

        if layout == "statement_pill" and len(lines) >= 3:
            w1 = str(lines[0].get("text", "")).upper()
            w2 = str(lines[1].get("text", "")).upper()
            w3 = str(lines[2].get("text", "")).upper()
            cx, cy = self.W // 2, int(self.H * 0.38)
            p1 = self.spring(0.0, 0.42)
            if p1 > 0.05:
                f1 = self._fit_font(w1, 175)
                self.draw.text((cx, cy - int(205 * self.S) + int((1.0 - p1) * 45 * self.S)), w1, font=f1, fill=self.col("cream"), anchor="mm")
            p2 = self.spring(0.16, 0.45)
            if p2 > 0.05:
                pw, ph = int(760 * self.S * p2), int(145 * self.S * p2)
                layer = Image.new("RGBA", (pw + 60, ph + 60), (0, 0, 0, 0))
                ld = ImageDraw.Draw(layer)
                ld.rounded_rectangle([30, 30, 30 + pw, 30 + ph], radius=int(24 * self.S), fill=self.col("dark"))
                f2 = self._fit_font(w2, 105, max_w_ratio=0.65)
                ld.text((30 + pw // 2, 30 + ph // 2), w2, font=f2, fill=self.col("cream"), anchor="mm")
                rot = layer.rotate(4.0, resample=Image.Resampling.BICUBIC, expand=True)
                self.img.paste(rot, (cx - rot.width // 2, cy - rot.height // 2), rot)
                self.draw = ImageDraw.Draw(self.img, "RGBA")
            p3 = self.spring(0.32, 0.45)
            if p3 > 0.05:
                f3 = self._fit_font(w3, 185)
                self.draw.text((cx, cy + int(215 * self.S) + int((1.0 - p3) * 50 * self.S)), w3, font=f3, fill=self.col("cream"), anchor="mm")
            return

        # Standard Top or Bottom Staggered Poster Lines
        y_start_frac = 0.73 if layout in ("triptych_dots", "top_art_bottom_text") else 0.14
        cur_y = int(self.H * y_start_frac)
        cx = self.W // 2

        for idx, item in enumerate(lines):
            txt = str(item.get("text", "")).upper().strip()
            if not txt:
                continue
            delay = float(item.get("delay", idx * 0.16))
            col_key = str(item.get("color", "dark"))
            base_sz = int(item.get("size", 108))

            p = self.spring(delay, 0.42, overshoot=1.55)
            if p <= 0.05:
                continue
            f = self._fit_font(txt, base_sz, max_w_ratio=0.88)
            y_off = int((1.0 - p) * 45 * self.S)
            ty = cur_y + int(f.size * 0.5) + y_off
            self.draw.text((cx, ty), txt, font=f, fill=self.col(col_key), anchor="mm")
            cur_y += int(f.size * 1.16)


# ==========================================
# STAGE 5A & 5B: 2-PASS CREATIVE DIRECTOR & PURE PYTHON VECTOR ARTIST
# ==========================================
PASS1_STORYBOARD_PROMPT = """You are a Senior Motion Graphics Creative Director (Claude Nalarka style).
Given a topic, structured script, and Whisper word timestamps, design a 10 to 14 beat visual storyboard from `0.0` to `{total_dur}.0` seconds.
Voiceover starts at `1.0`s.

### RULES FOR DIVERSE, TOPIC-SPECIFIC VISUAL STORYTELLING
1. **EVERY BEAT MUST DEPICT A CONCRETE, UNIQUE PHYSICAL OBJECT OR MECHANISM FROM THE SCRIPT**:
   - Write a detailed `visual_blueprint` for each beat describing the exact objects, parts, colors, and motion to draw (e.g. for "Why our recorded voice sounds cringe": Beat 1 = Smartphone voice-note player card with pulsing waveform bars; Beat 2 = Ear wincing away from jagged high-pitched soundwaves; Beat 3 = Finger pressing a giant fast-forward SKIP `[>>|]` button; Beat 4 = Side-profile head showing TWO sound paths: blue air waves curving outside vs warm orange bone-conduction waves vibrating through the skull bones; Beat 5 = Studio microphone capturing ONLY the blue air waves while orange bone waves bounce away; Beat 6 = Bass vs Treble frequency equalizer; Beat 7 = Brain comparing two audio cassettes/waveforms with a shocked reaction).
   - **NEVER repeat the same object across unrelated beats!**
2. **ROTATE LAYOUTS & BACKGROUND COLORS**:
   - `layout`:
     * `"triptych_dots"` : Top 3-dot progress indicator (`dot_index`: 1, 2, or 3) + illustration in upper-middle (`stage_cy=43%`) + 2 giant lines of text at bottom (`y=73%`).
     * `"top_text_bottom_art"` : 2–3 giant staggered lines of text at top (`y=14%`) + illustration stage in lower half (`stage_cy=60%`).
     * `"top_art_bottom_text"` : Illustration in upper-middle (`stage_cy=43%`) + 2 giant lines of text at bottom (`y=73%`).
     * `"statement_pill"` : Full-screen 3-word punchy statement (`lines` has 3 items: Top Word, Tilted Pill Word, Bottom Word) + accent illustration at bottom (`stage_cy=77%`).
   - `bg_color`: Rotate `"cream"`, `"orange_bg"`, and `"green_bg"`! (When `bg_color` is `"orange_bg"` or `"green_bg"`, set line text colors to `"cream"` and `"dark"`!).
   - `lines`: 2 to 3 short, punchy lines (`1 to 4 words per line`), each with `"text"`, `"color"`, `"size"` (`90` to `130`, or `52` for small subtitle tags), and `"delay"` (`0.0`, `0.18`, `0.36`).

Return ONLY valid JSON:
{{
  "scenes": [
    {{
      "beat_id": 1,
      "start": 0.0,
      "end": 2.4,
      "bg_color": "cream",
      "layout": "top_text_bottom_art",
      "dot_index": 0,
      "transition": "cut",
      "summary": "Hook Title & Voice Note Player",
      "visual_blueprint": "Yellow stage circle with a sleek smartphone showing a voice recording bubble, animated equalizer waveform bars, and a pulsing red REC/Play icon.",
      "lines": [
        {{"text": "KENAPA", "color": "dark", "size": 105, "delay": 0.0}},
        {{"text": "SUARA SENDIRI", "color": "dark", "size": 105, "delay": 0.14}},
        {{"text": "BIKIN CRINGE?", "color": "red_hot", "size": 105, "delay": 0.28}}
      ]
    }}
  ]
}}"""


PASS2_PYTHON_VECTOR_ARTIST_PROMPT = """You are a Senior Flat-Vector Illustrator & Creative Coder writing pure Python drawing functions `def beat_01(v):` through `def beat_{count:02d}(v):` for `VectorKit` (`v`).

### COORDINATE SYSTEM OF `v` (`VectorKit`)
- `(0, 0)` is ALREADY positioned at the center of the illustration stage!
- Coordinates `x` and `y` are in virtual pixels relative to `(0, 0)` (safe stage bounds: `x` in `-380 .. +380`, `y` in `-280 .. +280`).
- Typography (`lines` and `three_dots`) is ALREADY rendered automatically by the engine—your `def beat_XX(v):` function MUST focus 100% on drawing the **custom multi-part flat-vector illustration** described in `visual_blueprint`!

### AVAILABLE `v` (`VectorKit`) PRIMITIVES
- **Palette Colors**: `"cream"`, `"orange_bg"`, `"green_bg"`, `"blue_bg"`, `"dark"`, `"red_hot"`, `"yellow_stage"`, `"blue_soft"`, `"blue_tear"`, `"mint_soft"`, `"pink_organ"`, `"pink_soft"`, `"purple"`, `"skin"`, `"skin_dark"`, `"white"`, `"grey"`, `"stone"`, `"stone_dark"`, `"brown"`.
- **Timing & Physics**:
  * `v.lt` (seconds elapsed in beat), `v.p` (0.0 to 1.0 progress)
  * `v.ease(delay=0.0, dur=0.45)`, `v.spring(delay=0.0, dur=0.48)`, `v.elastic(delay=0.0, dur=0.6)`
  * `v.wave(freq=2.0, amp=15.0, phase=0.0)` -> returns oscillating float offset in virtual pixels!
  * `v.pulse(freq=2.5, lo=0.94, hi=1.06)` -> returns oscillating scale factor!
- **Flat-Vector Drawing Primitives (No ugly black outlines; layer colorful flat shapes!)**:
  * `v.backdrop_circle(r=330, color="yellow_stage"|"blue_soft"|"mint_soft", x=0, y=0, delay=0.0)`
  * `v.circle(x, y, r, color="...", alpha=255, outline=None, width=0, delay=0.0)`
  * `v.ellipse(x, y, w, h, color="...", angle=0.0, alpha=255, delay=0.0)`
  * `v.rect(x, y, w, h, radius=20, color="...", angle=0.0, alpha=255, shadow=False, delay=0.0)`
  * `v.poly([(x1,y1), (x2,y2), (x3,y3), ...], color="...", alpha=255, dx=0, dy=0, scale=1.0, angle=0.0, delay=0.0)`
  * `v.line(x1, y1, x2, y2, width=12, color="...", alpha=255, progress=1.0, dashed=False, dash_speed=140.0)`
  * `v.arc(x, y, w, h, start=0, end=180, width=14, color="...", alpha=255)`
  * `v.bezier([(x1,y1), (x2,y2), (x3,y3), (x4,y4)], width=14, color="...", fill=None, alpha=255, progress=1.0)`
  * `v.wave_line(x1, y1, x2, y2, amp=22, freq=3.5, speed=8.0, width=10, color="...", alpha=255, progress=1.0)` -> Animated sine wave (great for soundwaves, bone vibrations, heat, signals)!
  * `v.concentric_waves(x, y, min_r=45, max_r=240, count=3, color="...", width=10, start_angle=0, end_angle=360)` -> Expanding sonar / directional sound arcs!
  * `v.waveform_bars(x, y, w=360, h=120, count=13, color="...", bg_color=None, speed=9.0)` -> Animated audio equalizer bars!
  * `v.blob(x, y, r=135, lobes=7, color="pink_soft", delay=0.0)` -> Organic cloud / brain / organ shape!
  * `v.arrow(x1, y1, x2, y2, width=14, color="...", delay=0.1, dur=0.45)`
  * `v.starburst(x, y, r_out=110, r_in=55, points=10, color="...", speed=0.6, delay=0.0)`
  * `v.droplet(x, y, r=32, color="...", angle=0.0, delay=0.0)`
  * `v.ring_gauge(x, y, r=150, pct=0.78, color="...", bg_color="white", label="78%", delay=0.05)`
  * `v.label_pill(x, y, text="UDARA", bg="white", fg="red_hot", size=36, pointer=True|False, delay=0.2)`

### STRICT QUALITY REQUIREMENTS FOR EACH `def beat_XX(v):`
1. **Draw the EXACT physical objects in `visual_blueprint` using 8 to 20 shape calls per function!**
   - Build recognizable, charming flat-vector objects by layering `v.rect`, `v.circle`, `v.ellipse`, `v.poly`, `v.arc`, `v.bezier`, `v.wave_line`, `v.waveform_bars`, and `v.label_pill`.
   - For example:
     * A **microphone** = `v.rect(0, -40, 110, 170, radius=55, color="stone")` + grill lines `v.line(-35, -70, 35, -70, width=6, color="grey")` + U-stand `v.arc(0, -25, 160, 160, start=0, end=180, width=14, color="dark")` + stem `v.line(0, 55, 0, 145, width=16, color="dark")` + base `v.rect(0, 150, 140, 24, radius=12, color="dark")` + incoming `v.wave_line(...)`.
     * A **skip/fast-forward button or audio player** = player card `v.rect(...)` + `v.waveform_bars(...)` + double triangles `v.poly([(-40,-45), (15,0), (-40,45)], color="white")` + `v.poly([(10,-45), (65,0), (10,45)], color="white")` + bar `v.rect(78, 0, 14, 90, radius=6, color="white")`.
     * **Air vs Bone sound conduction** = side head silhouette (`v.circle` cranium + `v.rect` jaw + ear `v.ellipse`) + outer blue `v.wave_line(-280, -40, -40, -10, color="blue_tear")` labeled `"UDARA"` + inner orange bone vibration `v.wave_line(-40, 95, 85, -10, color="red_hot", freq=6.0)` labeled `"TULANG"`!
2. **Every beat MUST look distinct from the previous beat** and animate smoothly using `v.spring(...)`, `v.ease(...)`, `v.wave(...)`, `v.wave_line(...)`, or `v.pulse(...)`.
3. Output ONLY a single ` ```python ... ``` ` code block containing `def beat_01(v):` through `def beat_{count:02d}(v):`.
"""


def generate_two_pass_storyboard_and_code(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    sections: List[Dict[str, str]],
    words: List[dict],
    total_dur: int,
    ref_profile: dict,
    log_fn
) -> Tuple[dict, Dict[str, Any]]:
    # PASS 1: Storyboard & Visual Blueprints (JSON)
    log_fn(f"[PASS 1/2: CREATIVE DIRECTOR] Planning topic-specific visual blueprints for '{topic}'...")
    p1_sys = PASS1_STORYBOARD_PROMPT.format(total_dur=total_dur)
    p1_user = json.dumps({
        "topic": topic,
        "structured_sections": sections,
        "word_timestamps": words,
        "total_duration": total_dur
    }, ensure_ascii=False)

    plan = call_ai_json(base_url, api_key, model, p1_sys, p1_user, log_fn, temperature=0.40)
    plan["palette"] = ref_profile["palette"]
    scenes = plan.get("scenes", [])
    if not scenes:
        raise ValueError("Pass 1 returned an empty 'scenes' list.")

    scenes[0]["start"] = 0.0
    for i in range(len(scenes) - 1):
        scenes[i]["end"] = round(float(scenes[i + 1]["start"]), 3)
        scenes[i]["beat_id"] = i + 1
    scenes[-1]["end"] = float(total_dur)
    scenes[-1]["beat_id"] = len(scenes)

    for s in scenes:
        log_fn(f"  Beat #{s['beat_id']:02d} [{s['start']:04.1f}s-{s['end']:04.1f}s] ({s.get('layout')}, {s.get('bg_color')}): {s.get('visual_blueprint')}")

    # PASS 2: Pure Python Flat-Vector Code Generation
    log_fn(f"[PASS 2/2: VECTOR ARTIST] Writing pure Python flat-vector functions for {len(scenes)} beats...")
    p2_sys = PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(count=len(scenes))
    blueprint_list = [
        {
            "function_name": f"beat_{s['beat_id']:02d}",
            "duration": round(float(s["end"]) - float(s["start"]), 2),
            "bg_color": s.get("bg_color", "cream"),
            "layout": s.get("layout", "top_text_bottom_art"),
            "summary": s.get("summary", ""),
            "visual_blueprint": s.get("visual_blueprint", "")
        }
        for s in scenes
    ]
    p2_user = (
        f"Topic: {topic}\n"
        f"Write Python functions `beat_01(v)` through `beat_{len(scenes):02d}(v)` implementing each `visual_blueprint` below:\n"
        + json.dumps(blueprint_list, indent=2, ensure_ascii=False)
    )

    py_code = call_ai_python_code(base_url, api_key, model, p2_sys, p2_user, log_fn, temperature=0.35)
    with open("generated_scenes.py", "w", encoding="utf-8") as f:
        f.write("# Auto-generated by Nalarka Motion Graphics Studio v7.0\nimport math\n\n" + py_code + "\n")

    namespace = compile_scene_module(py_code, log_fn)
    return plan, namespace


# ==========================================
# STAGE 5C: SELF-CORRECTION LOOP (COMPLEXITY + ANTI-REPETITION AUDIT)
# ==========================================
def compile_scene_module(py_code: str, log_fn) -> Dict[str, Any]:
    safe_globals: Dict[str, Any] = {
        "__builtins__": {
            "range": range, "len": len, "min": min, "max": max,
            "abs": abs, "int": int, "float": float, "str": str,
            "round": round, "enumerate": enumerate, "zip": zip,
            "list": list, "dict": dict, "tuple": tuple,
            "True": True, "False": False, "None": None,
        },
        "math": math,
        "clamp": clamp,
    }
    try:
        exec(py_code, safe_globals)
    except SyntaxError as e:
        log_fn(f"[PYTHON SYNTAX WARN] {e}")
    return safe_globals


def render_single_beat_canvas(
    t: float,
    beat: dict,
    beat_idx: int,
    width: int,
    height: int,
    palette: dict,
    words: List[dict],
    namespace: Dict[str, Any]
) -> Tuple[Image.Image, VectorKit, Optional[str]]:
    v = VectorKit(width, height, t, beat, words, palette)
    v.render_beat_typography()

    fn_name = f"beat_{beat_idx + 1:02d}"
    fn = namespace.get(fn_name)
    err = None
    if callable(fn):
        try:
            fn(v)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
    else:
        err = f"Function '{fn_name}(v)' not found in generated_scenes.py"

    # Procedural geometric fallback if AI function crashed after all retries (NO yawning face!)
    if err is not None or v.shape_count == 0:
        v.backdrop_circle(r=310, color="yellow_stage" if v.beat.get("bg_color") != "orange_bg" else "cream", delay=0.0)
        v.concentric_waves(0, 0, min_r=60, max_r=260, count=3, color="red_hot", width=10)
        v.waveform_bars(0, 0, w=340, h=120, count=11, color="dark", bg_color="white")

    return v.img, v, err


def rewrite_single_beat_function_with_ai(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    beat_idx: int,
    beat: dict,
    reason: str,
    log_fn
) -> Optional[Any]:
    fn_name = f"beat_{beat_idx + 1:02d}"
    sys_prompt = (
        PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(count=beat_idx + 1)
        + f"\nWrite ONLY the single Python function `def {fn_name}(v):` to fix the audit issue below.\n"
        "Use at least 8 distinct `v.*` vector shape calls to draw the exact object in `visual_blueprint`!"
    )
    user_prompt = json.dumps({
        "topic": topic,
        "function_name": fn_name,
        "bg_color": beat.get("bg_color", "cream"),
        "layout": beat.get("layout", "top_text_bottom_art"),
        "summary": beat.get("summary", ""),
        "visual_blueprint": beat.get("visual_blueprint", ""),
        "audit_issue_to_fix": reason
    }, ensure_ascii=False)

    code_str = call_ai_python_code(base_url, api_key, model, sys_prompt, user_prompt, log_fn, temperature=0.45)
    ns = compile_scene_module(code_str, log_fn)
    if callable(ns.get(fn_name)):
        beat["custom_code"] = code_str
        return ns[fn_name]
    return None


def audit_and_fix_beats(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    width: int,
    height: int,
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    log_fn,
    max_retries: int = 2
):
    log_fn(f"[SELF-CORRECTION LOOP v7.0] Auditing {len(plan.get('scenes', []))} beats for complexity, motion & anti-repetition...")
    palette = plan.get("palette", {})
    scenes = plan.get("scenes", [])
    prev_stage_crop: Optional[np.ndarray] = None

    for idx, beat in enumerate(scenes):
        st, en = float(beat["start"]), float(beat["end"])
        dur = max(0.3, en - st)
        t_early = st + min(0.12, dur * 0.15)
        t_mid = st + dur * 0.55
        t_late = st + dur * 0.88

        for attempt in range(max_retries + 1):
            img1, v1, err1 = render_single_beat_canvas(t_early, beat, idx, width, height, palette, words, namespace)
            img2, v2, err2 = render_single_beat_canvas(t_mid, beat, idx, width, height, palette, words, namespace)
            img3, v3, err3 = render_single_beat_canvas(t_late, beat, idx, width, height, palette, words, namespace)

            runtime_err = err1 or err2 or err3
            arr1 = np.asarray(img1, dtype=np.float32)
            arr2 = np.asarray(img2, dtype=np.float32)
            arr3 = np.asarray(img3, dtype=np.float32)
            motion_score = float(np.mean(np.abs(arr2 - arr1)) + np.mean(np.abs(arr3 - arr2)))

            # Crop illustration stage region to check visual uniqueness against previous beat
            cy = v2.stage_cy
            r_crop = int(min(width, height) * 0.28)
            y_lo, y_hi = max(0, cy - r_crop), min(height, cy + r_crop)
            x_lo, x_hi = max(0, width // 2 - r_crop), min(width, width // 2 + r_crop)
            cur_crop = np.asarray(img2.crop((x_lo, y_lo, x_hi, y_hi)).resize((64, 64)), dtype=np.float32)

            diff_from_prev = 99.0
            if prev_stage_crop is not None and prev_stage_crop.shape == cur_crop.shape:
                diff_from_prev = float(np.mean(np.abs(cur_crop - prev_stage_crop)))

            issues = []
            if runtime_err:
                issues.append(f"Runtime Error: {runtime_err}")
            if v2.shape_count < 5:
                issues.append(f"Illustration is too simple (only {v2.shape_count} shapes). Build a detailed multi-part object with >= 7 shapes.")
            if motion_score < 0.22:
                issues.append(f"Motion score too low ({motion_score:.2f}). Animate parts using `v.spring`, `v.wave`, `v.wave_line`, or `v.pulse`.")
            if diff_from_prev < 6.5:
                issues.append(f"Illustration looks too similar to Beat #{idx:02d} (diff={diff_from_prev:.1f}). Draw a completely different object matching `{beat.get('visual_blueprint')}`!")

            if not issues:
                prev_stage_crop = cur_crop
                log_fn(
                    f"  [BEAT #{idx+1:02d} PASS] shapes={v2.shape_count} | motion={motion_score:.2f} | "
                    f"unique_diff={diff_from_prev:.1f} | '{beat.get('summary')}'"
                )
                break

            reason_str = " | ".join(issues)
            if attempt < max_retries:
                log_fn(f"  [BEAT #{idx+1:02d} AUTO-FIX {attempt+1}/{max_retries}] {reason_str}")
                new_fn = rewrite_single_beat_function_with_ai(base_url, api_key, model, topic, idx, beat, reason_str, log_fn)
                if new_fn:
                    namespace[f"beat_{idx+1:02d}"] = new_fn
            else:
                prev_stage_crop = cur_crop
                log_fn(f"  [BEAT #{idx+1:02d} PROCEED] Max retries reached ({reason_str}).")


# ==========================================
# STAGE 6: DETERMINISTIC FRAME PIPELINE & TRANSITIONS
# ==========================================
def composite_transition(
    img_prev: Image.Image,
    img_next: Image.Image,
    progress: float,
    trans_type: str
) -> Image.Image:
    if trans_type == "cut":
        return img_next
    p = ease_in_out_cubic(progress)
    w, h = img_next.size

    if trans_type == "directional_wipe":
        split_x = int(w * p)
        out = img_prev.copy()
        if split_x > 0:
            out.paste(img_next.crop((0, 0, split_x, h)), (0, 0))
        return out

    elif trans_type == "whip_pan":
        offset = int(w * p)
        out = Image.new("RGB", (w, h))
        out.paste(img_prev, (-offset, 0))
        out.paste(img_next, (w - offset, 0))
        return out

    elif trans_type == "circle_iris":
        mask = Image.new("L", (w, h), 0)
        mdraw = ImageDraw.Draw(mask)
        max_rad = int(math.hypot(w, h) * 0.65 * p)
        cx, cy = w // 2, h // 2
        mdraw.ellipse([cx - max_rad, cy - max_rad, cx + max_rad, cy + max_rad], fill=255)
        return Image.composite(img_next, img_prev, mask)

    return Image.blend(img_prev, img_next, p)


def render_frame(
    t: float,
    width: int,
    height: int,
    plan: dict,
    words: List[dict],
    namespace: Dict[str, Any]
) -> Image.Image:
    palette = plan.get("palette", {})
    scenes = plan["scenes"]
    active_idx = len(scenes) - 1
    for i, sc in enumerate(scenes):
        if float(sc["start"]) <= t <= float(sc["end"]):
            active_idx = i
            break

    active_beat = scenes[active_idx]
    img, _, _ = render_single_beat_canvas(t, active_beat, active_idx, width, height, palette, words, namespace)

    trans_type = str(active_beat.get("transition", "cut")).lower()
    trans_dur = 0.22
    local_t = t - float(active_beat["start"])
    if active_idx > 0 and trans_type != "cut" and 0.0 <= local_t < trans_dur:
        prev_beat = scenes[active_idx - 1]
        img_prev, _, _ = render_single_beat_canvas(t, prev_beat, active_idx - 1, width, height, palette, words, namespace)
        img = composite_transition(img_prev, img, local_t / trans_dur, trans_type)

    return img


def render_video_pipeline(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    vo_path: str,
    bgm_path: Optional[str],
    output_path: str,
    total_dur: int,
    width: int,
    height: int,
    log_fn,
    progress_fn
):
    total_frames = total_dur * FPS
    fade_st = max(0.0, round(total_dur - 2.5, 2))
    fade_vo_st = max(0.0, round(total_dur - 0.5, 2))

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(FPS),
        "-i", "-",
        "-i", vo_path,
    ]

    if bgm_path and os.path.exists(bgm_path):
        cmd += ["-stream_loop", "-1", "-i", bgm_path]
        filter_complex = (
            f"[1:a]adelay=1000:all=1,apad=whole_dur={total_dur},atrim=0:{total_dur},asetpts=PTS-STARTPTS,asplit=2[vo1][vo2];"
            f"[2:a]atrim=0:{total_dur},asetpts=PTS-STARTPTS,volume=0.32,afade=t=out:st={fade_st}:d=2.5[bg];"
            f"[bg][vo1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
            f"[duck][vo2]amix=inputs=2:duration=longest:dropout_transition=0,volume=2,alimiter=limit=0.95[aout]"
        )
    else:
        filter_complex = (
            f"[1:a]adelay=1000:all=1,apad=whole_dur={total_dur},atrim=0:{total_dur},"
            f"afade=t=out:st={fade_vo_st}:d=0.5[aout]"
        )

    cmd += [
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(total_frames),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", str(total_dur),
        "-movflags", "+faststart", output_path
    ]

    log_fn(f"[FFMPEG] Rendering {total_frames} frames ({total_dur}s @ {FPS}fps, {width}x{height})...")
    t0 = time.time()

    with open(FFMPEG_LOG_FILE, "w", encoding="utf-8") as ff_err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=ff_err)
        try:
            for frame_idx in range(total_frames):
                t = frame_idx / float(FPS)
                frame_img = render_frame(t, width, height, plan, words, namespace)
                proc.stdin.write(frame_img.tobytes())

                if (frame_idx + 1) % 30 == 0 or frame_idx == total_frames - 1:
                    pct = int(((frame_idx + 1) / total_frames) * 100)
                    progress_fn(pct, f"Rendering frame {frame_idx + 1}/{total_frames} ({pct}%)")
                if (frame_idx + 1) % 150 == 0 or frame_idx == total_frames - 1:
                    log_fn(f"[RENDER] Progress: {frame_idx + 1}/{total_frames} frames ({int((frame_idx+1)/total_frames*100)}%)")
        finally:
            if proc.stdin:
                proc.stdin.close()
            ret = proc.wait()

    if ret != 0:
        with open(FFMPEG_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            err_tail = f.read()[-1500:]
        log_fn(f"[FFMPEG ERROR] Exit code {ret}:\n{err_tail}")
        raise RuntimeError(f"FFmpeg failed with exit code {ret}. Check {FFMPEG_LOG_FILE}.")

    log_fn(f"[RENDER] Completed in {round(time.time() - t0, 2)}s -> {output_path}")


# ==========================================
# STAGE 7: 7-POINT AUTOMATED VERIFICATION SUITE
# ==========================================
def run_verification_suite(
    output_path: str,
    expected_dur: Optional[int],
    expected_w: int,
    expected_h: int,
    log_fn
) -> dict:
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Output MP4 file not found: {output_path}")

    log_fn("\n==========================================")
    log_fn("RUNNING 7-POINT AUTOMATED VERIFICATION SUITE")
    log_fn("==========================================")

    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,pix_fmt",
        "-of", "json", output_path
    ]
    probe_data = json.loads(subprocess.check_output(probe_cmd, text=True))
    fmt_dur = float(probe_data.get("format", {}).get("duration", 0.0))
    streams = probe_data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    target_dur = expected_dur if expected_dur is not None else int(round(fmt_dur))
    expected_frames = target_dur * FPS

    frame_cmd = [
        "ffprobe", "-v", "error", "-count_frames",
        "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
        "-of", "csv=p=0", output_path
    ]
    actual_frames = int(subprocess.check_output(frame_cmd, text=True).strip())

    black_cmd = ["ffmpeg", "-i", output_path, "-vf", "blackdetect=d=0.1:pix_th=0.05", "-an", "-f", "null", "-"]
    black_proc = subprocess.run(black_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    black_hits = [ln for ln in black_proc.stderr.splitlines() if "black_start" in ln]

    vol_cmd = ["ffmpeg", "-i", output_path, "-af", "volumedetect", "-vn", "-f", "null", "-"]
    vol_proc = subprocess.run(vol_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    mean_vol, max_vol = -20.0, -1.0
    for ln in vol_proc.stderr.splitlines():
        if "mean_volume:" in ln:
            m = re.search(r"mean_volume:\s*([-\d.]+)", ln)
            if m:
                mean_vol = float(m.group(1))
        if "max_volume:" in ln:
            m = re.search(r"max_volume:\s*([-\d.]+)", ln)
            if m:
                max_vol = float(m.group(1))

    check_dir = Path("check")
    if check_dir.exists():
        shutil.rmtree(check_dir)
    check_dir.mkdir(parents=True, exist_ok=True)

    last_ts = max(0.0, round(target_dur - 0.1, 2))
    subprocess.run(
        ["ffmpeg", "-y", "-i", output_path, "-vf", "fps=1/2,scale=360:-1", str(check_dir / "check_%02d.png")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    last_frame_path = str(check_dir / "last_frame.png")
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(last_ts), "-i", output_path, "-frames:v", "1", last_frame_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    last_frame_valid = False
    if os.path.exists(last_frame_path):
        lf_arr = np.asarray(Image.open(last_frame_path).convert("RGB"), dtype=np.float32)
        last_frame_valid = float(np.std(lf_arr)) > 5.0

    chk_files = sorted(check_dir.glob("check_*.png"))
    if chk_files:
        thumbs = [Image.open(p).convert("RGB") for p in chk_files[:15]]
        cols = min(5, len(thumbs))
        rows = math.ceil(len(thumbs) / cols)
        tw, th = thumbs[0].size
        sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
        for i, im_t in enumerate(thumbs):
            r, c = divmod(i, cols)
            sheet.paste(im_t, (c * tw, r * th))
        sheet.save(str(check_dir / "verification_contact_sheet.jpg"), quality=90)

    tests = [
        (
            "1. Codec & Pixel Format (H.264 + AAC, yuv420p)",
            v_stream.get("codec_name") == "h264" and a_stream.get("codec_name") == "aac" and v_stream.get("pix_fmt") == "yuv420p",
            f"v={v_stream.get('codec_name')}/{v_stream.get('pix_fmt')}, a={a_stream.get('codec_name')}"
        ),
        (
            "2. Resolution & Aspect Ratio Match",
            int(v_stream.get("width", 0)) == expected_w and int(v_stream.get("height", 0)) == expected_h,
            f"expected={expected_w}x{expected_h}, actual={v_stream.get('width')}x{v_stream.get('height')}"
        ),
        (
            "3. Constant Frame Rate (30/1 CFR)",
            v_stream.get("r_frame_rate") == "30/1" and v_stream.get("avg_frame_rate") == "30/1",
            f"r_frame_rate={v_stream.get('r_frame_rate')}, avg_frame_rate={v_stream.get('avg_frame_rate')}"
        ),
        (
            "4. Exact Container Duration (<= 60s Hard Cap)",
            abs(fmt_dur - target_dur) <= 0.12 and fmt_dur <= (MAX_DURATION + 0.12),
            f"target={target_dur}.000s, actual={fmt_dur:.3f}s"
        ),
        (
            "5. Exact Frame Count (DUR * 30)",
            actual_frames == expected_frames,
            f"expected={expected_frames} frames, actual={actual_frames} frames"
        ),
        (
            "6. Zero Black Frames & Active Visual Ending",
            len(black_hits) == 0 and last_frame_valid,
            f"black_segments={len(black_hits)}, last_frame_active={last_frame_valid}"
        ),
        (
            "7. Audio Presence & Anti-Clipping Protection",
            mean_vol > -45.0 and max_vol <= 0.0,
            f"mean_volume={mean_vol:.1f} dB, max_volume={max_vol:.1f} dB"
        ),
    ]

    passed_count = sum(1 for _, passed, _ in tests if passed)
    for name, passed, detail in tests:
        log_fn(f"  {'[PASS]' if passed else '[FAIL]'} {name} -> ({detail})")

    log_fn(f"[VERIFICATION SUMMARY] Passed {passed_count}/{len(tests)} checks. Sheet: check/verification_contact_sheet.jpg")
    if passed_count < len(tests):
        raise AssertionError(f"Verification Suite failed {len(tests) - passed_count} check(s).")

    return {"passed": passed_count, "total": len(tests), "duration": fmt_dur, "frames": actual_frames}


# ==========================================
# GRAPHICAL USER INTERFACE (v7.0 — UNIVERSAL PROCEDURAL VECTOR STUDIO)
# ==========================================
class NalarkaStudioApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Nalarka Motion Graphics Studio v7.0 (Universal Procedural Vector Engine — Topic-Agnostic)")
        self.geometry("1240x950")
        self.minsize(1080, 840)

        self.log_queue = queue.Queue()
        self.current_script_data = None
        self.current_plan = None
        self.current_namespace: Dict[str, Any] = {}
        self.current_words = []
        self.ref_library_dir = DEFAULT_REF_LIBRARY_DIR

        self._build_ui()
        self._load_config()
        self._refresh_reference_library()
        self.after(100, self._poll_log_queue)

    def _build_ui(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- SECTION 1: AI ENDPOINT & DUAL MODEL CONFIGURATION ---
        ai_box = ttk.LabelFrame(main_frame, text=" 1. Custom OpenAI API Configuration (oao.ai.id Model Presets) ", padding=6)
        ai_box.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(ai_box, text="Base URL:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=2)
        self.base_url_var = tk.StringVar(value="https://oao.ai.id/v1")
        ttk.Entry(ai_box, textvariable=self.base_url_var, width=34).grid(row=0, column=1, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="Vector Artist Model (Stage 5):").grid(row=0, column=2, sticky=tk.W, padx=4, pady=2)
        self.coder_model_var = tk.StringVar(value="claude-fable-5")
        ttk.Combobox(ai_box, textvariable=self.coder_model_var, values=OAO_VISUAL_CODER_MODELS, width=22).grid(row=0, column=3, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="API Key:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=2)
        self.api_key_var = tk.StringVar(value="")
        ttk.Entry(ai_box, textvariable=self.api_key_var, show="*", width=34).grid(row=1, column=1, sticky=tk.EW, padx=4, pady=2)

        ttk.Label(ai_box, text="Script Model (Stage 1):").grid(row=1, column=2, sticky=tk.W, padx=4, pady=2)
        self.script_model_var = tk.StringVar(value="deepseek-v4-flash")
        ttk.Combobox(ai_box, textvariable=self.script_model_var, values=OAO_SCRIPT_MODELS, width=22).grid(row=1, column=3, sticky=tk.EW, padx=4, pady=2)

        btn_frame_ai = ttk.Frame(ai_box)
        btn_frame_ai.grid(row=0, column=4, rowspan=2, sticky=tk.E, padx=4)
        ttk.Button(btn_frame_ai, text="Save Config", command=self._save_config).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_frame_ai, text="Test Vector Model", command=self._test_ai_connection).pack(side=tk.LEFT, padx=3)
        ai_box.columnconfigure(1, weight=2)
        ai_box.columnconfigure(3, weight=1)

        # --- SECTION 2A: TOPIC & STRUCTURED VOICEOVER SCRIPT ---
        script_box = ttk.LabelFrame(main_frame, text=" 2A. Topic & Structured Voiceover Script ([Hook], [Inti 1..3], [Penutup]) ", padding=6)
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

        self.script_info_var = tk.StringVar(
            value="Structured Script ([Hook], [Inti 1..3], [Penutup] tags are automatically stripped before TTS):"
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

    def _load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                self.base_url_var.set(cfg.get("base_url", self.base_url_var.get()))
                self.api_key_var.set(cfg.get("api_key", ""))
                self.coder_model_var.set(cfg.get("coder_model", cfg.get("model", "claude-fable-5")))
                self.script_model_var.set(cfg.get("script_model", "deepseek-v4-flash"))
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
                self.log(f"[CONFIG] Loaded configuration from {CONFIG_FILE}")
            except Exception as e:
                self.log(f"[CONFIG WARN] Could not read {CONFIG_FILE}: {e}")

    def _save_config(self):
        cfg = {
            "base_url": self.base_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "coder_model": self.coder_model_var.get().strip(),
            "script_model": self.script_model_var.get().strip(),
            "tts_provider": self.tts_provider_var.get().strip(),
            "elevenlabs_api_key": self.el_api_key_var.get().strip(),
            "elevenlabs_voice_id": self.el_voice_var.get().strip(),
            "elevenlabs_model_id": self.el_model_var.get().strip(),
            "elevenlabs_stability": float(self.el_stability_var.get()),
            "elevenlabs_similarity": float(self.el_sim_var.get()),
            "elevenlabs_style": float(self.el_style_var.get()),
            "ref_library_dir": self.ref_library_dir,
            "selected_reference": self.ref_choice_var.get().strip(),
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)

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
        threading.Thread(target=self._worker_generate_script, args=(topic,), daemon=True).start()

    def _worker_generate_script(self, topic: str):
        try:
            self.set_progress(15, f"Generating structured script via '{self.script_model_var.get()}'...")
            data = generate_script(
                self.base_url_var.get(),
                self.api_key_var.get(),
                self.script_model_var.get(),
                topic,
                self.lang_var.get(),
                self.log
            )
            self.current_script_data = data
            tagged_text = data["tagged_text"]
            clean_text = data["clean_tts_text"]
            word_count = len(clean_text.split())
            est_vo = round(word_count / 2.5, 1)
            est_vid = math.ceil(est_vo + 2.5)

            self.log(f"[SCRIPT] Estimate: ~{word_count} spoken words ≈ {est_vo}s voiceover ≈ {est_vid}s total video")
            self.log(f"[STRUCTURED SCRIPT OUTPUT]:\n{tagged_text}\n")

            def _update_ui():
                self.script_text.delete("1.0", tk.END)
                self.script_text.insert(tk.END, tagged_text)
                self.script_info_var.set(
                    f"Structured Script (~{word_count} spoken words | Est. Voice ~{est_vo}s | Est. Video ~{est_vid}s — [Tags] stripped for TTS):"
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
                log_fn=self.log
            )
        else:
            edge_voice = "en-US-ChristopherNeural" if "English" in self.lang_var.get() else "id-ID-ArdiNeural"
            generate_edge_tts_fallback(raw_editor_text, out_vo, edge_voice, self.log)

    def _start_generate_tts(self):
        self._save_config()
        raw_text = self.script_text.get("1.0", tk.END).strip()
        if not raw_text:
            messagebox.showwarning("Empty Script", "The script editor is empty. Generate or type a script first.")
            return
        out_vo = self.vo_path_var.get().strip() or "Suara_Voiceover.mp3"
        self.btn_tts.config(state=tk.DISABLED)
        threading.Thread(target=self._worker_tts, args=(raw_text, out_vo), daemon=True).start()

    def _worker_tts(self, raw_text: str, out_vo: str):
        try:
            self.set_progress(25, f"Synthesizing voiceover via {self.tts_provider_var.get()}...")
            self._synthesize_voiceover_now(raw_text, out_vo)
            dur = get_audio_duration(out_vo, self.log)
            total_dur = math.ceil(dur + 2.5)
            self.log(f"[AUDIO INFO] Voiceover = {dur:.2f}s -> Locked Video Target Duration = {total_dur}s")
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
        threading.Thread(target=self._worker_reroll_scene, args=(beat_idx,), daemon=True).start()

    def _worker_reroll_scene(self, beat_idx: int):
        try:
            beat = self.current_plan["scenes"][beat_idx]
            user_note = self.reroll_note_var.get().strip() or "Draw a completely fresh, detailed multi-part flat-vector illustration for this beat."
            if self.reroll_note_var.get().strip():
                beat["visual_blueprint"] = f"{beat.get('visual_blueprint', '')} ({user_note})"

            self.set_progress(25, f"Regenerating Beat #{beat_idx+1:02d} via '{self.coder_model_var.get()}'...")
            new_fn = rewrite_single_beat_function_with_ai(
                self.base_url_var.get(),
                self.api_key_var.get(),
                self.coder_model_var.get(),
                self.topic_var.get().strip(),
                beat_idx,
                beat,
                user_note,
                self.log
            )
            if new_fn:
                self.current_namespace[f"beat_{beat_idx+1:02d}"] = new_fn

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
                progress_fn=self.set_progress
            )
            run_verification_suite(out_mp4, total_dur, width, height, self.log)
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
        threading.Thread(target=self._worker_verify_only, args=(out_mp4,), daemon=True).start()

    def _worker_verify_only(self, out_mp4: str):
        try:
            self.set_progress(50, "Running 7-Point Verification Suite...")
            ref_profile = analyze_reference_video(self._get_selected_reference_path(), self.log)
            w, h = self._resolve_dimensions(ref_profile)
            res = run_verification_suite(out_mp4, None, w, h, self.log)
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
        threading.Thread(target=self._worker_full_render, daemon=True).start()

    def _worker_full_render(self):
        try:
            vo_path = self.vo_path_var.get().strip()
            raw_script_editor = self.script_text.get("1.0", tk.END).strip()
            _, parsed_sections = parse_tagged_script(raw_script_editor)

            if not os.path.exists(vo_path):
                if not raw_script_editor:
                    raise FileNotFoundError(f"Voiceover file '{vo_path}' not found and script editor is empty.")
                self.log(f"[AUTO-TTS] '{vo_path}' not found. Synthesizing via {self.tts_provider_var.get()}...")
                self.set_progress(8, "Generating voiceover audio...")
                self._synthesize_voiceover_now(raw_script_editor, vo_path)

            self.set_progress(15, "Measuring voiceover duration (ffprobe)...")
            vo_dur = get_audio_duration(vo_path, self.log)
            total_dur = math.ceil(vo_dur + 2.5)
            total_frames = total_dur * FPS
            self.log(f"[DURATION] Voiceover = {vo_dur:.2f}s | Locked Video Target = {total_dur}s ({total_frames} frames @ {FPS}fps)")

            if total_dur > MAX_DURATION:
                raise ValueError(
                    f"HARD STOP: Calculated video duration ({total_dur}s) exceeds the {MAX_DURATION}s maximum limit! "
                    "Please shorten the script or voiceover."
                )

            self.set_progress(22, "Loading Selected Reference from Library...")
            ref_path = self._get_selected_reference_path()
            ref_profile = analyze_reference_video(ref_path, self.log)
            width, height = self._resolve_dimensions(ref_profile)

            self.set_progress(32, "Extracting word timestamps (Faster-Whisper)...")
            words = extract_word_timestamps(vo_path, self.whisper_var.get(), self.log)
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
                max_retries=2
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
                progress_fn=self.set_progress
            )

            self.set_progress(96, "Running 7-Point Verification Suite...")
            ver_res = run_verification_suite(out_mp4, total_dur, width, height, self.log)
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
    app = NalarkaStudioApp()
    app.mainloop()