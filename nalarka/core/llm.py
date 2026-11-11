"""Multi-provider LLM client and call wrappers for Nalarka Studio."""
import os
import time
import json
import hashlib
import threading
import logging
import re
import urllib.error
import urllib.request
from .llm_guard import ProviderGuard, ProviderCircuitOpen, get_llm_guard, set_llm_guard  # noqa: F401
from typing import Optional, List, Dict, Tuple, Any
from openai import OpenAI

from .constants import OPENROUTER_MODELS_URL
from .extractors import extract_json_safely, extract_python_code_safely


def is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def is_deepseek(base_url: str) -> bool:
    return "deepseek.com" in (base_url or "").lower()


def _env_key_name(base_url: str) -> str:
    if is_openrouter(base_url):
        return "OPENROUTER_API_KEY"
    if is_deepseek(base_url):
        return "DEEPSEEK_API_KEY"
    return "NALARKA_API_KEY"


def make_client(base_url: str, api_key: str, timeout: float = 300.0, max_retries: int = 1) -> OpenAI:
    """Builds an OpenAI-SDK client. OpenRouter gets its optional attribution headers automatically."""
    key = (api_key or "").strip()
    if not key:
        key = os.environ.get(_env_key_name(base_url), "").strip()
    if not key:
        raise ValueError(
            "API key is empty. Enter the key for the selected provider in Section 1 "
            f"(or set the {_env_key_name(base_url)} environment variable)."
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


def fetch_provider_models(base_url: str, api_key: str, log_fn) -> List[Dict[str, Any]]:
    """Lists the models an OpenAI-compatible provider serves (GET <base_url>/models), e.g. DeepSeek.
    The key is sent only to the provider's own base URL."""
    key = (api_key or "").strip() or os.environ.get(_env_key_name(base_url), "").strip()
    if not key:
        raise ValueError("API key is empty. Save the provider's key first, then fetch models.")
    url = base_url.strip().rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"}, method="GET")
    log_fn(f"[MODELS] GET {url}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        class _E(Exception):
            status_code = err.code
        raise RuntimeError(friendly_api_error(_E(f"HTTP {err.code} from {url}"), base_url)) from err
    models = [{"id": m["id"], "name": m.get("name", m["id"])} for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
    models.sort(key=lambda x: x["id"])
    return models


def friendly_api_error(err: Exception, base_url: str) -> str:
    """One-line, actionable explanation for the HTTP errors people actually hit (OpenRouter included)."""
    code = getattr(err, "status_code", None)
    host = "OpenRouter" if is_openrouter(base_url) else "DeepSeek" if is_deepseek(base_url) else "the API endpoint"
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
    if is_deepseek(base_url):
        hints.update({
            400: "DeepSeek rejected the request body. Check the model id (use Fetch models) and max_tokens.",
            402: "DeepSeek reports insufficient balance. Top up at platform.deepseek.com, then press Resume.",
            422: "DeepSeek rejected a parameter value. Check the model id and settings.",
            429: "DeepSeek is rate limiting this account. Wait a moment, or lower 'parallel calls' in Settings.",
            500: "DeepSeek had an internal error. Retry in a few minutes.",
            503: "DeepSeek is overloaded. Retry in a few minutes, or lower 'parallel calls' in Settings.",
        })
    base = f"{type(err).__name__}: {err}"
    return f"{base}  ->  {hints[code]}" if code in hints else base


def _chat_text_impl(
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
    cap = [None]   # per-model output limit learned from a provider error

    def _create(stream: bool, temp: Optional[float]):
        for _ in range(3):
            kw: Dict[str, Any] = {"model": model.strip(), "messages": messages, "stream": stream}
            if temp is not None and "temperature" not in dropped:
                kw["temperature"] = temp
            if max_tokens and "max_tokens" not in dropped:
                kw["max_tokens"] = min(max_tokens, cap[0]) if cap[0] else max_tokens
            try:
                # The non-streaming fallback must not retry inside the SDK: a stuck provider would cost
                # (retries x timeout) minutes per call. The stop point handles repeated failures instead.
                target = client if (stream or not hasattr(client, "with_options")) else client.with_options(max_retries=0)
                return target.chat.completions.create(**kw)
            except Exception as err:
                msg = str(err).lower()
                if "temperature" in msg and "temperature" not in dropped:
                    dropped.add("temperature")
                    log_fn(f"[{tag} WARN] '{model}' rejected 'temperature'; retrying without it.")
                    continue
                if ("max_tokens" in msg or "max_completion_tokens" in msg) and "max_tokens" not in dropped:
                    # e.g. DeepSeek: "valid range of max_tokens is [1, 8192]" -> ask for that maximum instead of dropping it
                    rng = re.search(r"\[\s*1\s*,\s*(\d{3,7})\s*\]", str(err))
                    if rng and int(rng.group(1)) < (cap[0] or 10**9):
                        cap[0] = int(rng.group(1))
                        log_fn(f"[{tag} WARN] '{model}' allows at most {cap[0]} output tokens; retrying with that limit.")
                        continue
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
            if time.time() - t0 > timeout:
                raise TimeoutError(f"no complete answer within {int(timeout)}s")
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


# ---------------------------------------------------------------------------------------------
# AI request ledger hook. core stays DB-free: the job runner installs a recorder (see
# services/llm_ledger.py). Every call is recorded; on a resumed job, responses the previous
# run already accepted are replayed from the ledger instead of paying for them again.
# ---------------------------------------------------------------------------------------------
_RECORDER = None
_TLS = threading.local()


def set_llm_recorder(recorder) -> None:
    global _RECORDER
    _RECORDER = recorder


def get_llm_recorder():
    return _RECORDER


def llm_request_key(model: str, system_prompt: str, user_prompt: str, temperature, max_tokens, tag: str) -> str:
    payload = json.dumps([model.strip(), system_prompt, user_prompt, temperature, max_tokens, tag], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def accept_last_call() -> None:
    """Mark this thread's most recent call as 'used successfully' (so a resume may replay it)."""
    cid = getattr(_TLS, "call_id", None)
    _TLS.call_id = None
    if cid and _RECORDER is not None:
        try:
            _RECORDER.accept(cid)
        except Exception:
            pass


def _chat_text(*args, **kwargs) -> Tuple[str, Optional[str]]:
    """Guarded + ledger-aware chat completion. Returns (text, finish_reason).

    The provider guard (core/llm_guard.py) counts real network failures and raises
    ProviderCircuitOpen to stop the whole job; replayed ledger answers never touch it."""
    guard = get_llm_guard()
    if guard is not None:
        guard.before_call()
    _TLS.replayed = False
    try:
        out = _chat_text_ledger(*args, **kwargs)
    except Exception as err:
        if guard is not None and not getattr(_TLS, "replayed", False):
            guard.on_failure(err)
        raise
    if guard is not None and not getattr(_TLS, "replayed", False):
        guard.on_success()
    return out


def _chat_text_ledger(
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
    """Ledger-aware wrapper around one chat completion. Returns (text, finish_reason)."""
    rec = _RECORDER
    _TLS.call_id = None
    if rec is None:
        return _chat_text_impl(base_url, api_key, model, system_prompt, user_prompt, log_fn, tag,
                               temperature, timeout, max_tokens, min_chars)

    key = llm_request_key(model, system_prompt, user_prompt, temperature, max_tokens, tag)
    base = {"tag": tag, "model": model.strip(), "base_url": base_url.strip(), "request_hash": key,
            "system_prompt": system_prompt, "user_prompt": user_prompt,
            "temperature": temperature, "max_tokens": max_tokens}
    try:
        hit = rec.lookup(key)
    except Exception:
        hit = None
    if hit:
        _TLS.replayed = True
        log_fn(f"[{tag}] Replayed saved AI response ({len(hit['response_text'] or '')} chars) - no API call, no cost.")
        try:
            _TLS.call_id = rec.record(status="replayed", response_text=hit["response_text"],
                                      finish_reason=hit["finish_reason"], replay_of=hit["id"], **base)
        except Exception:
            _TLS.call_id = None
        return hit["response_text"] or "", hit["finish_reason"]

    t0 = time.time()
    try:
        text, finish = _chat_text_impl(base_url, api_key, model, system_prompt, user_prompt, log_fn, tag,
                                       temperature, timeout, max_tokens, min_chars)
    except Exception as err:
        try:
            rec.record(status="error", error=str(err)[:2000], duration_sec=round(time.time() - t0, 2), **base)
        except Exception:
            pass
        raise
    try:
        _TLS.call_id = rec.record(status="ok", response_text=text, finish_reason=finish,
                                  duration_sec=round(time.time() - t0, 2), **base)
    except Exception as err:  # never let bookkeeping break a paid call
        log_fn(f"[{tag} WARN] Could not save AI response to the ledger: {err}")
        _TLS.call_id = None
    return text, finish


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
            parsed = extract_json_safely(raw, log_fn)
            accept_last_call()
            return parsed
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
    code = extract_python_code_safely(raw)
    accept_last_call()
    return code
