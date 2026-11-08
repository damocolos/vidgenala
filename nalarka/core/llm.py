"""Multi-provider LLM client and call wrappers for Nalarka Studio."""
import os
import time
import json
import logging
import urllib.request
from typing import Optional, List, Dict, Tuple, Any
from openai import OpenAI

from .constants import OPENROUTER_MODELS_URL
from .extractors import extract_json_safely, extract_python_code_safely


def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def make_client(base_url: str, api_key: str, timeout: float = 300.0, max_retries: int = 3) -> OpenAI:
    """Builds an OpenAI-SDK client. OpenRouter gets its optional attribution headers automatically."""
    key = (api_key or "").strip()
    if not key:
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
    Returns (text, finish_reason).
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
