"""Phrase-by-phrase captions: 2-4 words at a time, lower third, white with a soft shadow.

Off unless a plan carries `plan["captions"]` (a list of cues), so videos made without the option are
byte-for-byte what they were before. The wording comes from the SCRIPT (correct spelling); Whisper only
supplies when each word is spoken.
"""
import bisect
import difflib
import re
from typing import Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter

MAX_WORDS = 4
MAX_CHARS = 26            # a phrase longer than this is split, so one line always fits a phone screen
PAUSE_SPLIT = 0.15        # seconds of silence between words that ends a phrase
TAIL = 0.05               # keep a phrase on screen this long after its last word
Y_CENTER = 0.65           # caption centre, as a share of the frame height
SIZE_SHARE = 0.05         # font size, as a share of the frame height
MAX_WIDTH_SHARE = 0.88

_END_PUNCT = (".", "!", "?", ",", ";", ":", "…")


def _norm(token: str) -> str:
    return re.sub(r"[^\w]", "", token.lower(), flags=re.UNICODE)


def align_script_to_words(script_text: str, words: List[dict]) -> Tuple[List[dict], float]:
    """Give every SCRIPT word a start/end time using the Whisper words. Returns (timed words, match ratio).

    Words both sides agree on keep Whisper's time; words only the script has (Whisper misheard them) are
    spread evenly between their timed neighbours."""
    toks = [t for t in re.split(r"\s+", script_text.strip()) if _norm(t)]
    heard = [w for w in words if _norm(str(w.get("word", "")))]
    if not toks or not heard:
        return [], 0.0
    sm = difflib.SequenceMatcher(a=[_norm(t) for t in toks], b=[_norm(str(w["word"])) for w in heard], autojunk=False)
    timed: List[Optional[Tuple[float, float]]] = [None] * len(toks)
    matched = 0
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            w = heard[blk.b + k]
            timed[blk.a + k] = (float(w["start"]), float(w["end"]))
            matched += 1
    ratio = matched / len(toks)
    if matched == 0:
        return [], 0.0
    first, last = float(heard[0]["start"]), float(heard[-1]["end"])
    i = 0
    while i < len(toks):
        if timed[i] is not None:
            i += 1
            continue
        j = i
        while j < len(toks) and timed[j] is None:
            j += 1
        lo = timed[i - 1][1] if i > 0 else first
        hi = timed[j][0] if j < len(toks) else last
        hi = max(hi, lo)
        step = (hi - lo) / (j - i)
        for k in range(i, j):
            timed[k] = (lo + step * (k - i), lo + step * (k - i + 1))
        i = j
    return [{"word": t, "start": round(s, 3), "end": round(e, 3)} for t, (s, e) in zip(toks, timed)], ratio


def chunk_words(words: List[dict], max_words: int = MAX_WORDS, max_chars: int = MAX_CHARS,
                pause: float = PAUSE_SPLIT) -> List[Dict[str, object]]:
    """Group timed words into cues {start, end, text}. A phrase ends at a pause, at punctuation, or when full."""
    cues: List[Dict[str, object]] = []
    cur: List[dict] = []

    def flush():
        if cur:
            cues.append({"start": float(cur[0]["start"]), "end": float(cur[-1]["end"]),
                         "text": " ".join(str(w["word"]) for w in cur)})
            cur.clear()

    for i, w in enumerate(words):
        if cur and len(" ".join([str(x["word"]) for x in cur] + [str(w["word"])])) > max_chars:
            flush()
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (float(nxt["start"]) - float(w["end"])) if nxt else 0.0
        if (len(cur) >= max_words or str(w["word"]).endswith(_END_PUNCT) or gap >= pause or nxt is None):
            flush()
    # Show each phrase until just after its last word, but never over the next phrase.
    for i, c in enumerate(cues):
        end = float(c["end"]) + TAIL
        if i + 1 < len(cues):
            end = min(end, float(cues[i + 1]["start"]))
        c["end"] = round(max(end, float(c["start"]) + 0.05), 3)
        c["start"] = round(float(c["start"]), 3)
    return cues


