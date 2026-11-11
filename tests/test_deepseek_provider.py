"""DeepSeek direct provider: seeded, importable, key-safe, error hints, output-limit handling, model listing."""
import io
import json
from types import SimpleNamespace

import pytest

import nalarka.core.llm as llm
from nalarka.core.constants import PROVIDER_DEFAULT_BASE_URL, PROVIDER_HINTS
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.settings import get_provider, set_provider, list_providers, get_models_catalog
from nalarka.services.config_importer import import_legacy_config_dict, export_legacy_config

OK_JSON = '{"status": "ok", "note": "all good here, thanks for asking"}'


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "ds.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


class Client:
    """Records the kwargs of each request; replies from a script of texts / exceptions."""
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.seen.append(kw)
        r = self.replies[min(len(self.seen) - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        if kw.get("stream"):
            mk = lambda t, fr: SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t), finish_reason=fr)], error=None)
            return iter([mk(r, None), mk("", "stop")])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=r), finish_reason="stop")])


def test_deepseek_is_seeded_with_direct_base_url_and_models(db):
    p = get_provider(db, "deepseek")
    assert p and p["base_url"] == "https://api.deepseek.com" == PROVIDER_DEFAULT_BASE_URL["deepseek"]
    assert not p["is_active"] and not p["has_key"]
    assert p["coder_model"] and p["script_model"]
    ids = {m["model_id"] for m in get_models_catalog(db, provider_key="deepseek", role="coder")}
    assert p["coder_model"] in ids
    assert "deepseek" in PROVIDER_HINTS
    # the other providers are untouched, and seeding twice changes nothing
    assert {x["key"] for x in list_providers(db)} >= {"custom", "openrouter", "deepseek"}
    seed_defaults(db)
    assert len(get_models_catalog(db, provider_key="deepseek", role="coder")) == len(ids)
    assert sum(1 for x in list_providers(db) if x["key"] == "deepseek") == 1


def test_existing_database_gets_deepseek_on_next_start(tmp_path):
    conn = get_connection(tmp_path / "old.db"); run_migrations(conn); seed_defaults(conn)
    conn.execute("DELETE FROM ai_providers WHERE key = 'deepseek'"); conn.commit()   # a database made before this version
    assert get_provider(conn, "deepseek") is None
    seed_defaults(conn)
    assert get_provider(conn, "deepseek")["base_url"] == "https://api.deepseek.com"
    conn.close()


def test_is_deepseek_and_env_key_name(monkeypatch):
    assert llm.is_deepseek("https://api.deepseek.com") and llm.is_deepseek("https://api.deepseek.com/v1")
    assert not llm.is_deepseek("https://openrouter.ai/api/v1") and not llm.is_deepseek("")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-ds")
    captured = {}
    monkeypatch.setattr(llm, "OpenAI", lambda **kw: captured.update(kw) or object())
    llm.make_client("https://api.deepseek.com", "")
    assert captured["api_key"] == "sk-env-ds" and "default_headers" not in captured   # no OpenRouter headers
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        llm.make_client("https://api.deepseek.com", "")


def test_stored_key_is_not_sent_to_another_host(db):
    set_provider(db, "deepseek", api_key="sk-ds-secret")
    assert get_provider(db, "deepseek")["api_key"] == "sk-ds-secret"
    set_provider(db, "deepseek", base_url="https://api.deepseek.com/")        # same host, trailing slash: key stays
    assert get_provider(db, "deepseek")["api_key"] == "sk-ds-secret"
    set_provider(db, "deepseek", base_url="https://evil.example/v1")           # other host: key is dropped
    assert not get_provider(db, "deepseek")["has_key"]


def test_config_import_export_roundtrip_with_deepseek_and_no_key_leak(db):
    import_legacy_config_dict(db, {
        "ai_provider": "deepseek",
        "providers": {"deepseek": {"base_url": "https://api.deepseek.com", "api_key": "sk-ds-1", "coder_model": "deepseek-v4-pro", "script_model": "deepseek-flash"}},
    })
    p = get_provider(db, "deepseek")
    assert p["is_active"] and p["api_key"] == "sk-ds-1" and p["coder_model"] == "deepseek-v4-pro"
    assert export_legacy_config(db)["ai_provider"] == "deepseek"
    assert export_legacy_config(db)["providers"]["deepseek"]["api_key"] == ""      # keys are not exported by default
    # legacy flat config pointing at DeepSeek lands on the deepseek provider, not "custom"
    import_legacy_config_dict(db, {"base_url": "https://api.deepseek.com", "api_key": "sk-flat", "model": "deepseek-flash"})
    assert get_provider(db, "deepseek")["api_key"] == "sk-flat"


