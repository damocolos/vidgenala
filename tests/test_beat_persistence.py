"""Per-beat persistence: every paid-for AI answer is saved at once; a re-render asks only for what is missing."""
import json
import re
from pathlib import Path

import pytest

import nalarka.core.storyboard as sbmod
from nalarka.core.llm_guard import ProviderCircuitOpen
from nalarka.core.scene_code import new_scene_namespace
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.seed import seed_defaults
from nalarka.db.repos.topics import create_topic
from nalarka.db.repos.storyboards import list_beats_for_storyboard, list_revisions_for_beat
from nalarka.jobs.jobrunner import _storyboard_matches
from nalarka.services.plan_assembler import (
    save_plan, load_plan, sync_code, count_saved_beats, missing_beat_indices,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    conn = get_connection(tmp_path / "beats.db")
    run_migrations(conn)
    seed_defaults(conn)
    yield conn
    conn.close()


def _scenes(n):
    return [
        {"beat_id": i + 1, "start": float(i * 5), "end": float((i + 1) * 5), "bg_color": "cream",
         "layout": "top_text_bottom_art", "transition": "cut", "summary": f"s{i + 1}", "visual_blueprint": f"blueprint {i + 1}"}
        for i in range(n)
    ]


def _code_for(prompt_user: str) -> str:
    """Fake model: answers with one valid beat function per `function_name` named in the prompt."""
    names = re.findall(r'"function_name": "(beat_\d+)"', prompt_user)
    return "\n\n".join(f"def {n}(v):\n    v.circle(0, 0, 40, color='dark')\n" for n in names)


class FakeAI:
    def __init__(self, fail_on=()):
        self.fail_on = set(fail_on)
        self.requested = []

    def __call__(self, base_url, api_key, model, system, user, log_fn, **kw):
        names = re.findall(r'"function_name": "(beat_\d+)"', user)
        self.requested.extend(names)
        if self.fail_on & set(names):
            raise ProviderCircuitOpen("provider is down")
        return _code_for(user)


def _run_pass2(monkeypatch, ai, plan, ns, only=None, on_update=None):
    monkeypatch.setattr(sbmod, "call_ai_python_code", ai)
    sbmod.generate_scene_code_batches(
        "http://x/v1", "k", "m", "topic", plan["scenes"], plan, ns, lambda m: None,
        batch_beats=1, parallel_calls=1, only_indices=only, on_code_update=on_update,
    )


def test_beat_spans_groups_contiguous_missing_indices():
    assert sbmod.beat_spans(5, 2) == [(0, 2), (2, 4), (4, 5)]
    assert sbmod.beat_spans([1, 2, 3, 7], 2) == [(1, 3), (3, 4), (7, 8)]
    assert sbmod.beat_spans([], 3) == []


def test_sync_code_updates_in_place_and_only_changed_rows(db):
    topic_id = create_topic(db, "T", max_duration=30)
    plan = {"total_dur": 15, "palette": {}, "scenes": _scenes(3), "scene_sources": {"helpers": {}, "beats": {}}}
    sb_id = save_plan(db, topic_id, plan, coder_model="m")
    assert count_saved_beats(db, sb_id) == (0, 3)

    plan["scene_sources"]["beats"]["beat_02"] = "def beat_02(v):\n    v.circle(0, 0, 40, color='dark')\n"
    assert sync_code(db, sb_id, plan, reason="generated") == 1
    assert sync_code(db, sb_id, plan, reason="generated") == 0      # nothing changed -> no write, no new revision
    assert count_saved_beats(db, sb_id) == (1, 3)

    n_sb = db.execute("SELECT COUNT(*) FROM storyboards WHERE topic_id = ?", (topic_id,)).fetchone()[0]
    assert n_sb == 1                                                # never a second storyboard
    beat2 = [b for b in list_beats_for_storyboard(db, sb_id) if b["beat_no"] == 2][0]
    assert beat2["code_status"] == "ok"
    assert len(list_revisions_for_beat(db, beat2["id"])) == 1


def test_sync_code_saves_helpers_in_order(db):
    topic_id = create_topic(db, "T", max_duration=30)
    plan = {"total_dur": 10, "palette": {}, "scenes": _scenes(2), "scene_sources": {"helpers": {}, "beats": {}}}
    sb_id = save_plan(db, topic_id, plan, coder_model="m")
    plan["scene_sources"]["helpers"] = {"_a": "def _a():\n    return 1\n", "_b": "def _b():\n    return _a()\n"}
    sync_code(db, sb_id, plan)
    _, ns = load_plan(db, sb_id)
    assert ns["_b"]() == 1


def test_failed_batch_keeps_finished_batches_and_next_run_asks_only_for_the_rest(db, monkeypatch):
    topic_id = create_topic(db, "T", max_duration=40)
    plan = {"total_dur": 20, "palette": {}, "scenes": _scenes(4), "scene_sources": {"helpers": {}, "beats": {}}}
    sb_id = save_plan(db, topic_id, plan, coder_model="m")         # the Pass 1 skeleton
    ns = new_scene_namespace()

    # Run 1: beat_03 hits a provider outage -> job aborts, but beats that finished are already stored.
    ai1 = FakeAI(fail_on={"beat_03"})
    with pytest.raises(ProviderCircuitOpen):
        _run_pass2(monkeypatch, ai1, plan, ns, on_update=lambda p, r: sync_code(db, sb_id, p, reason=r))
    done, total = count_saved_beats(db, sb_id)
    assert total == 4 and done >= 2                                # beat_01 and beat_02 were saved
    saved_after_run1 = {b["beat_no"] for b in list_beats_for_storyboard(db, sb_id) if b["source_code"]}
    assert {1, 2} <= saved_after_run1 and 3 not in saved_after_run1

    # Run 2 ("Render" again): reload from the database, request ONLY what is missing.
    plan2, ns2 = load_plan(db, sb_id)
    missing = missing_beat_indices(plan2, ns2)
    assert [i + 1 for i in missing] == sorted(set(range(1, 5)) - saved_after_run1)
    ai2 = FakeAI()
    _run_pass2(monkeypatch, ai2, plan2, ns2, only=missing, on_update=lambda p, r: sync_code(db, sb_id, p, reason=r))
    assert sorted(ai2.requested) == [f"beat_{i + 1:02d}" for i in missing]
    assert not set(ai2.requested) & {f"beat_{n:02d}" for n in saved_after_run1}   # saved beats are never re-requested
    assert count_saved_beats(db, sb_id) == (4, 4)
    assert missing_beat_indices(*load_plan(db, sb_id)) == []
    assert db.execute("SELECT COUNT(*) FROM storyboards WHERE topic_id = ?", (topic_id,)).fetchone()[0] == 1


def test_beat_that_fails_to_parse_stays_missing_not_lost(db, monkeypatch):
    topic_id = create_topic(db, "T", max_duration=30)
    plan = {"total_dur": 10, "palette": {}, "scenes": _scenes(2), "scene_sources": {"helpers": {}, "beats": {}}}
    sb_id = save_plan(db, topic_id, plan, coder_model="m")
    ns = new_scene_namespace()

    def flaky(base_url, api_key, model, system, user, log_fn, **kw):
        if "beat_02" in user and "beat_01" not in user:
            raise RuntimeError("524 gateway timeout")             # an ordinary error: only that batch is lost
        return _code_for(user)

    monkeypatch.setattr(sbmod, "call_ai_python_code", flaky)
    sbmod.generate_scene_code_batches(
        "http://x/v1", "k", "m", "t", plan["scenes"], plan, ns, lambda m: None, batch_beats=1, parallel_calls=1,
        on_code_update=lambda p, r: sync_code(db, sb_id, p, reason=r),
    )
    assert count_saved_beats(db, sb_id) == (1, 2)
    assert [i + 1 for i in missing_beat_indices(*load_plan(db, sb_id))] == [2]


def test_pass1_skeleton_is_saved_before_any_pass2_call(monkeypatch):
    seen = {}
    plan_calls = []

    monkeypatch.setattr(sbmod, "plan_storyboard", lambda *a, **k: _scenes(3))

    def boom(*a, **k):
        seen["skeleton_before_pass2"] = bool(seen.get("skeleton"))
        raise ProviderCircuitOpen("down")

    monkeypatch.setattr(sbmod, "call_ai_python_code", boom)
    with pytest.raises(ProviderCircuitOpen):
        sbmod.generate_two_pass_storyboard_and_code(
            "http://x/v1", "k", "m", "topic", [], [], 15, {"palette": {}}, lambda m: None,
            batch_beats=3, parallel_calls=1,
            on_scenes_ready=lambda p: seen.update(skeleton=len(p["scenes"])),
        )
    assert seen["skeleton"] == 3 and seen["skeleton_before_pass2"] is True


def test_storyboard_reuse_requires_same_script_voiceover_and_length():
    sb = {"script_id": 4, "voiceover_id": 9, "total_dur": 40}
    assert _storyboard_matches(sb, script_id=4, voiceover_id=9, total_dur=40)
    assert not _storyboard_matches(sb, script_id=5, voiceover_id=9, total_dur=40)
    assert not _storyboard_matches(sb, script_id=4, voiceover_id=10, total_dur=40)
    assert not _storyboard_matches(sb, script_id=4, voiceover_id=9, total_dur=41)
    assert _storyboard_matches({"script_id": None, "voiceover_id": None, "total_dur": 40}, script_id=4, voiceover_id=9, total_dur=40)
    assert _storyboard_matches(sb, script_id=4, voiceover_id=9, total_dur=None)   # unknown never blocks
