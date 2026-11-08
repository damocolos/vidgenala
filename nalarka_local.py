import os
import re
import sys
import ast
import json
import math
import time
import wave
import queue
import shutil
import asyncio
import hashlib
import logging
import threading
import subprocess
import traceback
import multiprocessing
import urllib.request
import urllib.error
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
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
MAX_DURATION = 60            # "Standard" cap from the Nalarka spec (default)
MIN_DURATION_CAP = 15        # lowest cap the UI accepts
HARD_MAX_DURATION = 900      # absolute ceiling for Extended / long-video mode
DEFAULT_MAX_DURATION = MAX_DURATION
DURATION_PRESETS = ["60", "90", "120", "180", "300", "600"]

SPEECH_WORDS_PER_SEC = 2.5   # spoken pace used for all duration estimates
INTRO_OUTRO_SECONDS = 2.5    # ~1.0s lead-in + ~1.5s tail (spec: voiceover + 2.5s, rounded up)
MIN_BEAT_SECONDS = 1.2       # shortest beat the storyboard sanitiser will keep
PASS1_WINDOW_SECONDS = 90.0  # storyboard is planned in windows of this length
PASS2_BATCH_BEATS = 6        # beats of Python code requested per LLM call
PASS2_PARALLEL_CALLS = 3     # concurrent code-generation calls
EL_CHUNK_CHARS = 4500        # ElevenLabs text per request (keeps well under the 10k model limit)

# --- AI providers ---------------------------------------------------------
PROVIDER_CUSTOM = "custom"
PROVIDER_OPENROUTER = "openrouter"
PROVIDER_LABELS = {
    PROVIDER_CUSTOM: "Custom OpenAI-compatible (oao.ai.id)",
    PROVIDER_OPENROUTER: "OpenRouter",
}
PROVIDER_DEFAULT_BASE_URL = {
    PROVIDER_CUSTOM: "https://oao.ai.id/v1",
    PROVIDER_OPENROUTER: "https://openrouter.ai/api/v1",
}
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Starting points only. Use "Fetch OpenRouter Models" to load the live catalogue
# (model slugs on OpenRouter change over time and this list is not verified live).
OPENROUTER_VISUAL_CODER_MODELS = [
    "anthropic/claude-opus-5.5",
    "anthropic/claude-sonnet-5.5",
    "deepseek/deepseek-chat",
    "moonshotai/kimi-k2",
    "qwen/qwen3-coder",
    "google/gemini-2.5-pro",
    "openai/gpt-5",
]

