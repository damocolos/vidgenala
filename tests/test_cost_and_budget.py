"""Token/cost tracking, budget cap, per-stage models, reuse of earlier answers, beat status strip."""
import json
from types import SimpleNamespace

import pytest

import nalarka.core.llm as llm
from nalarka.core.llm_guard import ProviderGuard, ProviderCircuitOpen, parse_prices, estimate_cost, set_llm_guard
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos import llm_calls as repo
from nalarka.db.repos.topics import create_topic
from nalarka.db.repos.settings import set_setting
from nalarka.db.repos.storyboards import list_beats_for_storyboard, beat_state_summary
from nalarka.services.llm_ledger import DbLLMRecorder, cost_report
from nalarka.services.plan_assembler import save_plan, sync_code
from nalarka.services.settings_validation import validate_settings

PRICES = '{"cheap": [0.5, 1.5], "dear": [5, 15]}'


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "c.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def test_parse_prices_skips_bad_entries_and_never_guesses():
    p = parse_prices('{"a": [1, 2], "b": ["x", 1], "c": [1], "d": [-1, 2], "": [1, 1]}')
    assert p == {"a": (1.0, 2.0)}
    assert parse_prices("not json") == {} and parse_prices("") == {}
    assert estimate_cost(p, "a", 1_000_000, 500_000) == pytest.approx(2.0)
    assert estimate_cost(p, "unknown-model", 10, 10) is None          # no price -> no dollar figure


def test_token_budget_stops_the_run_after_the_call_that_crosses_it():
    g = ProviderGuard(max_tokens=1000)
    g.on_usage("m", 300, 200)
    with pytest.raises(ProviderCircuitOpen, match="budget of 1,000"):
        g.on_usage("m", 400, 200)
    with pytest.raises(ProviderCircuitOpen):                           # stays tripped: no further calls
        g.before_call()


def test_cost_budget_counts_priced_models_only():
    g = ProviderGuard(max_cost_usd=1.0, prices=parse_prices(PRICES))
    g.on_usage("unpriced", 10_000_000, 10_000_000)                      # unknown price: tokens counted, dollars not
    assert g.cost_used == 0 and "unpriced" in g.unpriced_models
    with pytest.raises(ProviderCircuitOpen, match=r"\$1\.00"):
        g.on_usage("dear", 200_000, 0)                                  # $1.00


def test_budget_off_by_default():
    g = ProviderGuard()
    g.on_usage("m", 10**9, 10**9)
    g.before_call()


def test_chat_text_counts_reported_tokens_against_the_budget(monkeypatch):
    def fake_impl(*a, **k):
        llm._TLS.usage = (400, 100)
        return "x" * 50, "stop"
    monkeypatch.setattr(llm, "_chat_text_impl", fake_impl)
    llm.set_llm_recorder(None)
    g = ProviderGuard(max_tokens=600)
    set_llm_guard(g)
    try:
        llm._chat_text("u", "k", "m", "sys", "usr", lambda m: None, "T")
        assert g.tokens_used == 500
        with pytest.raises(ProviderCircuitOpen):
            llm._chat_text("u", "k", "m", "sys", "usr", lambda m: None, "T")
    finally:
        set_llm_guard(None)


def test_chat_text_estimates_tokens_when_provider_reports_none(monkeypatch):
    monkeypatch.setattr(llm, "_chat_text_impl", lambda *a, **k: ("y" * 400, "stop"))
    llm.set_llm_recorder(None)
    g = ProviderGuard()
    set_llm_guard(g)
    try:
        llm._chat_text("u", "k", "m", "s" * 200, "u" * 200, lambda m: None, "T")
        assert g.tokens_used == 100 + 100
    finally:
        set_llm_guard(None)


