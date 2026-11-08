"""ElevenLabs and Edge-TTS voice synthesis integration."""
import os
import re
import json
import time
import asyncio
import subprocess
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

from .constants import EL_CHUNK_CHARS
from .scripttext import prepare_text_for_elevenlabs


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
        if multi:
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