def build_cues(script_text: Optional[str], words: List[dict]) -> Tuple[List[dict], dict]:
    """Cues for a video. Uses the script's wording when it lines up with what Whisper heard (at least half
    the words match), otherwise Whisper's own words. Second value = a small report for the job log."""
    report = {"source": "whisper", "script_words": 0, "matched": 0.0, "cues": 0}
    timed: List[dict] = []
    if script_text and script_text.strip():
        timed, ratio = align_script_to_words(script_text, words)
        report["script_words"], report["matched"] = len(timed), round(ratio, 2)
        if ratio >= 0.5:
            report["source"] = "script"
        else:
            timed = []
    if not timed:
        timed = [dict(w) for w in words if _norm(str(w.get("word", "")))]
    cues = chunk_words(timed)
    report["cues"] = len(cues)
    return cues, report


# --- drawing ---------------------------------------------------------------------------------

_LAYER_CACHE: Dict[tuple, Image.Image] = {}


def _font(size: int):
    from .vectorkit import load_font
    return load_font(size, bold=False)


def _caption_layer(text: str, width: int, height: int) -> Image.Image:
    """RGBA strip with the caption already drawn (shadow included); cached because the same cue is
    drawn on dozens of consecutive frames."""
    key = (text, width, height)
    hit = _LAYER_CACHE.get(key)
    if hit is not None:
        return hit
    size = max(14, int(height * SIZE_SHARE))
    max_w = int(width * MAX_WIDTH_SHARE)
    dummy = ImageDraw.Draw(Image.new("L", (1, 1)))
    font = _font(size)

    def measure(txt: str, fnt):
        l, t, r, b = dummy.multiline_textbbox((0, 0), txt, font=fnt, align="center", spacing=max(2, size // 8))
        return (int(l), int(t), int(r + 0.999), int(b + 0.999))

    # One line when it fits; otherwise two balanced lines; only then a smaller font.
    shown = text
    box = measure(shown, font)
    if box[2] - box[0] > max_w and " " in text:
        words = text.split(" ")
        shown = min((" ".join(words[:k]) + "\n" + " ".join(words[k:]) for k in range(1, len(words))),
                    key=lambda t: max(dummy.textlength(line, font=font) for line in t.split("\n")))
        box = measure(shown, font)
    while size > 8 and box[2] - box[0] > max_w:
        size -= 2
        font = _font(size)
        box = measure(shown, font)
    tw, th = box[2] - box[0], box[3] - box[1]
    pad = max(8, size // 2)
    layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    ox, oy = pad - box[0], pad - box[1]
    kw = dict(font=font, align="center", spacing=max(2, size // 8))
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).multiline_text((ox, oy + max(2, size // 14)), shown, fill=(0, 0, 0, 170), **kw)
    layer = Image.alpha_composite(layer, shadow.filter(ImageFilter.GaussianBlur(max(2, size // 12))))
    ImageDraw.Draw(layer).multiline_text((ox, oy), shown, fill=(255, 255, 255, 255), **kw)
    if len(_LAYER_CACHE) > 256:
        _LAYER_CACHE.clear()
    _LAYER_CACHE[key] = layer
    return layer


def active_cue(cues: List[dict], t: float) -> Optional[dict]:
    if not cues:
        return None
    i = bisect.bisect_right([c["start"] for c in cues], t) - 1
    if i >= 0 and cues[i]["start"] <= t < cues[i]["end"]:
        return cues[i]
    return None


def apply_caption(img: Image.Image, t: float, cues: List[dict]) -> Image.Image:
    """Return `img` with the cue spoken at time `t` drawn on it (unchanged when there is none)."""
    cue = active_cue(cues, t)
    if cue is None:
        return img
    layer = _caption_layer(str(cue["text"]), img.width, img.height)
    out = img.convert("RGBA") if img.mode != "RGBA" else img.copy()
    x = (img.width - layer.width) // 2
    y = int(img.height * Y_CENTER) - layer.height // 2
    out.alpha_composite(layer, (x, y))
    return out.convert(img.mode)