def test_usage_capture_from_stream_and_stream_options_fallback(monkeypatch):
    seen = []

    def create(**kw):
        seen.append(kw)
        if kw.get("stream") and "stream_options" in kw:
            raise Exception("Unknown parameter: stream_options")
        mk = lambda t, fr, u=None: SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t), finish_reason=fr)], error=None, usage=u)
        return iter([mk("hello " * 10, None), mk("", "stop", SimpleNamespace(prompt_tokens=7, completion_tokens=9))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: client)
    llm.set_llm_recorder(None)
    text, _ = llm._chat_text_impl("http://x/v1", "k", "m", "s", "u", lambda m: None, "T")
    assert text.startswith("hello") and llm._TLS.usage == (7, 9)
    assert "stream_options" in seen[0] and "stream_options" not in seen[1]      # dropped after the provider refused it


def test_ledger_stores_tokens_and_cost_report_uses_only_user_prices(db):
    set_setting(db, "ai.prices", PRICES)
    tid = create_topic(db, "T", max_duration=30)
    base = dict(job_id="j1", topic_id=tid, tag="t", base_url="u", request_hash="h", system_prompt="s" * 40, user_prompt="u" * 40,
                temperature=0.4, max_tokens=None)
    repo.record_call(db, model="cheap", status="ok", response_text="r" * 80, prompt_tokens=1_000_000, completion_tokens=1_000_000, accepted=True, **base)
    repo.record_call(db, model="cheap", status="replayed", response_text="r" * 80, prompt_tokens=None, completion_tokens=None, **{**base, "job_id": "j2"})
    repo.record_call(db, model="mystery", status="ok", response_text="r" * 80, **base)
    rep = cost_report(db, topic_id=tid)
    by = {m["model"]: m for m in rep["models"]}
    assert by["cheap"]["paid_usd"] == pytest.approx(2.0)
    assert by["cheap"]["saved_tokens"] == (80 // 4) + (80 // 4)                # estimated from chars
    assert by["mystery"]["paid_usd"] is None and rep["unpriced_models"] == ["mystery"]
    assert by["mystery"]["estimated_calls"] == 1
    assert rep["paid_usd"] == pytest.approx(2.0) and rep["has_prices"]


def test_settings_validation_for_budget_prices_and_models():
    ok, err = validate_settings({"ai.max_job_tokens": "50000", "ai.max_job_cost_usd": "1.5", "ai.prices": PRICES,
                                 "ai.audit_model": "  cheap  ", "ai.pass2_model": "", "ai.storyboard_model": "dear"})
    assert not err and ok["ai.max_job_tokens"] == 50000 and ok["ai.max_job_cost_usd"] == 1.5 and ok["ai.audit_model"] == "cheap"
    assert json.loads(ok["ai.prices"]) == {"cheap": [0.5, 1.5], "dear": [5.0, 15.0]}
    assert validate_settings({"ai.prices": "garbage"})[1]
    assert validate_settings({"ai.prices": ""})[0]["ai.prices"] == "{}"
    assert validate_settings({"ai.max_job_tokens": -5})[0]["ai.max_job_tokens"] == 0


def test_new_settings_are_seeded_with_no_invented_prices(db):
    from nalarka.db.repos.settings import get_setting
    assert get_setting(db, "ai.prices", None) in ("{}", {})
    assert int(get_setting(db, "ai.max_job_tokens", -1)) == 0 and float(get_setting(db, "ai.max_job_cost_usd", -1)) == 0


def _call(db, job, topic, h, accepted=True, status="ok"):
    return repo.record_call(db, job_id=job, topic_id=topic, tag="t", model="m", base_url="u", request_hash=h, system_prompt="s",
                            user_prompt="u", temperature=0.4, max_tokens=None, status=status, response_text="ans-" + h, accepted=accepted)


def test_topic_job_ids_and_cross_job_replay_for_a_fresh_render(db):
    t1, t2 = create_topic(db, "A", max_duration=30), create_topic(db, "B", max_duration=30)
    _call(db, "old1", t1, "h1"); _call(db, "old2", t1, "h2"); _call(db, "other", t2, "h3")
    _call(db, "junk", t1, "h4", accepted=False)                               # not accepted: never offered
    ids = repo.topic_job_ids(db, t1, exclude="new")
    assert set(ids) == {"old1", "old2"} and "other" not in ids and "junk" not in ids
    assert repo.topic_job_ids(db, None) == []
    rec = DbLLMRecorder(db.execute("PRAGMA database_list").fetchone()[2], "new", t1, replay_job_ids=ids)
    assert rec.lookup("h1")["response_text"] == "ans-h1"                      # same prompt hash -> free
    assert rec.lookup("h3") is None and rec.lookup("h4") is None              # other topic / unaccepted -> paid again


def test_beat_state_summary_marks_missing_ok_and_audit_fixed(db):
    tid = create_topic(db, "T", max_duration=30)
    scenes = [{"beat_id": i + 1, "start": float(i * 5), "end": float((i + 1) * 5), "bg_color": "cream", "layout": "x",
               "transition": "cut", "summary": f"s{i}", "visual_blueprint": "b"} for i in range(3)]
    plan = {"total_dur": 15, "palette": {}, "scenes": scenes, "scene_sources": {"helpers": {}, "beats": {}}}
    sb = save_plan(db, tid, plan, coder_model="m")
    plan["scene_sources"]["beats"]["beat_01"] = "def beat_01(v):\n    v.circle(0, 0, 40, color='dark')\n"
    plan["scene_sources"]["beats"]["beat_02"] = "def beat_02(v):\n    v.circle(0, 0, 40, color='dark')\n"
    sync_code(db, sb, plan, reason="pass2")
    plan["scene_sources"]["beats"]["beat_02"] = "def beat_02(v):\n    v.circle(0, 0, 50, color='dark')\n"
    sync_code(db, sb, plan, reason="audit")
    beats = list_beats_for_storyboard(db, sb)
    counts = beat_state_summary(db, beats)
    assert [b["state"] for b in beats] == ["ok", "fixed", "missing"]
    assert counts["saved"] == 2 and counts["total"] == 3 and counts["missing"] == 1 and counts["fixed"] == 1
    assert beat_state_summary(db, []) == {"ok": 0, "fixed": 0, "missing": 0, "fallback": 0, "total": 0, "saved": 0}


def test_stage_models_reach_the_right_calls(monkeypatch):
    import nalarka.core.storyboard as sb
    used = {}
    monkeypatch.setattr(sb, "plan_storyboard", lambda b, k, model, *a, **kw: used.setdefault("pass1", model) and [
        {"beat_id": 1, "start": 0.0, "end": 5.0, "bg_color": "cream", "layout": "top_text_bottom_art", "transition": "cut", "summary": "s", "visual_blueprint": "b"}])
    monkeypatch.setattr(sb, "call_ai_python_code",
                        lambda b, k, model, s, u, log, **kw: used.setdefault("pass2", model) and "def beat_01(v):\n    v.circle(0, 0, 40, color='dark')\n")
    sb.generate_two_pass_storyboard_and_code("http://x/v1", "k", "coder-model", "topic", [], [], 5, {"palette": {}}, lambda m: None,
                                             batch_beats=1, parallel_calls=1, plan_model="planner-model")
    assert used == {"pass1": "planner-model", "pass2": "coder-model"}


def test_non_local_bind_requires_token_and_allowed_hosts():
    from nalarka.api.security import bind_problems
    for h in ("127.0.0.1", "localhost", "::1"):
        assert bind_problems(h, "") == []                                    # local use stays zero-config
    assert any("NALARKA_API_TOKEN must be set" in p for p in bind_problems("0.0.0.0", "", "studio.example.com"))
    assert any("too short" in p for p in bind_problems("0.0.0.0", "short", "studio.example.com"))
    assert any("NALARKA_ALLOWED_HOSTS" in p for p in bind_problems("192.168.1.5", "x" * 24, ""))
    assert bind_problems("0.0.0.0", "x" * 24, "studio.example.com") == []


def test_run_server_refuses_unsafe_bind(tmp_path):
    import os, subprocess, sys
    pytest.importorskip("uvicorn")      # run_server imports the real web stack
    pytest.importorskip("fastapi")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "NALARKA_HOST": "0.0.0.0", "NALARKA_API_TOKEN": "", "PYTHONPATH": root}
    r = subprocess.run([sys.executable, os.path.join(root, "run_server.py")], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "NALARKA_API_TOKEN must be set" in r.stderr


def test_smoke_script_reports_ok_failure_and_never_prints_the_key(monkeypatch, capsys, tmp_path):
    import importlib.util, os
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("smoke_llm", Path(__file__).parent.parent / "scripts" / "smoke_llm.py")
    smoke = importlib.util.module_from_spec(spec); spec.loader.exec_module(smoke)

    prov = {"key": "deepseek", "base_url": "https://api.deepseek.com", "api_key": "sk-SECRET-123", "script_model": "deepseek-flash", "coder_model": "x"}

    def good(base, key, model, system, user, log, tag, **kw):
        assert key == "sk-SECRET-123" and kw["max_tokens"] == 64 and kw["min_chars"] == 1     # tiny and cheap
        llm._TLS.usage = (12, 4)
        return '{"status":"ok"}', "stop"

    r = smoke.run_one(prov, chat=good)
    assert r["ok"] and r["tokens"] == (12, 4) and r["model"] == "deepseek-flash"

    def bad(*a, **k):
        raise RuntimeError("DeepSeek rejected the API key")
    r = smoke.run_one(prov, chat=bad)
    assert not r["ok"] and "rejected" in r["error"] and "SECRET" not in json.dumps(r)

    assert not smoke.run_one({**prov, "script_model": "", "coder_model": ""}, chat=good)["ok"]
    assert not smoke.run_one(prov, chat=lambda *a, **k: ("", "stop"))["ok"]

    # whole-script run against a fresh database: nothing has a key -> exit 2, nothing sent
    monkeypatch.setenv("NALARKA_DB_PATH", str(tmp_path / "s.db"))
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "NALARKA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert smoke.main([]) == 2
    out = capsys.readouterr()
    assert "skipped" in out.out and "SECRET" not in out.out + out.err
    assert smoke.main(["nope"]) == 2
