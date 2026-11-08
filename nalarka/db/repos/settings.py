"""Repository for app settings, AI providers, and model catalogs."""
import json
import sqlite3
from typing import Dict, Any, List, Optional
from ...services.secrets import encrypt_secret, decrypt_secret, mask_secret


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    cur = conn.cursor()
    cur.execute("SELECT value, value_type FROM app_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    if not row:
        return default
    val_str, vtype = row["value"], row["value_type"]
    if vtype == "int":
        return int(val_str)
    elif vtype == "float":
        return float(val_str)
    elif vtype == "bool":
        return val_str.lower() in ("true", "1", "yes")
    elif vtype == "json":
        return json.loads(val_str)
    return val_str


def set_setting(conn: sqlite3.Connection, key: str, value: Any, value_type: Optional[str] = None) -> None:
    if value_type is None:
        if isinstance(value, bool):
            value_type = "bool"
            val_str = "true" if value else "false"
        elif isinstance(value, int):
            value_type = "int"
            val_str = str(value)
        elif isinstance(value, float):
            value_type = "float"
            val_str = str(value)
        elif isinstance(value, (dict, list)):
            value_type = "json"
            val_str = json.dumps(value, ensure_ascii=False)
        else:
            value_type = "string"
            val_str = str(value)
    else:
        val_str = str(value)

    with conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, value_type, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                value_type = excluded.value_type,
                updated_at = excluded.updated_at
            """,
            (key, val_str, value_type),
        )


def get_all_settings(conn: sqlite3.Connection) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("SELECT key, value, value_type FROM app_settings")
    res = {}
    for row in cur.fetchall():
        key, val_str, vtype = row["key"], row["value"], row["value_type"]
        if vtype == "int":
            res[key] = int(val_str)
        elif vtype == "float":
            res[key] = float(val_str)
        elif vtype == "bool":
            res[key] = val_str.lower() in ("true", "1", "yes")
        elif vtype == "json":
            res[key] = json.loads(val_str)
        else:
            res[key] = val_str
    return res


def get_provider(conn: sqlite3.Connection, key: str) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT key, label, base_url, api_key_enc, coder_model, script_model, is_active, updated_at
        FROM ai_providers WHERE key = ?
        """,
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    data = dict(row)
    enc = data.pop("api_key_enc", None)
    plain_key = decrypt_secret(enc) if enc else None
    data["api_key"] = plain_key
    data["has_key"] = bool(plain_key)
    data["masked_key"] = mask_secret(plain_key)
    return data


def get_active_provider(conn: sqlite3.Connection) -> Optional[dict]:
    cur = conn.cursor()
    cur.execute("SELECT key FROM ai_providers WHERE is_active = 1")
    row = cur.fetchone()
    if row:
        return get_provider(conn, row["key"])
    return None


def set_provider(
    conn: sqlite3.Connection,
    key: str,
    *,
    label: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    coder_model: Optional[str] = None,
    script_model: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> None:
    current = get_provider(conn, key)
    if not current:
        raise ValueError(f"Provider '{key}' does not exist.")

    upd_label = label if label is not None else current["label"]
    upd_base_url = base_url if base_url is not None else current["base_url"]
    upd_coder = coder_model if coder_model is not None else current["coder_model"]
    upd_script = script_model if script_model is not None else current["script_model"]
    upd_active = int(is_active) if is_active is not None else current["is_active"]

    if api_key is not None:
        enc_key = encrypt_secret(api_key.strip()) if api_key.strip() else None
    elif base_url is not None and base_url.strip().rstrip("/") != (current["base_url"] or "").strip().rstrip("/"):
        # Never forward a stored key to a different endpoint: the caller must re-enter it.
        enc_key = None
    else:
        # Keep existing encrypted key
        cur = conn.cursor()
        cur.execute("SELECT api_key_enc FROM ai_providers WHERE key = ?", (key,))
        row = cur.fetchone()
        enc_key = row["api_key_enc"] if row else None

    with conn:
        if upd_active:
            conn.execute("UPDATE ai_providers SET is_active = 0 WHERE is_active = 1 AND key != ?", (key,))

        conn.execute(
            """
            UPDATE ai_providers
            SET label = ?, base_url = ?, api_key_enc = ?, coder_model = ?, script_model = ?,
                is_active = ?, updated_at = datetime('now')
            WHERE key = ?
            """,
            (upd_label, upd_base_url, enc_key, upd_coder, upd_script, upd_active, key),
        )


def list_providers(conn: sqlite3.Connection) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT key FROM ai_providers ORDER BY key")
    return [get_provider(conn, r["key"]) for r in cur.fetchall()]


def get_models_catalog(conn: sqlite3.Connection, provider_key: Optional[str] = None, role: Optional[str] = None) -> List[dict]:
    query = "SELECT provider_key, model_id, role, is_free, source, fetched_at FROM model_catalog WHERE 1=1"
    params = []
    if provider_key:
        query += " AND provider_key = ?"
        params.append(provider_key)
    if role:
        query += " AND role = ?"
        params.append(role)
    query += " ORDER BY is_free DESC, model_id ASC"
    cur = conn.cursor()
    cur.execute(query, params)
    return [dict(r) for r in cur.fetchall()]


def add_catalog_model(
    conn: sqlite3.Connection,
    provider_key: str,
    model_id: str,
    role: str,
    is_free: bool = False,
    source: str = "fetched",
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO model_catalog (provider_key, model_id, role, is_free, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(provider_key, model_id, role) DO UPDATE SET
                is_free = excluded.is_free,
                source = excluded.source,
                fetched_at = datetime('now')
            """,
            (provider_key, model_id, role, int(is_free), source),
        )


def list_tts_voices(conn: sqlite3.Connection) -> List[dict]:
    cur = conn.cursor()
    cur.execute("SELECT voice_id, name, category, label, source, fetched_at FROM tts_voices ORDER BY name ASC")
    return [dict(r) for r in cur.fetchall()]


def add_tts_voice(
    conn: sqlite3.Connection,
    voice_id: str,
    name: str,
    category: Optional[str],
    label: str,
    source: str = "fetched",
) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO tts_voices (voice_id, name, category, label, source, fetched_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(voice_id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                label = excluded.label,
                source = excluded.source,
                fetched_at = datetime('now')
            """,
            (voice_id, name, category, label, source),
        )
