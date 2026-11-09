"""AI request ledger: every call stored, accepted calls replayed on resume, no stuck renders."""
import json
from types import SimpleNamespace

import pytest

import nalarka.core.llm as llm
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos import llm_calls as repo
from nalarka.db.repos.jobs import create_job, get_job, update_job_status, get_job_events
from nalarka.db.repos.topics import create_topic
from nalarka.db.repos.renders import (
    create_render, get_render, bind_render_to_job, end_unfinished_render_for_job,
)
from nalarka.services.llm_ledger import DbLLMRecorder

GOOD = '{"status": "ok", "note": "all good here, thanks for asking"}'
BAD = "Sorry, I cannot produce JSON right now but here is some long prose instead of it."


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        text = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(text, Exception):
            raise text
        if kw.get("stream"):
            mk = lambda t, fr: SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t), finish_reason=fr)], error=None)
            return iter([mk(text, None), mk("", "stop")])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")])


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "ledger.db"
    c = get_connection(p); run_migrations(c); seed_defaults(c); c.close()
    return p


@pytest.fixture(autouse=True)
def _reset_recorder():
    yield
    llm.set_llm_recorder(None)


def _call(client, monkeypatch, user="ping"):
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: client)
    return llm.call_ai_json("http://x/v1", "k", "m", "sys", user, lambda m: None, attempts=1)


def test_no_recorder_means_no_ledger_and_normal_behaviour(monkeypatch):
    client = FakeClient([GOOD])
    assert _call(client, monkeypatch)["status"] == "ok"
    assert client.calls == 1


def test_every_call_is_recorded_and_accepted_after_parse(db_path, monkeypatch):
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-1", None))
    _call(FakeClient([GOOD]), monkeypatch)
    c = get_connection(db_path)
    rows = repo.list_calls(c, job_id="job-1", with_text=True)
    assert len(rows) == 1 and rows[0]["status"] == "ok" and rows[0]["accepted"] == 1
    assert rows[0]["response_text"] == GOOD and rows[0]["system_prompt"] == "sys" and rows[0]["user_prompt"] == "ping"
    c.close()


def test_resume_replays_accepted_response_without_calling_the_api(db_path, monkeypatch):
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-1", None))
    _call(FakeClient([GOOD]), monkeypatch)

    second = FakeClient(["should never be used " * 5])
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-2", None, replay_job_ids=["job-1"]))
    out = _call(second, monkeypatch)
    assert out["status"] == "ok" and second.calls == 0
    c = get_connection(db_path)
    r = repo.list_calls(c, job_id="job-2")[0]
    assert r["status"] == "replayed" and r["accepted"] == 1
    s = repo.summary(c, job_id="job-2")
    assert s["replayed_calls"] == 1 and s["paid_calls"] == 0 and s["saved_resp_chars"] > 0
    c.close()
    # a later resume of the resume still finds it (chain through the replayed row)
    third = FakeClient(["nope " * 20])
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-3", None, replay_job_ids=["job-2", "job-1"]))
    _call(third, monkeypatch)
    assert third.calls == 0


def test_unusable_response_is_not_replayed(db_path, monkeypatch):
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-1", None))
    with pytest.raises(ValueError):
        _call(FakeClient([BAD]), monkeypatch)
    c = get_connection(db_path)
    assert repo.list_calls(c, job_id="job-1")[0]["accepted"] == 0
    c.close()
    retry = FakeClient([GOOD])
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-2", None, replay_job_ids=["job-1"]))
    assert _call(retry, monkeypatch)["status"] == "ok"
    assert retry.calls == 1  # asked the provider again


def test_provider_errors_are_recorded(db_path, monkeypatch):
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-1", None))
    with pytest.raises(Exception):
        _call(FakeClient([RuntimeError("boom 500")]), monkeypatch)
    c = get_connection(db_path)
    rows = repo.list_calls(c, job_id="job-1")
    assert rows and all(r["status"] == "error" for r in rows) and "boom" in rows[0]["error"]
    c.close()


def test_different_prompt_is_a_cache_miss(db_path, monkeypatch):
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-1", None))
    _call(FakeClient([GOOD]), monkeypatch, user="A")
    other = FakeClient([GOOD])
    llm.set_llm_recorder(DbLLMRecorder(db_path, "job-2", None, replay_job_ids=["job-1"]))
    _call(other, monkeypatch, user="B")
    assert other.calls == 1


def test_job_chain_follows_resume_of(db_path):
    c = get_connection(db_path)
    create_job(c, "a", "generate_script", "light", {})
    create_job(c, "b", "generate_script", "light", {"resume_of": "a"})
    create_job(c, "c", "generate_script", "light", {"resume_of": "b"})
    assert repo.job_chain(c, "c") == ["c", "b", "a"]
    assert repo.job_chain(c, "a") == ["a"]
    c.close()