OPENROUTER_SCRIPT_MODELS = [
    "anthropic/claude-sonnet-5.5",
    "anthropic/claude-haiku-5.5",
    "deepseek/deepseek-chat",
    "google/gemini-2.5-flash",
    "openai/gpt-5-mini",
]

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
# The HTTP stack logs every request at DEBUG; that floods the log file and can echo request details.
for _noisy in ("openai", "httpx", "httpcore", "PIL", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


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

    for i in range(1, 13):
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


def script_fingerprint(raw_editor_text: str) -> str:
    """Stable hash of the spoken text (tags/whitespace ignored) used to match a voiceover file to its script."""
    clean, _ = parse_tagged_script(raw_editor_text)
    return hashlib.sha1(re.sub(r"\s+", " ", clean).strip().encode("utf-8")).hexdigest()


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


# ==========================================
# AI CLIENT (OpenAI-compatible: custom endpoint + OpenRouter)
# ==========================================
def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def make_client(base_url: str, api_key: str, timeout: float = 300.0, max_retries: int = 3) -> OpenAI:
    """Builds an OpenAI-SDK client. OpenRouter gets its optional attribution headers automatically."""
    key = (api_key or "").strip()
    if not key:   # lets users keep keys out of config_nalarka.json
        key = os.environ.get("OPENROUTER_API_KEY" if is_openrouter(base_url) else "NALARKA_API_KEY", "").strip()
    if not key:
        raise ValueError(
            "API key is empty. Enter the key for the selected provider in Section 1 "
            f"(or set the {'OPENROUTER_API_KEY' if is_openrouter(base_url) else 'NALARKA_API_KEY'} environment variable)."
        )
    kwargs: Dict[str, Any] = {}
    if is_openrouter(base_url):
        kwargs["default_headers"] = {
            "HTTP-Referer": "http://localhost",
            "X-Title": "Nalarka Motion Graphics Studio",
        }
    return OpenAI(
        base_url=base_url.strip(),
        api_key=key,
        timeout=timeout,
        max_retries=max_retries,
        **kwargs,
    )


def fetch_openrouter_models(api_key: str, log_fn) -> List[Dict[str, Any]]:
    """Loads the live OpenRouter catalogue (the endpoint is public; the key is sent only if present)."""
    headers = {"Accept": "application/json"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    req = urllib.request.Request(OPENROUTER_MODELS_URL, headers=headers, method="GET")
    log_fn("[OPENROUTER] Fetching model catalogue from GET /api/v1/models ...")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    models: List[Dict[str, Any]] = []
    for m in data.get("data", []):
        mid = m.get("id")
        if not mid:
            continue
        pricing = m.get("pricing") or {}
        free = str(pricing.get("prompt", "1")) in ("0", "0.0") and str(pricing.get("completion", "1")) in ("0", "0.0")
        models.append({"id": mid, "name": m.get("name", mid), "context": m.get("context_length"), "free": free})
    models.sort(key=lambda x: x["id"])
    return models


def friendly_api_error(err: Exception, base_url: str) -> str:
    """One-line, actionable explanation for the HTTP errors people actually hit (OpenRouter included)."""
    code = getattr(err, "status_code", None)
    host = "OpenRouter" if is_openrouter(base_url) else "the API endpoint"
    hints = {
        400: "the request was rejected (bad parameter or model id). Check the model name.",
        401: f"{host} rejected the API key. Re-check it in Section 1.",
        402: f"{host} reports insufficient credits. Add credits or pick a ':free' / cheaper model.",
        403: "the key or account is not allowed to use this model (or the request was moderated).",
        404: "model not found / no provider is serving it. Use 'Fetch OpenRouter Models' to get a valid id." if is_openrouter(base_url)
             else "model or endpoint not found. Check the Base URL and model name.",
        408: "the request timed out. Try again or choose a faster model.",
        429: "rate limited. Wait a moment, or use a paid model (free models have tight limits).",
        502: "the upstream model provider failed. Retry, or choose another model.",
        503: "no provider is available for this model right now. Retry later or choose another model.",
    }
    base = f"{type(err).__name__}: {err}"
    return f"{base}  ->  {hints[code]}" if code in hints else base


def _chat_text(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_fn,
    tag: str,
    temperature: Optional[float] = 0.40,
    timeout: float = 300.0,
    max_tokens: Optional[int] = None,
    min_chars: int = 30,
) -> Tuple[str, Optional[str]]:
    """
    One chat completion, streamed first with a non-streaming fallback.
    Returns (text, finish_reason). The fallback now also triggers when the stream died half-way
    (the old code silently accepted a truncated partial response as complete).
    Parameters some models reject (temperature, max_tokens) are dropped automatically on a 400.
    """
    client = make_client(base_url, api_key, timeout=timeout)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    dropped = set()

    def _create(stream: bool, temp: Optional[float]):
        for _ in range(3):
            kw: Dict[str, Any] = {"model": model.strip(), "messages": messages, "stream": stream}
            if temp is not None and "temperature" not in dropped:
                kw["temperature"] = temp
            if max_tokens and "max_tokens" not in dropped:
                kw["max_tokens"] = max_tokens
            try:
                return client.chat.completions.create(**kw)
            except Exception as err:
                msg = str(err).lower()
                if "temperature" in msg and "temperature" not in dropped:
                    dropped.add("temperature")
                    log_fn(f"[{tag} WARN] '{model}' rejected 'temperature'; retrying without it.")
                    continue
                if ("max_tokens" in msg or "max_completion_tokens" in msg) and "max_tokens" not in dropped:
                    dropped.add("max_tokens")
                    log_fn(f"[{tag} WARN] '{model}' rejected 'max_tokens'; retrying without it.")
                    continue
                explained = friendly_api_error(err, base_url)
                log_fn(f"[{tag} ERROR] {explained}")
                raise RuntimeError(explained) from err
        raise RuntimeError("Chat completion kept failing after parameter fallbacks.")

    log_fn(f"[{tag}] Calling {base_url.strip()} | model={model.strip()} (streaming)")
    t0 = time.time()
    chunks: List[str] = []
    finish: Optional[str] = None
    stream_err: Optional[Exception] = None
    try:
        stream = _create(True, temperature)
        for chunk in stream:
            # OpenRouter (and some gateways) report a failure AFTER the HTTP 200 was sent: a final chunk with a
            # top-level `error` and finish_reason == "error". Treat that as a failed stream, never as a short answer.
            chunk_err = getattr(chunk, "error", None)
            if chunk_err:
                raise RuntimeError(f"provider error mid-stream: {chunk_err}")
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            choice = choices[0]
            content = getattr(getattr(choice, "delta", None), "content", None)
            if content:
                chunks.append(content)
            fr = getattr(choice, "finish_reason", None)
            if fr == "error":
                raise RuntimeError("provider ended the stream with finish_reason='error'")
            if fr:
                finish = fr
    except Exception as err:
        stream_err = err
        log_fn(f"[{tag} WARN] Stream from '{model}' failed ({err}). Falling back to non-streaming...")

    raw = "".join(chunks).strip()
    if stream_err is not None or len(raw) < min_chars:
        if stream_err is None:
            log_fn(f"[{tag} WARN] Response from '{model}' was empty/too short. Retrying with stream=False...")
        resp = _create(False, 0.2 if temperature is not None else None)
        if not getattr(resp, "choices", None):
            raise RuntimeError(f"'{model}' returned no choices ({getattr(resp, 'error', None) or 'empty response'}).")
        choice = resp.choices[0]
        raw = (getattr(choice.message, "content", "") or "").strip()
        finish = getattr(choice, "finish_reason", None)
        if finish == "error":
            raise RuntimeError(f"'{model}' finished with finish_reason='error' ({getattr(resp, 'error', None) or 'no detail'}).")

    log_fn(f"[{tag}] Received {len(raw)} chars from '{model}' in {round(time.time() - t0, 2)}s (finish={finish}).")
    return raw, finish


def call_ai_json(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_fn,
    temperature: float = 0.40,
    max_tokens: Optional[int] = None,
    attempts: int = 2,
) -> dict:
    last_err: Optional[Exception] = None
    prompt = user_prompt
    for attempt in range(1, attempts + 1):
        raw, finish = _chat_text(
            base_url, api_key, model, system_prompt, prompt, log_fn, "AI JSON",
            temperature=temperature if attempt == 1 else 0.2,
            timeout=300.0, max_tokens=max_tokens,
        )
        logging.debug(f"RAW AI JSON ({model}) attempt {attempt}:\n{raw}")
        if finish == "length":
            log_fn(f"[AI JSON WARN] '{model}' hit its output limit (finish_reason=length); JSON is probably cut off.")
        try:
            return extract_json_safely(raw, log_fn)
        except ValueError as err:
            last_err = err
            if attempt < attempts:
                log_fn(f"[AI JSON WARN] Attempt {attempt} unusable ({err}). Retrying once with a stricter reminder...")
                prompt = user_prompt + "\n\nIMPORTANT: reply with ONE complete, valid JSON object only. No prose, no markdown."
    raise ValueError(f"AI did not return usable JSON after {attempts} attempt(s): {last_err}")


def call_ai_python_code(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    log_fn,
    temperature: float = 0.35,
    max_tokens: Optional[int] = 16000,
) -> str:
    raw, finish = _chat_text(
        base_url, api_key, model, system_prompt, user_prompt, log_fn, "AI CODE",
        temperature=temperature, timeout=420.0, max_tokens=max_tokens, min_chars=50,
    )
    if finish == "length":
        log_fn(
            f"[AI CODE WARN] '{model}' hit its output limit. Complete functions are kept; "
            "cut-off ones are regenerated by the audit loop."
        )
    return extract_python_code_safely(raw)


# ==========================================
# STAGE 1: GENERATE STRUCTURED VOICEOVER SCRIPT (duration-aware)
# ==========================================
def script_word_budget(max_duration: int) -> Tuple[int, int]:
    """(min_words, max_words) of spoken script that fit inside `max_duration` seconds of video.
    60s -> 75..125 words, matching the original 70-125 word guidance."""
    hi = int((max_duration - 3) * 2.2)
    lo = int(hi * 0.6)
    return lo, hi


def script_section_count(max_duration: int) -> int:
    """Number of [Inti] sections. 60s -> 3 (original layout); longer videos get more."""
    return max(3, min(12, int(round(max_duration / 20.0))))


def estimate_video_seconds(word_count: int) -> Tuple[float, int]:
    vo = word_count / SPEECH_WORDS_PER_SEC
    return round(vo, 1), int(math.ceil(vo + INTRO_OUTRO_SECONDS))


TTS_OVERSHOOT_TOLERANCE = 1.12   # the 2.5 words/s estimate is approximate; real voices run a little slower or faster


def check_script_fits(raw_editor_text: str, max_duration: int, log_fn) -> int:
    """
    Run BEFORE paying for TTS. The old flow only discovered 'video too long' after the audio was synthesised
    (spent ElevenLabs credits) and then hard-stopped. Returns the estimated video seconds; raises when the
    script is clearly too long for the cap, warns when it is borderline.
    """
    clean, _ = parse_tagged_script(raw_editor_text)
    words = len(clean.split())
    est_vo, est_vid = estimate_video_seconds(words)
    if est_vid > max_duration * TTS_OVERSHOOT_TOLERANCE:
        raise ValueError(
            f"Script is ~{words} words = ~{est_vid}s of video, over the {max_duration}s cap "
            f"(tolerance {int(max_duration * TTS_OVERSHOOT_TOLERANCE)}s). Shorten it or raise 'Max video duration' "
            "in Section 2A BEFORE generating audio (no TTS credits were used)."
        )
    if est_vid > max_duration:
        log_fn(f"[SCRIPT WARN] Estimated {est_vid}s is slightly over the {max_duration}s cap; it may still fit once the real audio length is known.")
    return est_vid


def generate_script(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    script_lang: str,
    log_fn,
    max_duration: int = DEFAULT_MAX_DURATION,
) -> dict:
    is_id = "Indonesian" in script_lang
    lang_instruction = (
        "Tulis naskah voiceover dalam Bahasa Indonesia lisan yang natural, mengalir, dan kaya gambaran objek visual nyata."
        if is_id
        else "Write the voiceover script in natural, vivid spoken English."
    )
    lo, hi = script_word_budget(max_duration)
    n_inti = script_section_count(max_duration)

    inti_lines = [
        "   - `inti_1`: Fakta utama atau mekanisme fisik/biologis/psikologis pertama secara konkret.",
        "   - `inti_2`: Mekanisme kedua atau perbandingan dua jalur/faktor secara visual.",
        "   - `inti_3`: Fakta penelitian atau paradoks menarik.",
    ]
    for i in range(4, n_inti + 1):
        inti_lines.append(
            f"   - `inti_{i}`: Fakta, contoh, atau mekanisme berikutnya yang konkret dan BERBEDA dari bagian sebelumnya, "
            "dengan objek visual yang bisa digambar."
        )
    template_lines = ['  "hook": "..."'] + [f'  "inti_{i}": "..."' for i in range(1, n_inti + 1)] + ['  "penutup": "..."']
    json_template = "{\n" + ",\n".join(template_lines) + "\n}"

    system_prompt = (
        "Kamu adalah penulis naskah motion graphics explainer senior bergaya Nalarka.\n"
        f"{lang_instruction}\n"
        "Aturan ketat:\n"
        f"1. Total panjang gabungan seluruh bagian WAJIB antara {lo} hingga {hi} kata "
        f"(~{SPEECH_WORDS_PER_SEC} kata/detik, total video <= {max_duration} detik). Jangan menambah kalimat hanya demi mengisi durasi.\n"
        f"2. Bagi naskah menjadi {n_inti + 2} bagian yang jelas:\n"
        "   - `hook`: Kalimat pembuka 3 detik pertama yang menyebutkan 2-3 reaksi/kejadian nyata lalu ditutup pertanyaan memikat.\n"
        + "\n".join(inti_lines) + "\n"
        "   - `penutup`: Kesimpulan yang membalik cara pandang penonton dan kalimat penutup yang menancap di ingatan.\n"
        "3. Pakai kalimat pendek, bahasa lisan. Tulis angka, singkatan, dan istilah asing sedemikian rupa sehingga tidak salah dibaca mesin suara (TTS).\n"
        "4. Jangan mengarang fakta, angka, atau hasil penelitian. Pakai klaim yang aman dan umum.\n"
        "5. Kembalikan HANYA JSON valid dengan struktur persis ini:\n"
        f"{json_template}"
    )

    def _structure(data: dict) -> dict:
        s: Dict[str, Any] = {"hook": str(data.get("hook") or "").strip()}
        for i in range(1, 13):
            val = str(data.get(f"inti_{i}") or data.get(f"point_{i}") or "").strip()
            if val:
                s[f"inti_{i}"] = val
        s["penutup"] = str(data.get("penutup") or data.get("outro") or "").strip()
        return s

    def _words(s: dict) -> int:
        return len(parse_tagged_script(format_sections_to_tagged_text(s))[0].split())

    data = call_ai_json(base_url, api_key, model, system_prompt, f"Topik: {topic}", log_fn)
    structured = _structure(data)
    words = _words(structured)

    if words > int(hi * 1.1):
        log_fn(f"[SCRIPT] {words} words exceeds the {hi}-word budget for {max_duration}s. Asking the model to shorten once...")
        try:
            retry_user = (
                f"Topik: {topic}\n\nNaskah di bawah terlalu panjang ({words} kata). "
                f"Persingkat menjadi MAKSIMAL {hi} kata total tanpa mengubah struktur bagian atau menghapus fakta kunci. "
                f"Kembalikan JSON dengan struktur yang sama.\n\n{json.dumps(structured, ensure_ascii=False)}"
            )
            shorter = _structure(call_ai_json(base_url, api_key, model, system_prompt, retry_user, log_fn, temperature=0.3))
            if 0 < _words(shorter) < words:
                structured, words = shorter, _words(shorter)
        except Exception as err:
            log_fn(f"[SCRIPT WARN] Shorten retry failed ({err}); keeping the first draft.")
    if words > hi:
        log_fn(f"[SCRIPT WARN] Script is {words} words; budget is {hi}. Trim it in the editor before rendering if the cap is exceeded.")

    structured["tagged_text"] = format_sections_to_tagged_text(structured)
    clean_text, sections = parse_tagged_script(structured["tagged_text"])
    structured["clean_tts_text"] = clean_text
    structured["sections"] = sections
    structured["word_budget"] = (lo, hi)
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


def split_text_for_tts(text: str, max_chars: int = EL_CHUNK_CHARS) -> List[str]:
    """Splits script text into chunks <= max_chars on paragraph (then sentence, then word) boundaries."""
    def _hard_split(piece: str) -> List[str]:
        out, cur = [], ""
        for w in piece.split():
            if cur and len(cur) + 1 + len(w) > max_chars:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}" if cur else w
        if cur:
            out.append(cur)
        return out

    chunks: List[str] = []
    cur = ""
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        pieces = [para]
        if len(para) > max_chars:
            pieces = []
            for sent in re.split(r"(?<=[.!?])\s+", para):
                pieces.extend(_hard_split(sent) if len(sent) > max_chars else [sent])
        for piece in pieces:
            if cur and len(cur) + 2 + len(piece) > max_chars:
                chunks.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def concat_audio_files(parts: List[str], output_path: str, log_fn):
    list_path = output_path + ".concat.txt"
    with open(list_path, "w", encoding="utf-8") as f:
        for p in parts:
            safe = os.path.abspath(p).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", output_path,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        os.remove(list_path)
    except OSError:
        pass
    if res.returncode != 0:
        raise RuntimeError(f"ffmpeg could not join the voiceover parts: {res.stderr[-600:]}")
    for p in parts:
        try:
            os.remove(p)
        except OSError:
            pass
    log_fn(f"[TTS] Joined {len(parts)} voiceover parts -> {output_path}")


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
    log_fn,
    language_code: Optional[str] = "id",
):
    if not xi_api_key or not xi_api_key.strip():
        raise ValueError("ElevenLabs API Key is missing! Please enter your xi-api-key in Section 2B.")

    clean_voice_id = voice_id.strip()
    if "(" in clean_voice_id and clean_voice_id.endswith(")"):
        clean_voice_id = clean_voice_id.split("(")[-1].rstrip(")").strip()

    cleaned_text = prepare_text_for_elevenlabs(raw_editor_text, add_ssml_breaks=add_ssml_breaks)
    if not cleaned_text:
        raise ValueError("Script text is empty after removing section tags.")

    # Long scripts are sent in chunks (ElevenLabs caps characters per request) and joined with ffmpeg.
    chunks = split_text_for_tts(cleaned_text)
    multi = len(chunks) > 1
    if multi:
        log_fn(f"[ELEVENLABS] Long script ({len(cleaned_text)} chars) -> {len(chunks)} chunks.")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{clean_voice_id}?output_format=mp3_44100_128"
    headers = {
        "xi-api-key": xi_api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    part_paths: List[str] = []
    t0 = time.time()
    for idx, chunk in enumerate(chunks):
        payload: Dict[str, Any] = {
            "text": chunk,
            "model_id": model_id.strip(),
            "voice_settings": {
                "stability": round(float(stability), 2),
                "similarity_boost": round(float(similarity_boost), 2),
                "style": round(float(style), 2),
                "use_speaker_boost": bool(use_speaker_boost),
            },
        }
        if language_code and any(k in model_id.lower() for k in ("flash", "turbo")):
            payload["language_code"] = language_code
        if multi:  # keeps intonation continuous across chunk boundaries
            if idx > 0:
                payload["previous_text"] = chunks[idx - 1][-300:]
            if idx < len(chunks) - 1:
                payload["next_text"] = chunks[idx + 1][:300]

        target = f"{output_path}.part{idx:02d}.mp3" if multi else output_path
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                audio_bytes = resp.read()
            with open(target, "wb") as f:
                f.write(audio_bytes)
            part_paths.append(target)
            log_fn(f"[ELEVENLABS] Chunk {idx + 1}/{len(chunks)} saved ({len(audio_bytes)} bytes).")
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            log_fn(f"[ELEVENLABS HTTP ERROR {err.code}] {err_body}")
            raise RuntimeError(f"ElevenLabs TTS failed (HTTP {err.code}): {err_body}") from err

    if multi:
        concat_audio_files(part_paths, output_path, log_fn)
    log_fn(f"[ELEVENLABS SUCCESS] Voiceover saved to '{output_path}' in {round(time.time() - t0, 2)}s.")


async def _edge_tts_save(text: str, output_path: str, voice: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate="+2%", pitch="-3Hz")
    await communicate.save(output_path)


def generate_edge_tts_fallback(raw_editor_text: str, output_path: str, voice: str, log_fn):
    cleaned_text = prepare_text_for_elevenlabs(raw_editor_text, add_ssml_breaks=False)
    log_fn(f"[EDGE-TTS] Synthesizing cleaned text ({len(cleaned_text)} chars) with voice '{voice}'...")
    asyncio.run(_edge_tts_save(cleaned_text, output_path, voice))
    log_fn(f"[EDGE-TTS] Saved voiceover to: {output_path}")


BGM_LOOP_SECONDS = 32


def generate_synthetic_ambient_bgm(output_wav: str, duration_sec: int, bpm: int, log_fn) -> str:
    """
    Writes a seamless 32s ambient loop (every component completes whole cycles in the loop length).
    The render pipeline loops it with `-stream_loop -1`, so memory use no longer grows with video length
    (the old version allocated float64 arrays for the full duration: ~1.5 GB at 10 minutes).
    `duration_sec` is kept for API compatibility.
    """
    sr = 48000
    L = BGM_LOOP_SECONDS
    t = np.arange(sr * L, dtype=np.float64) / sr

    def q(f: float) -> float:  # snap to a whole number of cycles per loop
        return max(1.0 / L, round(f * L) / L)

    freqs = [110.0, 164.81, 220.0, 261.63, 329.63, 392.00]
    amps = [0.22, 0.16, 0.18, 0.14, 0.10, 0.08]
    signal_l = np.zeros_like(t)
    signal_r = np.zeros_like(t)

    for idx, (f, a) in enumerate(zip(freqs, amps)):
        mod = 1.0 + 0.15 * np.sin(2.0 * np.pi * q(0.12 + idx * 0.04) * t)
        detune = 1.002 if idx % 2 == 0 else 0.998
        signal_l += a * mod * np.sin(2.0 * np.pi * q(f) * t)
        signal_r += a * mod * np.sin(2.0 * np.pi * q(f * detune) * t + 0.4)

    beat_freq = q(bpm / 60.0)
    beat_env = np.power(0.5 * (1.0 + np.sin(2.0 * np.pi * beat_freq * t - np.pi / 2.0)), 4.0)
    pulse = 0.15 * beat_env * np.sin(2.0 * np.pi * q(82.41) * t)
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
    log_fn(f"[BGM] Seamless {L}s ambient loop written to {output_wav} (looped to video length by ffmpeg).")
    return output_wav


# ==========================================
# STAGE 3 & 4: AUDIO ANALYSIS & SINGLE-FOLDER REFERENCE LIBRARY
# ==========================================
def get_audio_duration(audio_path: str, log_fn) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path]
    dur = float(subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT).strip())
    log_fn(f"[FFPROBE] Exact voiceover duration: {dur:.3f}s")
    return dur


