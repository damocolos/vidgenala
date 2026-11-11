"""Legacy config importer and exporter (config_nalarka.json <-> SQLite)."""
import json
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional

from ..core.constants import (
    PROVIDER_CUSTOM, PROVIDER_OPENROUTER, PROVIDER_DEEPSEEK,
    DEFAULT_MAX_DURATION,
)
from ..core.llm import is_openrouter, is_deepseek
from ..core.render import default_render_workers
from ..db.repos.settings import (
    set_setting, get_setting, get_all_settings,
    set_provider, get_provider, list_providers,
)
from .secrets import encrypt_secret, decrypt_secret


def import_legacy_config_dict(
    conn: sqlite3.Connection,
    config: dict,
    force: bool = False,
    log_fn=print,
) -> dict:
    """Imports dictionary formatted like config_nalarka.json into SQLite."""
    imported_keys = []

    # 1. AI Provider settings
    prov_dict = config.get("providers", {})
    if prov_dict:
        for p_key, p_data in prov_dict.items():
            if p_key in (PROVIDER_CUSTOM, PROVIDER_OPENROUTER, PROVIDER_DEEPSEEK):
                set_provider(
                    conn,
                    key=p_key,
                    base_url=p_data.get("base_url"),
                    api_key=p_data.get("api_key"),
                    coder_model=p_data.get("coder_model") or p_data.get("model"),
                    script_model=p_data.get("script_model"),
                )
                imported_keys.append(f"providers.{p_key}")
    else:
        # Legacy flat structure
        b_url = config.get("base_url", "")
        p_key = PROVIDER_OPENROUTER if is_openrouter(b_url) else PROVIDER_DEEPSEEK if is_deepseek(b_url) else PROVIDER_CUSTOM
        set_provider(
            conn,
            key=p_key,
            base_url=b_url or None,
            api_key=config.get("api_key") or None,
            coder_model=config.get("coder_model") or config.get("model") or None,
            script_model=config.get("script_model") or None,
        )
        imported_keys.append(f"legacy_provider->{p_key}")

    # Active provider
    active_prov = config.get("ai_provider")
    if active_prov in (PROVIDER_CUSTOM, PROVIDER_OPENROUTER, PROVIDER_DEEPSEEK):
        set_provider(conn, key=active_prov, is_active=True)
        set_setting(conn, "ui.active_provider", active_prov)
        imported_keys.append("ai_provider")

    # 2. TTS settings
    if "tts_provider" in config:
        set_setting(conn, "tts.provider", str(config["tts_provider"]))
        imported_keys.append("tts_provider")

    if "elevenlabs_voice_id" in config:
        set_setting(conn, "tts.voice", str(config["elevenlabs_voice_id"]))
        imported_keys.append("elevenlabs_voice_id")

    if "elevenlabs_model_id" in config:
        set_setting(conn, "tts.model_id", str(config["elevenlabs_model_id"]))
        imported_keys.append("elevenlabs_model_id")

    for key_src, key_tgt in [
        ("elevenlabs_stability", "tts.stability"),
        ("elevenlabs_similarity", "tts.similarity"),
        ("elevenlabs_style", "tts.style"),
    ]:
        if key_src in config:
            try:
                val = float(config[key_src])
                val = max(0.0, min(1.0, val))
                set_setting(conn, key_tgt, val)
                imported_keys.append(key_src)
            except (ValueError, TypeError):
                pass

    if "elevenlabs_api_key" in config:
        el_key = str(config["elevenlabs_api_key"]).strip()
        if el_key:
            enc = encrypt_secret(el_key)
            set_setting(conn, "tts.elevenlabs_api_key_enc", enc.decode("latin1") if enc else "")
            imported_keys.append("elevenlabs_api_key")

    # 3. Library settings
    if "ref_library_dir" in config:
        set_setting(conn, "library.dir", str(config["ref_library_dir"]))
        imported_keys.append("ref_library_dir")

    if "selected_reference" in config:
        set_setting(conn, "library.selected_reference_id", str(config["selected_reference"]))
        imported_keys.append("selected_reference")

    # 4. Render settings with clamping
    if "max_duration" in config:
        try:
            m_dur = int(config["max_duration"])
            m_dur = max(15, min(900, m_dur))
            set_setting(conn, "render.max_duration", m_dur)
            imported_keys.append("max_duration")
        except (ValueError, TypeError):
            pass

    if "render_workers" in config:
        try:
            rw = int(config["render_workers"])
            rw = max(1, min(16, rw))
            set_setting(conn, "render.workers", rw)
            imported_keys.append("render_workers")
        except (ValueError, TypeError):
            pass

    log_fn(f"[CONFIG IMPORTER] Imported {len(imported_keys)} keys: {', '.join(imported_keys)}")
    return {"imported": len(imported_keys), "keys": imported_keys}


def import_legacy_config_file(
    conn: sqlite3.Connection,
    config_file_path: Path,
    force: bool = False,
    log_fn=print,
) -> dict:
    if not config_file_path.exists():
        return {"imported": 0, "error": f"File {config_file_path} not found"}
    try:
        data = json.loads(config_file_path.read_text(encoding="utf-8"))
        return import_legacy_config_dict(conn, data, force=force, log_fn=log_fn)
    except Exception as exc:
        log_fn(f"[CONFIG IMPORTER ERROR] {exc}")
        return {"imported": 0, "error": str(exc)}


def export_legacy_config(conn: sqlite3.Connection, include_secrets: bool = False) -> dict:
    """Exports SQLite settings back to legacy config_nalarka.json format for seamless rollback."""
    providers_list = list_providers(conn)
    prov_data = {}
    active_key = "custom"
    for p in providers_list:
        k = p["key"]
        if p["is_active"]:
            active_key = k
        prov_data[k] = {
            "base_url": p["base_url"],
            "api_key": (p["api_key"] or "") if include_secrets else "",
            "coder_model": p["coder_model"],
            "script_model": p["script_model"],
        }

    el_key = ""
    if include_secrets:
        enc_str = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
        if enc_str:
            el_key = decrypt_secret(enc_str.encode("latin1")) or ""

    return {
        "ai_provider": active_key,
        "providers": prov_data,
        "tts_provider": get_setting(conn, "tts.provider", "ElevenLabs API v1"),
        "elevenlabs_api_key": el_key,
        "elevenlabs_voice_id": get_setting(conn, "tts.voice", "Bagas (Indonesia)"),
        "elevenlabs_model_id": get_setting(conn, "tts.model_id", "eleven_multilingual_v2"),
        "elevenlabs_stability": get_setting(conn, "tts.stability", 0.50),
        "elevenlabs_similarity": get_setting(conn, "tts.similarity", 0.75),
        "elevenlabs_style": get_setting(conn, "tts.style", 0.15),
        "ref_library_dir": get_setting(conn, "library.dir", "references_library"),
        "selected_reference": get_setting(conn, "library.selected_reference_id", ""),
        "max_duration": get_setting(conn, "render.max_duration", DEFAULT_MAX_DURATION),
        "render_workers": get_setting(conn, "render.workers", default_render_workers()),
    }
