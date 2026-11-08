"""Router for config, AI providers, models, and TTS settings."""
import uuid
import sqlite3
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..deps import get_db_conn, get_dispatcher
from ..schemas import (
    ProviderResponse, UpdateProviderRequest, TTSSettingsRequest,
    ConfigUpdateRequest, DurationHintResponse, JobAcceptedResponse,
)
from ...db.repos.settings import (
    get_all_settings, set_setting, get_setting,
    get_provider, set_provider, list_providers,
    get_models_catalog, list_tts_voices,
)
from ...db.repos.jobs import create_job
from ...services.config_importer import export_legacy_config, import_legacy_config_dict
from ...services.settings_validation import validate_settings
from ...services.secrets import encrypt_secret, decrypt_secret
from ...core.script import script_word_budget, script_section_count

router = APIRouter(tags=["Config & Providers"])


@router.get("/config")
def get_config(conn: sqlite3.Connection = Depends(get_db_conn)) -> Dict[str, Any]:
    all_s = get_all_settings(conn)
    # Filter out internal encrypted keys
    return {k: v for k, v in all_s.items() if not k.endswith("_enc")}


@router.put("/config")
def update_config(req: ConfigUpdateRequest, conn: sqlite3.Connection = Depends(get_db_conn)) -> Dict[str, Any]:
    clean, errors = validate_settings(req.settings)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    for k, v in clean.items():
        set_setting(conn, k, v)
    return {"status": "ok", "updated": len(clean)}


@router.get("/config/export")
def export_config(
    legacy: int = Query(1),
    include_secrets: int = Query(0),
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> Dict[str, Any]:
    return export_legacy_config(conn, include_secrets=bool(include_secrets))


@router.post("/config/import")
def import_config(
    data: Dict[str, Any],
    conn: sqlite3.Connection = Depends(get_db_conn),
) -> Dict[str, Any]:
    return import_legacy_config_dict(conn, data)


@router.get("/providers", response_model=List[ProviderResponse])
def get_providers_list(conn: sqlite3.Connection = Depends(get_db_conn)):
    return list_providers(conn)


@router.put("/providers/{key}")
def update_provider_endpoint(
    key: str,
    req: UpdateProviderRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    try:
        set_provider(
            conn,
            key=key,
            label=req.label,
            base_url=req.base_url,
            api_key=req.api_key,
            coder_model=req.coder_model,
            script_model=req.script_model,
            is_active=req.is_active,
        )
        return {"status": "ok", "provider": get_provider(conn, key)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/providers/{key}/test", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def test_provider_connection(
    key: str,
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    prov = get_provider(conn, key)
    if not prov:
        raise HTTPException(status_code=404, detail=f"Provider '{key}' not found.")
    job_id = f"test-ai-{uuid.uuid4().hex[:8]}"
    create_job(conn, job_id, kind="test_ai", weight="light", params={"provider": key})
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.post("/providers/openrouter/fetch-models", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def fetch_openrouter_models_endpoint(
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    job_id = f"fetch-or-{uuid.uuid4().hex[:8]}"
    create_job(conn, job_id, kind="fetch_openrouter_models", weight="light", params={})
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.get("/models")
def get_models(
    provider: Optional[str] = None,
    role: Optional[str] = None,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    return get_models_catalog(conn, provider_key=provider, role=role)


@router.get("/tts/settings")
def get_tts_settings(conn: sqlite3.Connection = Depends(get_db_conn)):
    enc = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
    plain_key = decrypt_secret(enc.encode("latin1")) if enc else ""
    return {
        "provider": get_setting(conn, "tts.provider", "ElevenLabs API v1"),
        "voice": get_setting(conn, "tts.voice", "Bagas (Indonesia)"),
        "model_id": get_setting(conn, "tts.model_id", "eleven_multilingual_v2"),
        "stability": get_setting(conn, "tts.stability", 0.50),
        "similarity": get_setting(conn, "tts.similarity", 0.75),
        "style": get_setting(conn, "tts.style", 0.15),
        "speaker_boost": get_setting(conn, "tts.speaker_boost", True),
        "ssml_breaks": get_setting(conn, "tts.ssml_breaks", False),
        "has_api_key": bool(plain_key),
    }


@router.put("/tts/settings")
def update_tts_settings(
    req: TTSSettingsRequest,
    conn: sqlite3.Connection = Depends(get_db_conn),
):
    if req.provider is not None:
        set_setting(conn, "tts.provider", req.provider)
    if req.voice is not None:
        set_setting(conn, "tts.voice", req.voice)
    if req.model_id is not None:
        set_setting(conn, "tts.model_id", req.model_id)
    if req.stability is not None:
        set_setting(conn, "tts.stability", req.stability)
    if req.similarity is not None:
        set_setting(conn, "tts.similarity", req.similarity)
    if req.style is not None:
        set_setting(conn, "tts.style", req.style)
    if req.speaker_boost is not None:
        set_setting(conn, "tts.speaker_boost", req.speaker_boost)
    if req.ssml_breaks is not None:
        set_setting(conn, "tts.ssml_breaks", req.ssml_breaks)
    if req.api_key is not None:
        key_str = req.api_key.strip()
        enc = encrypt_secret(key_str) if key_str else None
        set_setting(conn, "tts.elevenlabs_api_key_enc", enc.decode("latin1") if enc else "")
    return {"status": "ok"}


@router.post("/tts/voices/fetch", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
def fetch_voices_endpoint(
    conn: sqlite3.Connection = Depends(get_db_conn),
    dispatcher=Depends(get_dispatcher),
):
    job_id = f"fetch-el-{uuid.uuid4().hex[:8]}"
    create_job(conn, job_id, kind="fetch_el_voices", weight="light", params={})
    dispatcher.dispatch_next()
    return JobAcceptedResponse(job_id=job_id)


@router.get("/tts/voices")
def get_tts_voices(conn: sqlite3.Connection = Depends(get_db_conn)):
    return list_tts_voices(conn)


@router.get("/duration-hint", response_model=DurationHintResponse)
def get_duration_hint(d: int = Query(60, ge=15, le=900)):
    lo, hi = script_word_budget(d)
    sec_count = script_section_count(d)
    beats_lo = max(1, int(round(d / 7.0)))
    beats_hi = max(beats_lo, int(round(d / 4.5)))
    mode_label = f"Shorts / Reels ({d}s)" if d <= 60 else f"Standard Video ({d}s)"
    return DurationHintResponse(
        max_duration=d,
        word_budget_lo=lo,
        word_budget_hi=hi,
        recommended_sections=sec_count,
        beats_lo=beats_lo,
        beats_hi=beats_hi,
        mode_label=mode_label,
    )
