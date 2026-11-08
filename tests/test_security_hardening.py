"""Regression tests for the security review fixes (CORS/Host/token, export leak,
path whitelist, dispatcher race). No fastapi needed except where noted."""
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nalarka.api.security import LocalSecurityMiddleware
from nalarka.services.paths import (
    is_within, resolve_media_path, safe_output_name, media_roots,
)
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.settings import set_provider
from nalarka.db.repos.jobs import create_job, get_job
from nalarka.services.config_importer import export_legacy_config


# ---------- middleware (manual ASGI harness, no httpx required) ----------
async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _call(mw, path="/api/config", method="GET", headers=None, query=b""):
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    out = {}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = dict(msg.get("headers", []))

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": method, "path": path, "headers": hdrs,
             "query_string": query, "scheme": "http", "server": ("127.0.0.1", 8000)}
    asyncio.run(mw(scope, receive, send))
    return out["status"], out.get("headers", {})


def test_host_header_must_be_local():
    mw = LocalSecurityMiddleware(_ok_app, token="")
    assert _call(mw, headers={"host": "127.0.0.1:8000"})[0] == 200
    assert _call(mw, headers={"host": "localhost"})[0] == 200
    assert _call(mw, headers={"host": "[::1]:8000"})[0] == 200
    assert _call(mw, headers={"host": "evil.example.com"})[0] == 400  # DNS rebinding


def test_cross_origin_requests_blocked():
    mw = LocalSecurityMiddleware(_ok_app, token="")
    h = {"host": "127.0.0.1:8000"}
    assert _call(mw, method="POST", headers={**h, "origin": "http://127.0.0.1:8000"})[0] == 200
    assert _call(mw, method="POST", headers={**h, "origin": "http://localhost:5173"})[0] == 200
    assert _call(mw, method="POST", headers={**h, "origin": "https://evil.example.com"})[0] == 403
    assert _call(mw, method="POST", headers={**h, "origin": "null"})[0] == 403


def test_optional_token():
    mw = LocalSecurityMiddleware(_ok_app, token="s3cret")
    h = {"host": "localhost"}
    assert _call(mw, headers=h)[0] == 401
    assert _call(mw, headers={**h, "x-nalarka-token": "nope"})[0] == 401
    assert _call(mw, headers={**h, "x-nalarka-token": "s3cret"})[0] == 200
    assert _call(mw, headers={**h, "authorization": "Bearer s3cret"})[0] == 200
    assert _call(mw, headers={**h, "cookie": "nalarka_token=s3cret"})[0] == 200
    assert _call(mw, path="/static/app.js", headers=h)[0] == 200  # UI assets stay public
    st, hd = _call(mw, path="/auth", headers=h, query=b"token=s3cret")
    assert st == 204 and b"httponly" in hd[b"set-cookie"].lower()
    assert _call(mw, path="/auth", headers=h, query=b"token=bad")[0] == 401


# ---------- export no longer leaks keys ----------
@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "sec.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def test_export_without_secrets_has_no_plaintext_keys(db):
    set_provider(db, "openrouter", api_key="sk-or-SUPERSECRET")
    out = export_legacy_config(db, include_secrets=False)
    assert "SUPERSECRET" not in str(out)
    assert out["providers"]["openrouter"]["api_key"] == ""
    assert out["elevenlabs_api_key"] == ""
    # explicit opt-in still works
    out2 = export_legacy_config(db, include_secrets=True)
    assert out2["providers"]["openrouter"]["api_key"] == "sk-or-SUPERSECRET"


# ---------- path whitelist ----------
def test_safe_output_name():
    assert safe_output_name("../../etc/cron.d/x.mp4") == "x.mp4"
    assert safe_output_name("/abs/evil.mp4") == "evil.mp4"
    assert safe_output_name("..\\..\\win.mp4") == "win.mp4"
    assert safe_output_name("..") == "output_motion_graphic.mp4"
    assert safe_output_name("") == "output_motion_graphic.mp4"
    assert safe_output_name("my video") == "my video.mp4"
    assert safe_output_name("a;rm -rf.mp4") == "a_rm -rf.mp4"


