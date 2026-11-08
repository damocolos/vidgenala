"""Duration-aware voiceover script generation and budget estimation."""
import math
import json
from typing import Tuple, Dict, Any, List

from .constants import (
    DEFAULT_MAX_DURATION,
    SPEECH_WORDS_PER_SEC,
    INTRO_OUTRO_SECONDS,
    TTS_OVERSHOOT_TOLERANCE,
)
from .scripttext import (
    format_sections_to_tagged_text,
    parse_tagged_script,
)
from .llm import call_ai_json


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


def check_script_fits(raw_editor_text: str, max_duration: int, log_fn) -> int:
    """
    Run BEFORE paying for TTS. Returns the estimated video seconds; raises when the
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
