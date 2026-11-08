"""Security and secrets tests for Nalarka Web API."""
import pytest
from fastapi.testclient import TestClient

from nalarka.api.main import create_app
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.services.secrets import encrypt_secret, decrypt_secret, redact_secrets


@pytest.fixture
def client(tmp_path, monkeypatch):
    test_db = tmp_path / "test_sec.db"
    conn = get_connection(str(test_db))
    run_migrations(conn)
    seed_defaults(conn)
    conn.close()

    monkeypatch.setenv("NALARKA_DB_PATH", str(test_db))
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_encryption_roundtrip():
    secret = "sk-ant-api03-very-secret-token-123456789"
    enc = encrypt_secret(secret)
    assert enc != secret.encode()
    dec = decrypt_secret(enc)
    assert dec == secret


def test_redact_secrets():
    token = "sk-openrouter-secret-key-999"
    log_text = f"Connecting to provider with authorization header: Bearer {token} now"
    redacted = redact_secrets(log_text, [token])
    assert token not in redacted
    assert "Bearer [REDACTED] now" in redacted


def test_api_keys_never_exposed_in_providers(client):
    secret_key = "super-secret-key-for-test"
    # Update provider with a secret key
    put_res = client.put(
        "/api/providers/custom",
        json={"api_key": secret_key, "coder_model": "test-coder", "is_active": True},
    )
    assert put_res.status_code == 200

    # Get providers list
    res = client.get("/api/providers")
    assert res.status_code == 200
    providers = res.json()
    for p in providers:
        assert "api_key" not in p
        if p["key"] == "custom":
            assert p["has_key"] is True

    # Check that plain secret key never appears in GET /api/config
    cfg_res = client.get("/api/config")
    assert cfg_res.status_code == 200
    assert secret_key not in cfg_res.text


def test_preview_image_path_traversal_blocked(client):
    # Attempt to read non-existent or outside path
    res = client.get("/api/renders/preview-image?path=../../../../etc/passwd")
    assert res.status_code == 404