@pytest.mark.parametrize("code,needle", [(402, "balance"), (429, "rate limiting"), (503, "overloaded"), (401, "DeepSeek rejected the API key")])
def test_friendly_errors_name_deepseek_and_say_what_to_do(code, needle):
    err = type("E", (Exception,), {"status_code": code})(f"Error code: {code}")
    assert needle in llm.friendly_api_error(err, "https://api.deepseek.com")
    # other providers keep their own wording
    assert "DeepSeek" not in llm.friendly_api_error(err, "https://openrouter.ai/api/v1")


def test_output_limit_error_retries_with_the_allowed_maximum(monkeypatch):
    limit_err = Exception("Error code: 400 - Invalid max_tokens value, the valid range of max_tokens is [1, 8192]")
    client = Client([limit_err, OK_JSON])
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: client)
    out = llm.call_ai_json("https://api.deepseek.com", "k", "deepseek-flash", "sys", "ping", lambda m: None, attempts=1, max_tokens=32000)
    assert out["status"] == "ok"
    assert client.seen[0]["max_tokens"] == 32000
    assert client.seen[1]["max_tokens"] == 8192            # clamped to the limit the provider reported, not dropped


def test_fetch_provider_models_reads_models_and_sends_key_only_to_base_url(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return Resp(json.dumps({"data": [{"id": "deepseek-v4-pro"}, {"id": "deepseek-flash"}, {"object": "no id"}]}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    models = llm.fetch_provider_models("https://api.deepseek.com/", "sk-ds", lambda m: None)
    assert [m["id"] for m in models] == ["deepseek-flash", "deepseek-v4-pro"]
    assert seen["url"] == "https://api.deepseek.com/models" and seen["auth"] == "Bearer sk-ds"
    with pytest.raises(ValueError, match="API key is empty"):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        llm.fetch_provider_models("https://api.deepseek.com", "", lambda m: None)


def test_migration_0003_keeps_existing_providers_keys_and_catalog(tmp_path):
    """A database made before DeepSeek (CHECK key IN custom/openrouter) is upgraded without losing anything."""
    import sqlite3
    from nalarka.db import connection as dbc
    old_dir = tmp_path / "mig_old"; old_dir.mkdir()
    for f in sorted(dbc.DEFAULT_MIGRATIONS_DIR.glob("000[12]_*.sql")):
        (old_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    conn = get_connection(tmp_path / "pre.db")
    assert run_migrations_dir(conn, old_dir) == 2
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ai_providers (key,label,base_url,coder_model,script_model) VALUES ('deepseek','x','u','c','s')")
    conn.execute("INSERT INTO ai_providers (key,label,base_url,api_key_enc,coder_model,script_model,is_active) VALUES ('custom','C','http://c',x'0102','cm','sm',1)")
    conn.execute("INSERT INTO ai_providers (key,label,base_url,coder_model,script_model) VALUES ('openrouter','O','http://o','cm2','sm2')")
    conn.execute("INSERT INTO model_catalog (provider_key,model_id,role,source) VALUES ('custom','m1','coder','preset')")
    conn.execute("INSERT INTO model_catalog (provider_key,model_id,role,source) VALUES ('openrouter','m2','script','fetched')")
    conn.commit()

    assert run_migrations(conn) >= 1                                  # applies 0003 (and anything newer)
    rows = {r["key"]: r for r in conn.execute("SELECT * FROM ai_providers")}
    assert set(rows) == {"custom", "openrouter"} and rows["custom"]["is_active"] == 1 and bytes(rows["custom"]["api_key_enc"]) == b"\x01\x02"
    assert conn.execute("SELECT COUNT(*) FROM model_catalog").fetchone()[0] == 2        # not cascaded away
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("INSERT INTO ai_providers (key,label,base_url,coder_model,script_model) VALUES ('deepseek','D','u','c','s')")
    with pytest.raises(sqlite3.IntegrityError):                       # still one active provider at most, still sane keys
        conn.execute("UPDATE ai_providers SET is_active = 1 WHERE key = 'deepseek'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ai_providers (key,label,base_url,coder_model,script_model) VALUES ('Bad Key!','D','u','c','s')")
    conn.close()


def run_migrations_dir(conn, d):
    return run_migrations(conn, d)