def test_media_path_must_be_inside_roots(tmp_path, monkeypatch):
    data = tmp_path / "data"
    lib = tmp_path / "lib"
    outside = tmp_path / "secret"
    for d in (data, lib, outside):
        d.mkdir()
    (data / "a.png").write_bytes(b"x")
    (lib / "b.png").write_bytes(b"x")
    (outside / "c.png").write_bytes(b"x")
    monkeypatch.setenv("NALARKA_DATA_DIR", str(data))
    monkeypatch.delenv("NALARKA_MEDIA_DIRS", raising=False)
    roots = media_roots([str(lib)])
    assert resolve_media_path(data / "a.png", roots)
    assert resolve_media_path(lib / "b.png", roots)
    with pytest.raises(ValueError):
        resolve_media_path(outside / "c.png", roots)
    with pytest.raises(ValueError):
        resolve_media_path(data / ".." / "secret" / "c.png", roots)
    with pytest.raises(ValueError):
        resolve_media_path("", roots)
    # symlink escaping the root is resolved before the check
    link = data / "link.png"
    try:
        link.symlink_to(outside / "c.png")
        assert not is_within(link, roots)
    except OSError:
        pass  # symlinks unavailable (e.g. Windows without privileges)


# ---------- dispatcher race ----------
def test_dispatch_next_spawns_job_once_under_concurrency(tmp_path, monkeypatch):
    import nalarka.jobs.dispatcher as d

    db_path = tmp_path / "race.db"
    conn = get_connection(db_path)
    run_migrations(conn)
    seed_defaults(conn)
    create_job(conn, "scan-race", kind="scan_library", weight="light", params={})
    conn.close()

    spawned = []

    class FakeProc:
        pid = 4242
        def poll(self): return None
        def terminate(self): pass
        def kill(self): pass
        def wait(self, timeout=None): return 0

    def fake_popen(cmd, *a, **kw):
        spawned.append(cmd)
        time.sleep(0.25)  # widen the window between "check" and "mark running"
        return FakeProc()

    monkeypatch.setattr(d.subprocess, "Popen", fake_popen)
    disp = d.JobDispatcher(db_path=str(db_path))
    threads = [threading.Thread(target=disp.dispatch_next) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert len(spawned) == 1, f"job spawned {len(spawned)} times"
    c = get_connection(db_path)
    assert get_job(c, "scan-race")["status"] == "running"
    c.close()


# ---------- API level (needs fastapi + httpx; skipped otherwise) ----------
@pytest.fixture
def api_client(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from nalarka.api.main import create_app

    test_db = tmp_path / "api.db"
    c = get_connection(str(test_db)); run_migrations(c); seed_defaults(c)
    set_provider(c, "openrouter", api_key="sk-or-LEAKCHECK"); c.close()
    monkeypatch.setenv("NALARKA_DB_PATH", str(test_db))
    monkeypatch.setenv("NALARKA_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    with TestClient(create_app()) as tc:
        yield tc


def test_api_export_does_not_leak(api_client):
    r = api_client.get("/api/config/export?include_secrets=0")
    assert "LEAKCHECK" not in r.text


def test_api_preview_image_outside_data_dir_404(api_client, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG")
    assert api_client.get("/api/renders/preview-image", params={"path": str(outside)}).status_code == 404
    inside = tmp_path / "data" / "ok.png"
    inside.write_bytes(b"\x89PNG")
    assert api_client.get("/api/renders/preview-image", params={"path": str(inside)}).status_code == 200


def test_api_rejects_foreign_origin_and_host(api_client):
    assert api_client.get("/healthz", headers={"Origin": "https://evil.example"}).status_code == 403
    assert api_client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400


# =================== patch 2 ===================
import io
import os
import signal
import subprocess
import sys

from nalarka.services.paths import safe_upload_name, save_upload
from nalarka.services.settings_validation import validate_settings
from nalarka.db.repos.settings import get_provider


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_kill_process_tree_kills_grandchildren(tmp_path):
    from nalarka.jobs.dispatcher import kill_process_tree, _popen_group_kwargs
    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess,sys,time;"
        f"c=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        f"open(r'{pidfile}','w').write(str(c.pid));time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], **_popen_group_kwargs())
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        time.sleep(0.05)
    child = int(pidfile.read_text())
    os.kill(child, 0)  # alive
    kill_process_tree(proc)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
    assert proc.poll() is not None


def test_workers_spawn_in_own_group(tmp_path, monkeypatch):
    import nalarka.jobs.dispatcher as d
    seen = {}

    class P:
        pid = 1
        def poll(self): return None

    monkeypatch.setattr(d.subprocess, "Popen", lambda cmd, **kw: seen.update(kw) or P())
    c = get_connection(tmp_path / "g.db"); run_migrations(c); seed_defaults(c)
    create_job(c, "scan-g", kind="scan_library", weight="light", params={}); c.close()
    d.JobDispatcher(db_path=str(tmp_path / "g.db")).dispatch_next()
    assert seen.get("start_new_session") is True or "creationflags" in seen


def test_save_upload_limit_and_atomic(tmp_path):
    dest = tmp_path / "a.mp4"
    assert save_upload(io.BytesIO(b"x" * 100), dest, max_bytes=100) == 100
    assert dest.read_bytes() == b"x" * 100
    with pytest.raises(ValueError):
        save_upload(io.BytesIO(b"y" * 101), dest, max_bytes=100, chunk=10)
    assert dest.read_bytes() == b"x" * 100          # old file untouched
    assert not list(tmp_path.glob("*.part"))         # no partial left


def test_safe_upload_name():
    assert safe_upload_name("../../evil.mp4") == "evil.mp4"
    assert safe_upload_name("C:\\x\\clip one.MP4") == "clip one.MP4"
    assert safe_upload_name("..") == "upload"
    assert safe_upload_name("a b;c.mp3") == "a b_c.mp3"


def test_validate_settings(tmp_path):
    ok, err = validate_settings({"render.max_duration": "9999", "render.workers": 0,
                                 "render.synth_bgm": "false", "tts.stability": 5})
    assert not err
    assert ok == {"render.max_duration": 900, "render.workers": 1,
                  "render.synth_bgm": False, "tts.stability": 1.0}
    _, err = validate_settings({"tts.elevenlabs_api_key_enc": "x"})
    assert err and "unknown" in err[0]
    _, err = validate_settings({"render.max_duration": "abc", "render.workers": None})
    assert len(err) == 2                              # 422, not a 500
    ok, _ = validate_settings({"render.output_name": "../../x"})
    assert ok["render.output_name"] == "x.mp4"
    _, err = validate_settings({"library.dir": str(tmp_path / "missing")})
    assert err
    ok, err = validate_settings({"library.dir": str(tmp_path)})
    assert not err


def test_base_url_change_drops_stored_key(db):
    set_provider(db, "openrouter", api_key="sk-or-KEEP")
    # same URL (even with trailing slash) keeps the key
    set_provider(db, "openrouter", base_url=get_provider(db, "openrouter")["base_url"] + "/")
    assert get_provider(db, "openrouter")["api_key"] == "sk-or-KEEP"
    # a different endpoint must not inherit it
    set_provider(db, "openrouter", base_url="https://attacker.example/v1")
    assert not get_provider(db, "openrouter")["has_key"]
    # ...unless the caller supplies a key together with the URL
    set_provider(db, "openrouter", base_url="https://ok.example/v1", api_key="sk-new")
    assert get_provider(db, "openrouter")["api_key"] == "sk-new"
