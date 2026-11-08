"""Safe JSON and Python code extractors with reasoning (<think>) tag stripping."""
import re
import json


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
    # Find all ```python ... ``` or ``` ... ``` code blocks
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks)
    # If no closing block or unclosed markdown
    cleaned = re.sub(r"^```(?:python)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()