def extract_word_timestamps(audio_path: str, whisper_size: str, log_fn, language: Optional[str] = None) -> List[dict]:
    """`language` ("id"/"en") skips Whisper's auto-detect, which can mislabel Indonesian speech and degrade the transcript."""
    from faster_whisper import WhisperModel
    log_fn(f"[WHISPER] Loading Faster-Whisper model ('{whisper_size}', int8 CPU, language={language or 'auto'})...")
    t0 = time.time()
    whisper_model = WhisperModel(whisper_size, device="cpu", compute_type="int8")
    segments, _ = whisper_model.transcribe(audio_path, word_timestamps=True, language=language)

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


@lru_cache(maxsize=256)
def load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """Cached: `_fit_font` probes several sizes per text line per frame, and every probe used to re-read
    the TTF from disk (thousands of file loads on a long render)."""
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
            f_px = int(getattr(f, "size", base_sz * self.S))   # bitmap fallback font has no .size
            ty = cur_y + int(f_px * 0.5) + y_off
            self.draw.text((cx, ty), txt, font=f, fill=self.col(col_key), anchor="mm")
            cur_y += int(f_px * 1.16)


# ==========================================
# STAGE 5A & 5B: 2-PASS CREATIVE DIRECTOR & PURE PYTHON VECTOR ARTIST
# ==========================================
PASS1_STORYBOARD_PROMPT = """You are a Senior Motion Graphics Creative Director (Claude Nalarka style).
Given a topic, structured script, and Whisper word timestamps, design a {beats_lo} to {beats_hi} beat visual storyboard covering `{win_start:.1f}` to `{win_end:.1f}` seconds (the whole video is `{total_dur}.0` seconds long).
Voiceover starts at `1.0`s. {window_note}
Every beat must last at least `1.2` seconds, `start` values must strictly increase, and the first beat of this window MUST start exactly at `{win_start:.1f}`.

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
   - `dot_index`: use `1`, `2`, or `3` ONLY with the `"triptych_dots"` layout when the video has exactly three main points; otherwise always `0`.
3. **VARY TRANSITIONS**: `transition` is how the beat is entered. Allowed values: `"cut"`, `"directional_wipe"`, `"whip_pan"`, `"circle_iris"`, `"crossfade"`. Use `"cut"` for most beats and a different transition at major topic changes; never the same non-cut transition twice in a row.

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


PASS2_PYTHON_VECTOR_ARTIST_PROMPT = """You are a Senior Flat-Vector Illustrator & Creative Coder writing pure Python drawing functions `def {first_fn}(v):` through `def {last_fn}(v):` for `VectorKit` (`v`).

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
3. **Sandbox rules (code that breaks them is rejected and you will be asked to rewrite it):**
   - NO `import` statements (`math` is already available as `math`), NO `while` loops, NO names or attributes starting with `__`, NO `open`, `eval`, `exec`, `getattr`, `setattr`.
   - Only these built-ins exist: `range, len, min, max, abs, int, float, str, round, enumerate, zip, list, dict, tuple, sum, pow, bool, any, all, sorted, divmod`.
   - Every function must be SELF-CONTAINED. If you need a helper, define it as a nested `def` INSIDE the beat function. Do not define top-level helpers or constants.
