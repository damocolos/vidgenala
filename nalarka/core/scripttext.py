"""Structured script parsing, tag extraction, and fingerprint hashing."""
import re
import hashlib
from typing import List, Dict, Tuple


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
