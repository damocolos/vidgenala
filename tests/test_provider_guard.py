"""Stop point: too many failed AI calls / too much AI time ends the job, resumable."""
import pytest

import nalarka.core.llm as llm
from nalarka.core.llm_guard import ProviderGuard, ProviderCircuitOpen, set_llm_guard, short_error
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.jobs import create_job, get_job, get_job_events
from nalarka.db.repos.settings import set_setting
from nalarka.services.llm_ledger import DbLLMRecorder
from tests.test_ai_ledger import FakeClient, GOOD


@pytest.fixture(autouse=True)
def _reset():
    yield
    set_llm_guard(None)
    llm.set_llm_recorder(None)


def _boom(code=524):
    e = RuntimeError("Error code: %d - origin_response_timeout " % code + "x" * 2000)
    e.status_code = code
    return e


def test_consecutive_failures_trip_and_success_resets():
    g = ProviderGuard(max_consecutive=3, max_total=0, max_minutes=0)
    for _ in range(2):
        g.before_call(); g.on_failure(_boom())
    g.before_call(); g.on_success()                 # a good call resets the streak
    for _ in range(2):
        g.before_call(); g.on_failure(_boom())
    g.before_call()
    with pytest.raises(ProviderCircuitOpen) as ei:
        g.on_failure(_boom())
    assert "3 calls in a row" in str(ei.value) and "Resume" in str(ei.value)
    assert len(str(ei.value)) < 600                  # the huge Cloudflare JSON is shortened
    with pytest.raises(ProviderCircuitOpen):         # open: nothing else is sent
        g.before_call()


def test_total_failures_and_time_budget(monkeypatch):
    g = ProviderGuard(max_consecutive=0, max_total=3, max_minutes=0)
    for i in range(2):
        g.before_call(); g.on_failure(_boom()); g.on_success()
    g.before_call()
    with pytest.raises(ProviderCircuitOpen) as ei:
        g.on_failure(_boom())
    assert "3 calls in this run" in str(ei.value)

    import nalarka.core.llm_guard as lg
    now = [1000.0]
    monkeypatch.setattr(lg.time, "time", lambda: now[0])
    t = ProviderGuard(max_consecutive=0, max_total=0, max_minutes=10)
    t.before_call()
    now[0] += 9 * 60; t.before_call()
    now[0] += 2 * 60
    with pytest.raises(ProviderCircuitOpen) as ei:
        t.before_call()
    assert "over 10 minutes" in str(ei.value)


def test_limits_can_be_switched_off():
    g = ProviderGuard(max_consecutive=0, max_total=0, max_minutes=0)
    for _ in range(50):
        g.before_call(); g.on_failure(_boom())


def test_circuit_open_is_not_swallowed_by_except_exception():
    swallowed = False
    try:
        try:
            raise ProviderCircuitOpen("stop")
        except Exception:
            swallowed = True
    except ProviderCircuitOpen:
        pass
    assert not swallowed


def test_short_error_has_status_code():
    assert short_error(_boom(524)).startswith("HTTP 524")


def test_chat_text_counts_failures_and_stops_calling_provider(monkeypatch):
    client = FakeClient([_boom(), _boom(), _boom()])
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: client)
    set_llm_guard(ProviderGuard(max_consecutive=2, max_total=0, max_minutes=0))
    with pytest.raises(RuntimeError):
        llm.call_ai_json("http://x/v1", "k", "m", "s", "u1", lambda m: None, attempts=1)
    before = client.calls
    with pytest.raises(ProviderCircuitOpen):         # 2nd failed call trips it
        llm.call_ai_json("http://x/v1", "k", "m", "s", "u2", lambda m: None, attempts=1)
    after_trip = client.calls
    assert after_trip > before
    with pytest.raises(ProviderCircuitOpen):         # breaker open: no new request leaves the machine
        llm.call_ai_json("http://x/v1", "k", "m", "s", "u3", lambda m: None, attempts=1)
    assert client.calls == after_trip


def test_replayed_answers_do_not_count(tmp_path, monkeypatch):
    p = tmp_path / "g.db"
    c = get_connection(p); run_migrations(c); seed_defaults(c)
    create_job(c, "a", "test_ai", "light", {}); create_job(c, "b", "test_ai", "light", {"resume_of": "a"}); c.close()
    ok = FakeClient([GOOD])
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: ok)
    llm.set_llm_recorder(DbLLMRecorder(str(p), "a", None, replay_job_ids=[]))
    llm.call_ai_json("http://x/v1", "k", "m", "s", "same", lambda m: None, attempts=1)
    llm.set_llm_recorder(DbLLMRecorder(str(p), "b", None, replay_job_ids=["a"]))
    g = ProviderGuard(max_consecutive=1, max_total=1, max_minutes=0)
    set_llm_guard(g)
    llm.call_ai_json("http://x/v1", "k", "m", "s", "same", lambda m: None, attempts=1)  # replay
    assert g.total == 0 and g.ok_calls == 0 and ok.calls == 1


def test_job_stops_with_resumable_status(tmp_path, monkeypatch):
    from nalarka.jobs.jobrunner import run_job
    p = tmp_path / "j.db"
    c = get_connection(p); run_migrations(c); seed_defaults(c)
    set_setting(c, "ai.max_consecutive_failures", 1)
    create_job(c, "stop-1", "test_ai", "light", {}); c.close()
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: FakeClient([_boom(524)]))
    assert run_job("stop-1", str(p)) == 3
    c = get_connection(p)
    j = get_job(c, "stop-1")
    assert j["status"] == "failed" and j["error"].startswith("The AI provider failed 1 calls in a row")
    assert "Resume" in j["error"] and "Stopped" in (j["status_text"] or "")
    logs = " ".join(e["message"] or "" for e in get_job_events(c, "stop-1"))
    assert "[STOPPED]" in logs
    c.close()


def test_new_settings_are_validated():
    from nalarka.services.settings_validation import validate_settings
    ok, err = validate_settings({"ai.max_consecutive_failures": "3", "ai.max_total_failures": 999, "ai.max_minutes_per_run": -5})
    assert not err and ok == {"ai.max_consecutive_failures": 3, "ai.max_total_failures": 100, "ai.max_minutes_per_run": 0}


def test_stop_unwinds_through_parallel_batch_pool():
    """Pass 2 runs batches via ThreadPoolExecutor.pool.map inside `except Exception` handlers."""
    from concurrent.futures import ThreadPoolExecutor

    def worker(_):
        try:
            raise ProviderCircuitOpen("stop")
        except Exception:      # what the per-batch handler does: must NOT catch the stop
            return "swallowed"

    with pytest.raises(ProviderCircuitOpen):
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(worker, [1, 2, 3]))