4. Output ONLY a single ` ```python ... ``` ` code block containing `def {first_fn}(v):` through `def {last_fn}(v):`.
"""


VALID_LAYOUTS = ("triptych_dots", "top_text_bottom_art", "top_art_bottom_text", "statement_pill")
VALID_BG_COLORS = ("cream", "orange_bg", "green_bg", "blue_bg")
VALID_TRANSITIONS = ("cut", "directional_wipe", "whip_pan", "circle_iris", "crossfade")


# ==========================================
# SCENE CODE: SPLITTING, SANDBOX VALIDATION & NAMESPACE
# ==========================================
_SAFE_BUILTINS: Dict[str, Any] = {
    "range": range, "len": len, "min": min, "max": max, "abs": abs,
    "int": int, "float": float, "str": str, "round": round,
    "enumerate": enumerate, "zip": zip, "list": list, "dict": dict, "tuple": tuple,
    "sum": sum, "pow": pow, "bool": bool, "any": any, "all": all,
    "sorted": sorted, "divmod": divmod, "reversed": reversed, "set": set,
    "map": map, "filter": filter, "isinstance": isinstance,
    "print": (lambda *a, **k: None),
    "True": True, "False": False, "None": None,
}

_FORBIDDEN_NAMES = {
    "exec", "eval", "open", "compile", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "input", "breakpoint", "exit", "quit", "memoryview", "type", "object", "super",
}


def validate_scene_source(src: str) -> Optional[str]:
    """
    Static gate for AI-written drawing code. Returns an error string, or None when acceptable.
    This is a guard rail against accidents and obvious escapes (imports, dunder access, file/eval calls,
    unbounded `while` loops). It is not a hardened sandbox: only run models you trust.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError as err:
        return f"SyntaxError: {err}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "import statements are not allowed (math is already available)"
        if isinstance(node, ast.While):
            return "'while' loops are not allowed; use 'for ... in range(...)'"
        if isinstance(node, ast.Global):
            return "'global' is not allowed"
        if isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in _FORBIDDEN_NAMES):
            return f"name '{node.id}' is not allowed"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            return f"attribute '{node.attr}' is not allowed"
    return None