def test_jobrunner_records_then_resume_job_replays(db_path, monkeypatch):
    """End to end through run_job (kind test_ai calls the LLM once)."""
    from nalarka.jobs.jobrunner import run_job
    first, second = FakeClient([GOOD]), FakeClient(["unused " * 10])
    clients = iter([first, second])
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: next(clients))
    c = get_connection(db_path)
    create_job(c, "t-1", "test_ai", "light", {})
    create_job(c, "t-2", "test_ai", "light", {"resume_of": "t-1"})
    c.close()
    assert run_job("t-1", str(db_path)) == 0
    assert run_job("t-2", str(db_path)) == 0
    assert first.calls == 1 and second.calls == 0
    c = get_connection(db_path)
    assert repo.summary(c, job_id="t-1")["paid_calls"] == 1
    assert repo.summary(c, job_id="t-2")["replayed_calls"] == 1
    logs = " ".join(e["message"] or "" for e in get_job_events(c, "t-2"))
    assert "[RESUME]" in logs and "Replayed saved AI response" in logs
    c.close()


def test_failed_job_does_not_leave_render_running(db_path):
    c = get_connection(db_path)
    tid = create_topic(c, title="T", language="Indonesian", max_duration=60, notes=None)
    create_job(c, "r-1", "full_render", "heavy", {}, topic_id=tid)
    rid = create_render(c, topic_id=tid)
    bind_render_to_job(c, rid, "r-1")
    c.execute("UPDATE renders SET status='running' WHERE id=?", (rid,)); c.commit()
    end_unfinished_render_for_job(c, "r-1", "failed", "boom")
    r = get_render(c, rid)
    assert r["status"] == "failed" and r["error"] == "boom" and r["finished_at"]
    assert get_job(c, "r-1")["render_id"] == rid
    # finished renders are left alone
    c.execute("UPDATE renders SET status='succeeded', error=NULL WHERE id=?", (rid,)); c.commit()
    end_unfinished_render_for_job(c, "r-1", "failed", "late")
    assert get_render(c, rid)["status"] == "succeeded"
    c.close()


def test_cancel_closes_the_render_too(db_path):
    import nalarka.jobs.dispatcher as d
    c = get_connection(db_path)
    tid = create_topic(c, title="T", language="Indonesian", max_duration=60, notes=None)
    create_job(c, "r-2", "full_render", "heavy", {}, topic_id=tid)
    rid = create_render(c, topic_id=tid)
    bind_render_to_job(c, rid, "r-2")
    update_job_status(c, "r-2", status="queued")
    c.close()
    assert d.JobDispatcher(db_path=str(db_path)).cancel_job("r-2")
    c = get_connection(db_path)
    assert get_render(c, rid)["status"] == "canceled"
    c.close()


def test_saved_transcript_is_reused_only_when_audio_matches(tmp_path):
    from nalarka.services.pipeline import _save_cached_words, _load_cached_words
    vo = tmp_path / "a.wav"; vo.write_bytes(b"1234")
    words = [{"w": "halo", "start": 0.0, "end": 0.4}]
    _save_cached_words(tmp_path, vo, "base", "id", words)
    log = []
    assert _load_cached_words(tmp_path, vo, "base", "id", log.append) == words
    assert _load_cached_words(tmp_path, vo, "small", "id", log.append) is None
    vo.write_bytes(b"12345678")
    assert _load_cached_words(tmp_path, vo, "base", "id", log.append) is None
    assert _load_cached_words(None, vo, "base", "id", log.append) is None


# ---------------- API level (needs fastapi + httpx) ----------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi"); pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from nalarka.api.main import create_app
    p = tmp_path / "api.db"
    c = get_connection(str(p)); run_migrations(c); seed_defaults(c)
    create_job(c, "failed-1", "generate_script", "light", {"x": 1})
    update_job_status(c, "failed-1", status="failed", error="boom")
    create_job(c, "ok-1", "generate_script", "light", {})
    update_job_status(c, "ok-1", status="succeeded")
    c.close()
    monkeypatch.setenv("NALARKA_DB_PATH", str(p))
    monkeypatch.setenv("NALARKA_DATA_DIR", str(tmp_path / "data"))
    with TestClient(create_app()) as tc:
        # keep the real dispatcher from spawning workers in this test
        from nalarka.api import deps
        deps.get_dispatcher().dispatch_next = lambda: None
        yield tc


def test_api_resume_rules(api):
    assert api.post("/api/jobs/ok-1/resume").status_code == 409
    assert api.post("/api/jobs/nope/resume").status_code == 404
    r = api.post("/api/jobs/failed-1/resume")
    assert r.status_code == 202 and r.json()["resume_of"] == "failed-1"
    new_id = r.json()["job_id"]
    job = api.get(f"/api/jobs/{new_id}").json()
    assert job["params"]["resume_of"] == "failed-1" and job["params"]["x"] == 1
    assert api.post("/api/jobs/failed-1/resume").status_code == 409  # already being resumed


def test_api_job_log_and_llm_calls(api):
    log = api.get("/api/jobs/failed-1/log").json()
    assert log["status"] == "failed" and log["error"] == "boom" and "events" in log
    calls = api.get("/api/jobs/failed-1/llm-calls").json()
    assert calls["summary"]["total"] == 0 and calls["calls"] == []
    assert api.get("/api/llm-calls").status_code == 200
