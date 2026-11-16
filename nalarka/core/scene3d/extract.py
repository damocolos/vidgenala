"""Pull `function beat_NN(v) { ... }` declarations out of a model's answer."""
import re
from typing import Dict

_FN = re.compile(r"\bfunction\s+(beat_\d+)\s*\(")


def _strip_fences(raw: str) -> str:
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL)
    return re.sub(r"^\s*```[a-zA-Z]*\s*$", "", raw, flags=re.MULTILINE)


def _match_brace(text: str, open_idx: int) -> int:
    """Index of the `}` that closes the `{` at open_idx, skipping strings and comments; -1 if it never closes
    (an answer that was cut off)."""
    depth, i, n = 0, open_idx, len(text)
    while i < n:
        ch, nx = text[i], text[i + 1] if i + 1 < n else ""
        if ch == "/" and nx == "/":
            i = text.find("\n", i)
            if i < 0:
                return -1
            continue
        if ch == "/" and nx == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                return -1
            i = j + 2
            continue
        if ch in "\"'`":
            q, i = ch, i + 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def extract_beat_functions(raw: str) -> Dict[str, str]:
    """{ "beat_07": "function beat_07(v) {...}" } for every COMPLETE beat function; cut-off ones are left out."""
    text = _strip_fences(raw)
    out: Dict[str, str] = {}
    for m in _FN.finditer(text):
        brace = text.find("{", m.end())
        if brace < 0:
            continue
        end = _match_brace(text, brace)
        if end < 0:
            continue
        out[m.group(1)] = text[m.start():end + 1].strip()      # a later copy of the same name wins
    return out