def new_scene_namespace() -> Dict[str, Any]:
    return {"__builtins__": dict(_SAFE_BUILTINS), "math": math, "clamp": clamp}


def _trim_to_function_body(chunk: str) -> str:
    """
    Cuts a regex-sliced chunk at the end of its own function. A function body is the `def` line plus every
    following line that is blank or indented; the first unindented code line (a stray `import math`, a
    `print(...)`, prose from the model) belongs to something else. Without this, a stray top-level line
    glued to the PREVIOUS function made that perfectly valid function fail validation.
    A line that merely closes a bracket opened inside the signature/body (`)`, `]`, `}`) is kept.
    """
    lines = chunk.splitlines()
    keep = 1                                  # the `def` line itself
    for idx in range(1, len(lines)):
        ln = lines[idx]
        if not ln.strip():
            continue
        if ln[0] in " \t" or ln.lstrip()[0] in ")]}":
            keep = idx + 1
            continue
        break
    return "\n".join(lines[:keep]).rstrip()


def _salvage_functions(py_code: str, log_fn) -> Tuple[Dict[str, str], Dict[str, str]]:
    """If the whole module fails to parse, keep every top-level function that parses on its own."""
    helpers: Dict[str, str] = {}
    beats: Dict[str, str] = {}
    matches = list(re.finditer(r"^def\s+(\w+)\s*\(", py_code, flags=re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(py_code)
        chunk = _trim_to_function_body(py_code[m.start():end])
        try:
            ast.parse(chunk)
        except SyntaxError as err:
            log_fn(f"[CODE WARN] '{m.group(1)}' skipped, unrecoverable syntax error: {err}")
            continue
        (beats if re.fullmatch(r"beat_\d+", m.group(1)) else helpers)[m.group(1)] = chunk
    return helpers, beats


def split_scene_code(py_code: str, log_fn) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Splits generated code into (helpers, beats), each {name: source}."""
    try:
        tree = ast.parse(py_code)
    except SyntaxError as err:
        log_fn(f"[CODE WARN] Module has a syntax error ({err}); salvaging function by function...")
        return _salvage_functions(py_code, log_fn)

    helpers: Dict[str, str] = {}
    beats: Dict[str, str] = {}
    for i, node in enumerate(tree.body):
        seg = ast.get_source_segment(py_code, node)
        if not seg:
            continue
        if isinstance(node, ast.FunctionDef):
            (beats if re.fullmatch(r"beat_\d+", node.name) else helpers)[node.name] = seg
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            helpers[f"__const_{i}"] = seg
    return helpers, beats


def register_source(namespace: Dict[str, Any], name: str, src: str, log_fn) -> bool:
    err = validate_scene_source(src)
    if err:
        log_fn(f"[CODE REJECTED] {name}: {err}")
        return False
    try:
        exec(src, namespace)
    except Exception as exc:
        log_fn(f"[CODE ERROR] {name} failed to load: {type(exc).__name__}: {exc}")
        return False
    if name.startswith("__const_"):
        return True
    return callable(namespace.get(name))


def new_scene_sources() -> Dict[str, Dict[str, str]]:
    return {"helpers": {}, "beats": {}}


def build_namespace(sources: Dict[str, Dict[str, str]], log_fn) -> Dict[str, Any]:
    ns = new_scene_namespace()
    for name, src in sources.get("helpers", {}).items():
        register_source(ns, name, src, log_fn)
    for name, src in sources.get("beats", {}).items():
        register_source(ns, name, src, log_fn)
    return ns


def add_generated_code(plan: dict, namespace: Dict[str, Any], py_code: str, log_fn) -> List[str]:
    """Parses a code block from the model, loads what is valid, and records the sources on the plan."""
    helpers, beats = split_scene_code(py_code, log_fn)
    store = plan.setdefault("scene_sources", new_scene_sources())
    for name, src in helpers.items():
        if name in store["helpers"] and store["helpers"][name] != src and not name.startswith("__const_"):
            log_fn(f"[CODE WARN] Helper '{name}' redefined by a later batch; the newer version wins.")
        if register_source(namespace, name, src, log_fn):
            store["helpers"][name] = src
    loaded: List[str] = []
    for name, src in beats.items():
        if register_source(namespace, name, src, log_fn):
            store["beats"][name] = src
            loaded.append(name)
    return loaded


def register_beat_source(plan: dict, namespace: Dict[str, Any], name: str, src: str, log_fn) -> bool:
    if register_source(namespace, name, src, log_fn):
        plan.setdefault("scene_sources", new_scene_sources())["beats"][name] = src
        return True
    return False


def write_scene_module_file(plan: dict, path: str = "generated_scenes.py"):
    store = plan.get("scene_sources") or new_scene_sources()
    parts = ["# Auto-generated by Nalarka Motion Graphics Studio v8.1\nimport math\n"]
    parts += list(store.get("helpers", {}).values())
    for name in sorted(store.get("beats", {})):
        parts.append(store["beats"][name])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")


# ==========================================
# STORYBOARD: SANITISING, WINDOWING & BATCHED CODE GENERATION
# ==========================================
def sanitize_scenes(scenes: List[Any], total_dur: float, log_fn) -> List[dict]:
    """
    Repairs the model's storyboard so rendering can trust it: sorted, strictly increasing starts,
    >= MIN_BEAT_SECONDS per beat, valid layout / colour / transition, beat 1 at 0.0 and last beat to total_dur.
    (The old code trusted the raw model output; unsorted or duplicate starts gave zero/negative-length beats.)
    """
    items: List[dict] = []
    for s in scenes or []:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start", 0.0))
        except (TypeError, ValueError):
            continue
        d = dict(s)
        d["start"] = start
        items.append(d)
    if not items:
        raise ValueError("Storyboard contains no usable scenes.")

    items.sort(key=lambda s: s["start"])
    kept: List[dict] = []
    for s in items:
        st = max(0.0, min(s["start"], float(total_dur)))
        if not kept:
            st = 0.0
        elif st < kept[-1]["start"] + MIN_BEAT_SECONDS:
            st = kept[-1]["start"] + MIN_BEAT_SECONDS
        if kept and st > total_dur - MIN_BEAT_SECONDS:
            log_fn(f"[STORYBOARD] Dropped a beat that no longer fits before {total_dur}s.")
            continue
        s["start"] = round(st, 3)
        kept.append(s)

    bg_cycle = ["cream", "orange_bg", "green_bg"]
    for i, s in enumerate(kept):
        s["beat_id"] = i + 1
        s["end"] = round(kept[i + 1]["start"], 3) if i + 1 < len(kept) else float(total_dur)

        layout = str(s.get("layout", "")).lower().strip()
        s["layout"] = layout if layout in VALID_LAYOUTS else "top_text_bottom_art"

        bg = str(s.get("bg_color", "")).lower().strip()
        s["bg_color"] = bg if bg in VALID_BG_COLORS else bg_cycle[i % len(bg_cycle)]

        tr = str(s.get("transition", "cut")).lower().strip()
        s["transition"] = tr if tr in VALID_TRANSITIONS and i > 0 else "cut"

        try:
            dot = int(s.get("dot_index", 0) or 0)
        except (TypeError, ValueError):
            dot = 0
        s["dot_index"] = dot if dot in (0, 1, 2, 3) else 0

        lines: List[dict] = []
        raw_lines = s.get("lines")
        for ln in raw_lines if isinstance(raw_lines, list) else []:
            if isinstance(ln, str):
                ln = {"text": ln}
            if isinstance(ln, dict) and str(ln.get("text", "")).strip():
                lines.append(ln)
        s["lines"] = lines
        if s["layout"] == "statement_pill" and len(lines) < 3:
            s["layout"] = "top_text_bottom_art"

        s["summary"] = str(s.get("summary", "") or f"Beat {i + 1}")
        s["visual_blueprint"] = str(s.get("visual_blueprint", "") or s["summary"])
    return kept


def compress_words(words: List[dict], group: int = 4) -> List[dict]:
    """Phrase-level timestamps (keeps long-video prompts small)."""
    out = []
    for i in range(0, len(words), group):
        chunk = words[i:i + group]
        out.append({
            "start": chunk[0]["start"],
            "end": chunk[-1]["end"],
            "text": " ".join(w["word"] for w in chunk),
        })
    return out


def storyboard_windows(total_dur: int) -> List[Tuple[float, float]]:
    if total_dur <= 120:
        return [(0.0, float(total_dur))]
    n = int(math.ceil(total_dur / PASS1_WINDOW_SECONDS))
    edges = [round(total_dur * i / n, 1) for i in range(n + 1)]
    edges[-1] = float(total_dur)
    return [(edges[i], edges[i + 1]) for i in range(n)]


def plan_storyboard(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    sections: List[Dict[str, str]],
    words: List[dict],
    total_dur: int,
    log_fn,
) -> List[dict]:
    """PASS 1. Short videos use one call; long ones are planned window by window so the model
    never has to emit (or keep coherent) a 60-beat JSON document in one go."""
    windows = storyboard_windows(total_dur)
    scenes: List[dict] = []
    for wi, (w_start, w_end) in enumerate(windows):
        span = w_end - w_start
        beats_lo = max(1, int(math.ceil(span / 7.0)))
        beats_hi = max(beats_lo, int(math.ceil(span / 4.5)))
        note = ""
        if len(windows) > 1:
            note = f"This is part {wi + 1} of {len(windows)} of the video."
            if wi > 0:
                note += " Continue the story after `previous_beats` and do NOT repeat their objects."
            if wi == len(windows) - 1:
                note += " The last beat must hold until the end with a memorable closing visual."

        in_win = [w for w in words if w_start - 0.5 <= w["start"] < w_end]
        stamps = compress_words(in_win) if len(in_win) > 160 else in_win
        payload: Dict[str, Any] = {
            "topic": topic,
            "structured_sections": sections,
            "word_timestamps": stamps,
            "total_duration": total_dur,
            "window": [w_start, w_end],
        }
        if scenes:
            payload["previous_beats"] = [
                {"beat_id": s.get("beat_id", i + 1), "summary": s.get("summary", ""), "visual": str(s.get("visual_blueprint", ""))[:160],
                 "bg_color": s.get("bg_color"), "layout": s.get("layout")}
                for i, s in enumerate(scenes[-8:])
            ]

        p1_sys = PASS1_STORYBOARD_PROMPT.format(
            total_dur=total_dur, win_start=w_start, win_end=w_end,
            beats_lo=beats_lo, beats_hi=beats_hi, window_note=note,
        )
        log_fn(f"[PASS 1/2: CREATIVE DIRECTOR] Window {wi + 1}/{len(windows)} ({w_start:.0f}s-{w_end:.0f}s): planning {beats_lo}-{beats_hi} beats...")
        data = call_ai_json(
            base_url, api_key, model, p1_sys, json.dumps(payload, ensure_ascii=False), log_fn,
            temperature=0.40, max_tokens=16000,
        )
        win_scenes = data.get("scenes", []) if isinstance(data, dict) else []
        if not win_scenes:
            raise ValueError(f"Pass 1 returned an empty 'scenes' list for window {wi + 1}.")
        for s in win_scenes:
            if isinstance(s, dict):
                s.setdefault("start", w_start)
        scenes.extend(win_scenes)
    return scenes


def generate_scene_code_batches(
    base_url: str,
    api_key: str,
    model: str,
    topic: str,
    scenes: List[dict],
    plan: dict,
    namespace: Dict[str, Any],
    log_fn,
):
    """PASS 2. Beats are coded in small batches (several calls in parallel) so output limits are never hit."""
    batches = [(i, min(i + PASS2_BATCH_BEATS, len(scenes))) for i in range(0, len(scenes), PASS2_BATCH_BEATS)]
    log_fn(f"[PASS 2/2: VECTOR ARTIST] Coding {len(scenes)} beats in {len(batches)} batch(es), {PASS2_PARALLEL_CALLS} call(s) in parallel...")

    def _run(span: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[str]]:
        lo, hi = span
        first_fn, last_fn = f"beat_{lo + 1:02d}", f"beat_{hi:02d}"
        p2_sys = PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(first_fn=first_fn, last_fn=last_fn)
        blueprint_list = []
        for s in scenes[lo:hi]:
            blueprint_list.append({
                "function_name": f"beat_{s['beat_id']:02d}",
                "duration": round(float(s["end"]) - float(s["start"]), 2),
                "bg_color": s.get("bg_color", "cream"),
                "layout": s.get("layout", "top_text_bottom_art"),
                "summary": s.get("summary", ""),
                "visual_blueprint": s.get("visual_blueprint", ""),
            })
        prev = ""
        if lo > 0:
            prev = f"For variety, the beat just before this batch showed: {scenes[lo - 1].get('visual_blueprint', '')[:200]}\n"
        p2_user = (
            f"Topic: {topic}\n{prev}"
            f"Write Python functions `{first_fn}(v)` through `{last_fn}(v)` implementing each `visual_blueprint` below:\n"
            + json.dumps(blueprint_list, indent=2, ensure_ascii=False)
        )
        try:
            return span, call_ai_python_code(base_url, api_key, model, p2_sys, p2_user, log_fn, temperature=0.35)
        except Exception as err:
            log_fn(f"[PASS 2 WARN] Batch {first_fn}-{last_fn} failed ({err}). The audit loop will rewrite these beats one by one.")
            return span, None

    with ThreadPoolExecutor(max_workers=max(1, PASS2_PARALLEL_CALLS)) as pool:
        results = list(pool.map(_run, batches))

    for (lo, hi), code in results:   # registered in order, on this thread (no races on `plan`)
        if code:
            loaded = add_generated_code(plan, namespace, code, log_fn)
            missing = [f"beat_{i + 1:02d}" for i in range(lo, hi) if f"beat_{i + 1:02d}" not in loaded]
            if missing:
                log_fn(f"[PASS 2 WARN] Missing/invalid after batch: {', '.join(missing)} (will be rewritten by the audit).")


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
    log_fn(f"[PASS 1/2: CREATIVE DIRECTOR] Planning topic-specific visual blueprints for '{topic}' ({total_dur}s)...")
    raw_scenes = plan_storyboard(base_url, api_key, model, topic, sections, words, total_dur, log_fn)
    scenes = sanitize_scenes(raw_scenes, total_dur, log_fn)

    plan: Dict[str, Any] = {"scenes": scenes, "palette": ref_profile["palette"], "total_duration": total_dur}
    plan["scene_sources"] = new_scene_sources()
    for s in scenes:
        log_fn(f"  Beat #{s['beat_id']:02d} [{s['start']:05.1f}s-{s['end']:05.1f}s] ({s.get('layout')}, {s.get('bg_color')}, {s.get('transition')}): {s.get('visual_blueprint')}")

    namespace = new_scene_namespace()
    generate_scene_code_batches(base_url, api_key, model, topic, scenes, plan, namespace, log_fn)
    write_scene_module_file(plan)
    return plan, namespace


# ==========================================
# STAGE 5C: SELF-CORRECTION LOOP (COMPLEXITY + ANTI-REPETITION AUDIT)
# ==========================================
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
        err = f"Function '{fn_name}(v)' is missing or was rejected by the code validator"

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
) -> Optional[str]:
    """Returns validated-ready SOURCE for `beat_XX` (or None). Register it with register_beat_source()."""
    fn_name = f"beat_{beat_idx + 1:02d}"
    sys_prompt = (
        PASS2_PYTHON_VECTOR_ARTIST_PROMPT.format(first_fn=fn_name, last_fn=fn_name)
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
    helpers, beats = split_scene_code(code_str, log_fn)
    src = beats.get(fn_name)
    if src is None and len(beats) == 1:   # model numbered it differently; rename to the expected name
        only_name, only_src = next(iter(beats.items()))
        src = re.sub(rf"^def\s+{re.escape(only_name)}\s*\(", f"def {fn_name}(", only_src, count=1)
    if src is None:
        return None
    if helpers:   # keep any helper next to the beat so it stays self-contained
        src = "\n\n".join(helpers.values()) + "\n\n" + src
    return src


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
    max_retries: int = 2,
    progress_fn=None,
):
    scenes = plan.get("scenes", [])
    log_fn(f"[SELF-CORRECTION LOOP v8.1] Auditing {len(scenes)} beats for complexity, motion & anti-repetition...")
    palette = plan.get("palette", {})
    prev_stage_crop: Optional[np.ndarray] = None

    for idx, beat in enumerate(scenes):
        if progress_fn:
            progress_fn(62 + int(8 * idx / max(1, len(scenes))), f"Auditing beat {idx + 1}/{len(scenes)}...")
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
                try:
                    new_src = rewrite_single_beat_function_with_ai(base_url, api_key, model, topic, idx, beat, reason_str, log_fn)
                except Exception as err:
                    log_fn(f"  [BEAT #{idx+1:02d} AUTO-FIX ERROR] {err}")
                    new_src = None
                if new_src:
                    register_beat_source(plan, namespace, f"beat_{idx+1:02d}", new_src, log_fn)
            else:
                prev_stage_crop = cur_crop
                log_fn(f"  [BEAT #{idx+1:02d} PROCEED] Max retries reached ({reason_str}).")

    write_scene_module_file(plan)   # generated_scenes.py now reflects every rewrite


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
        # half-open interval: a frame exactly on a boundary belongs to the NEW beat
        if float(sc["start"]) <= t < float(sc["end"]):
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


def render_beat_preview_sheet(
    plan: dict,
    namespace: Dict[str, Any],
    words: List[dict],
    beat_idx: int,
    width: int,
    height: int,
    out_path: str,
    frames: int = 6,
) -> str:
    """Contact sheet of ONE beat (no ffmpeg, no full render). On a 3-minute video, judging a single beat
    used to cost a full re-render; this takes a couple of seconds."""
    scenes = plan["scenes"]
    if not 0 <= beat_idx < len(scenes):
        raise IndexError(f"Beat index {beat_idx + 1} is out of range (1..{len(scenes)}).")
    beat = scenes[beat_idx]
    st, en = float(beat["start"]), float(beat["end"])
    frames = max(2, int(frames))
    tw = 360 if height >= width else 480
    thumbs: List[Image.Image] = []
    for k in range(frames):
        t = st + (en - st) * (k + 0.5) / frames
        img = render_frame(t, width, height, plan, words, namespace)
        thumbs.append(img.resize((tw, max(1, int(img.height * tw / img.width))), Image.Resampling.LANCZOS))
    cols = min(3, frames)
    rows = int(math.ceil(frames / cols))
    th = thumbs[0].height
    sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
    for i, im_t in enumerate(thumbs):
        r, c = divmod(i, cols)
        sheet.paste(im_t, (c * tw, r * th))
    sheet.save(out_path)
    return out_path


# --- multi-process frame rendering -------------------------------------------------
# Worker processes rebuild the scene functions from their SOURCE (functions exec'd from AI code
# cannot be pickled), then render frames independently. Output order is preserved by imap.
_MP_STATE: Dict[str, Any] = {}


def _mp_init(plan_json: str, words: List[dict], width: int, height: int):
    plan = json.loads(plan_json)
    _MP_STATE["plan"] = plan
    _MP_STATE["words"] = words
    _MP_STATE["w"] = width
    _MP_STATE["h"] = height
    _MP_STATE["ns"] = build_namespace(plan.get("scene_sources") or new_scene_sources(), lambda _m: None)


def _mp_ping() -> int:
    return 1


def _mp_render_frame(frame_idx: int) -> bytes:
    img = render_frame(
        frame_idx / float(FPS), _MP_STATE["w"], _MP_STATE["h"],
        _MP_STATE["plan"], _MP_STATE["words"], _MP_STATE["ns"],
    )
    return img.tobytes()


def default_render_workers() -> int:
    cpus = os.cpu_count() or 2
    return max(1, min(8, cpus - 1))


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
    progress_fn,
    workers: int = 1,
):
    total_frames = total_dur * FPS
    fade_st = max(0.0, round(total_dur - 2.5, 2))
    fade_vo_st = max(0.0, round(total_dur - 0.5, 2))
    preset = "medium" if total_dur <= 90 else "veryfast"   # long videos: favour turnaround

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
        "-c:v", "libx264", "-preset", preset, "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(FPS), "-frames:v", str(total_frames),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", str(total_dur),
        "-movflags", "+faststart", output_path
    ]

    # ---- frame source: worker pool (preferred) or in-process fallback ----
    pool = None
    workers = max(1, int(workers))
    if workers > 1 and total_frames > FPS * 3:
        try:
            ctx = multiprocessing.get_context("spawn")
            pool = ctx.Pool(
                processes=workers,
                initializer=_mp_init,
                initargs=(json.dumps(plan, ensure_ascii=False), words, width, height),
            )
            pool.apply_async(_mp_ping).get(timeout=90)   # fails fast if workers cannot start
        except Exception as err:
            log_fn(f"[RENDER WARN] Could not start {workers} render workers ({err}). Rendering in a single process instead.")
            if pool is not None:
                pool.terminate()
            pool = None

    stop = threading.Event()
    sem = threading.BoundedSemaphore(max(2, workers * 3)) if pool else None   # caps frames in flight (~6 MB each)

    def _task_ids():
        for i in range(total_frames):
            while not sem.acquire(timeout=0.5):
                if stop.is_set():
                    return
            if stop.is_set():
                return
            yield i

    if pool:
        frames_iter = pool.imap(_mp_render_frame, _task_ids(), chunksize=1)
    else:
        frames_iter = (render_frame(i / float(FPS), width, height, plan, words, namespace).tobytes() for i in range(total_frames))

    log_fn(
        f"[FFMPEG] Rendering {total_frames} frames ({total_dur}s @ {FPS}fps, {width}x{height}, "
        f"x264 {preset}, {'%d workers' % workers if pool else '1 process'})..."
    )
    t0 = time.time()
    written = 0
    pipe_broken = False

    with open(FFMPEG_LOG_FILE, "w", encoding="utf-8") as ff_err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=ff_err)
        try:
            for frame_idx, data in enumerate(frames_iter):
                if sem:
                    sem.release()
                try:
                    proc.stdin.write(data)
                except (BrokenPipeError, OSError):
                    pipe_broken = True   # ffmpeg died; its log (checked below) explains why
                    break
                written += 1

                if (frame_idx + 1) % 30 == 0 or frame_idx == total_frames - 1:
                    pct = int(((frame_idx + 1) / total_frames) * 100)
                    progress_fn(pct, f"Rendering frame {frame_idx + 1}/{total_frames} ({pct}%)")
                if (frame_idx + 1) % 150 == 0 or frame_idx == total_frames - 1:
                    elapsed = max(0.001, time.time() - t0)
                    rate = (frame_idx + 1) / elapsed
                    eta = (total_frames - frame_idx - 1) / rate
                    log_fn(f"[RENDER] {frame_idx + 1}/{total_frames} frames ({int((frame_idx+1)/total_frames*100)}%) | {rate:.1f} fps | ETA {eta:.0f}s")
        finally:
            stop.set()
            if pool is not None:
                pool.terminate()
                pool.join()
            try:
                if proc.stdin:
                    proc.stdin.close()
            except OSError:
                pass
            ret = proc.wait()

    if ret != 0 or pipe_broken or written != total_frames:
        with open(FFMPEG_LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            err_tail = f.read()[-1500:]
        log_fn(f"[FFMPEG ERROR] Exit code {ret}, frames written {written}/{total_frames}:\n{err_tail}")
        raise RuntimeError(f"FFmpeg failed (exit code {ret}, {written}/{total_frames} frames). Check {FFMPEG_LOG_FILE}.")

    log_fn(f"[RENDER] Completed in {round(time.time() - t0, 2)}s -> {output_path}")


# ==========================================
# STAGE 7: 7-POINT AUTOMATED VERIFICATION SUITE
# ==========================================
def run_verification_suite(
    output_path: str,
    expected_dur: Optional[int],
    expected_w: int,
    expected_h: int,
    log_fn,
    max_duration: int = DEFAULT_MAX_DURATION
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
        ["ffmpeg", "-y", "-i", output_path, "-vf", f"fps=1/{max(2.0, target_dur / 15.0):.3f},scale=360:-1", str(check_dir / "check_%02d.png")],
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
            f"4. Exact Container Duration (<= {max_duration}s Cap)",
            abs(fmt_dur - target_dur) <= 0.12 and fmt_dur <= (max_duration + 0.12),
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

    # Informational (not part of the 7 pass/fail checks): long frozen stretches usually mean a beat's animation stalled.
    try:
        frz = subprocess.run(
            ["ffmpeg", "-i", output_path, "-vf", "freezedetect=n=-60dB:d=2.5", "-an", "-f", "null", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        freezes = [ln.strip() for ln in frz.stderr.splitlines() if "freeze_start" in ln]
        if freezes:
            log_fn(f"  [NOTE] {len(freezes)} frozen stretch(es) >= 2.5s detected (check the contact sheet if unintended).")
    except Exception:
        pass

    log_fn(f"[VERIFICATION SUMMARY] Passed {passed_count}/{len(tests)} checks. Sheet: check/verification_contact_sheet.jpg")
    if passed_count < len(tests):
        raise AssertionError(f"Verification Suite failed {len(tests) - passed_count} check(s).")

    return {"passed": passed_count, "total": len(tests), "duration": fmt_dur, "frames": actual_frames}


# ==========================================
# GRAPHICAL USER INTERFACE (v8.1 — UNIVERSAL PROCEDURAL VECTOR STUDIO)
# ==========================================
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