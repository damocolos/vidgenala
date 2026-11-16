"""Scene 3D phase 4: database, plan loading, API schema, audio parity with the flat type, and the whole 3D branch
(fake AI, three.js stand-in, real Chromium, real ffmpeg)."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from nalarka.core.render import build_audio_filter
from nalarka.core.scene3d.prompts import EXAMPLE_BEAT
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.repos.topics import create_topic
from nalarka.services.plan_assembler import load_plan, missing_beat_indices_3d, save_plan, sync_code

FAKE = (Path(__file__).parent / "fixtures" / "fake_three.js").read_text(encoding="utf-8")


@pytest.fixture()
def db(tmp_path):
    conn = get_connection(tmp_path / "t.db")
    run_migrations(conn)
    return conn


def _topic(db):
    return create_topic(db, "T")          # returns the new topic id


# ---------- database ----------
def test_video_type_columns_default_to_flat(db):
    for table in ("storyboards", "renders"):
        cols = {r["name"]: r for r in db.execute(f"PRAGMA table_info({table})")}
        assert cols["video_type"]["dflt_value"] == "'flat'" and cols["video_type"]["notnull"] == 1


def test_3d_plan_round_trip_without_compiling(db):
    tid = _topic(db)
    plan = {"total_dur": 6, "scenes": [{"beat_id": 1, "start": 0.0, "end": 2.0, "summary": "a", "visual_blueprint": "b"},
                                       {"beat_id": 2, "start": 2.0, "end": 6.0, "summary": "c", "visual_blueprint": "d"}],
            "scene_sources": {"helpers": {}, "beats": {"beat_01": EXAMPLE_BEAT}}}
    sb = save_plan(db, tid, plan, coder_model="m", video_type="3d")
    assert db.execute("SELECT video_type FROM storyboards WHERE id=?", (sb,)).fetchone()[0] == "3d"
    loaded, ns = load_plan(db, sb)
    assert ns == {} and loaded["video_type"] == "3d"                  # JavaScript is not compiled as Python
    assert loaded["scene_sources"]["beats"]["beat_01"] == EXAMPLE_BEAT
    assert missing_beat_indices_3d(loaded) == [1]
    loaded["scene_sources"]["beats"]["beat_02"] = EXAMPLE_BEAT.replace("beat_01", "beat_02")
    assert sync_code(db, sb, loaded, "generated") == 1
    assert missing_beat_indices_3d(load_plan(db, sb)[0]) == []


def test_flat_plan_still_loads_as_python(db):
    tid = _topic(db)
    plan = {"total_dur": 3, "scenes": [{"beat_id": 1, "start": 0.0, "end": 3.0, "summary": "a", "visual_blueprint": "b"}],
            "scene_sources": {"helpers": {}, "beats": {"beat_01": "def beat_01(v):\n    pass\n"}}}
    sb = save_plan(db, tid, plan, coder_model="m")
    loaded, ns = load_plan(db, sb)
    assert callable(ns["beat_01"]) and "video_type" not in loaded


# ---------- API schema ----------
def test_request_schema_video_type():
    pytest.importorskip("pydantic")
    from nalarka.api.schemas import CreateRenderRequest
    assert CreateRenderRequest().video_type is None
    assert CreateRenderRequest(video_type="3d").video_type == "3d"
    with pytest.raises(Exception):
        CreateRenderRequest(video_type="4d")


def test_setting_validation_accepts_only_known_types():
    from nalarka.services.settings_validation import validate_settings
    ok, err = validate_settings({"render.video_type": "3D"})
    assert ok == {"render.video_type": "3d"} and not err
    ok, err = validate_settings({"render.video_type": "xyz"})
    assert not ok and err


# ---------- audio is identical to the flat type ----------
def test_audio_filter_unchanged_by_the_refactor():
    assert build_audio_filter(40, False) == "[1:a]adelay=1000:all=1,apad=whole_dur=40,atrim=0:40,afade=t=out:st=39.5:d=0.5[aout]"
    assert build_audio_filter(40, True) == (
        "[1:a]adelay=1000:all=1,apad=whole_dur=40,atrim=0:40,asetpts=PTS-STARTPTS,asplit=2[vo1][vo2];"
        "[2:a]atrim=0:40,asetpts=PTS-STARTPTS,volume=0.32,afade=t=out:st=37.5:d=2.5[bg];"
        "[bg][vo1]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck];"
        "[duck][vo2]amix=inputs=2:duration=longest:dropout_transition=0,volume=2,alimiter=limit=0.95[aout]")


# ---------- the whole 3D branch ----------
@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")
def test_run_scene3d_end_to_end(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    from nalarka.services import pipeline3d as p3
    from nalarka.core.scene3d import generate as gen
    from nalarka.services.context import RunContext

    vo = tmp_path / "vo.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=330:duration=2.2", str(vo)], check=True)
    scenes = [{"beat_id": 1, "start": 0.0, "end": 2.0, "summary": "a", "visual_blueprint": "x"},
              {"beat_id": 2, "start": 2.0, "end": 3.5, "summary": "b", "visual_blueprint": "y"},
              {"beat_id": 3, "start": 3.5, "end": 5.0, "summary": "c", "visual_blueprint": "z"}]
    monkeypatch.setattr(p3, "ensure_three", lambda *a, **k: FAKE)
    monkeypatch.setattr(p3, "plan_storyboard_3d", lambda *a, **k: [dict(s) for s in scenes])

    calls = {"n": 0}

    def fake_ai(base, key, model, system, user, log, **kw):
        names = re.findall(r'"function_name": "(beat_\d+)"', user) or re.findall(r"Function `(beat_\d+)`", user)
        calls["n"] += 1
        return {n: EXAMPLE_BEAT.replace("beat_01", n) for n in names if not (n == "beat_02" and calls["n"] == 1)}   # beat_02 missing the 1st time

    monkeypatch.setattr(gen, "call_ai_js_code", fake_ai)
    saved, logs = [], []
    ctx = RunContext(workdir=tmp_path, log_fn=logs.append)
    kw = dict(base_url="u", api_key="k", coder_model="m", storyboard_model=None, topic="T", sections=[], words=[], total_dur=5,
              width=216, height=384, vo=vo, bgm_path=None, synth_bgm=False, ref_profile={}, max_duration=60,
              output_mp4=tmp_path / "out.mp4", script_raw_text="", captions=False, batch_beats=6, parallel_calls=1,
              on_plan_ready=lambda p, ns, w: saved.append("plan"), on_code_update=lambda p, r: saved.append(r))

    # first run: beat_02 never arrives -> a clear error, the other shots are kept in the plan
    plan_holder = {}
    def keep(p, ns, w):
        plan_holder["plan"] = p
    with pytest.raises(RuntimeError, match="beat_02"):
        p3.run_scene3d(ctx, existing_plan=None, **{**kw, "on_plan_ready": keep})
    plan = plan_holder["plan"]
    assert set(plan["scene_sources"]["beats"]) == {"beat_01", "beat_03"}

    # resume with the saved plan: only beat_02 is requested
    res = p3.run_scene3d(ctx, existing_plan=plan, **kw)
    assert res["frames"] == 150 and Path(res["output_mp4"]).exists() and res["verification"]["total"] >= 1
    probe = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries", "stream=codec_type,nb_read_frames,width,height",
                            "-of", "default=nw=1", res["output_mp4"]], capture_output=True, text=True).stdout
    assert "nb_read_frames=150" in probe and "width=216" in probe and "codec_type=audio" in probe
    assert Path(res["generated_scenes_path"]).read_text().count("function beat_") == 3
    assert any("Reusing 2/3" in l for l in logs)


def test_flat_pipeline_ignores_video_type_default():
    import inspect
    from nalarka.services.pipeline import execute_full_render
    assert inspect.signature(execute_full_render).parameters["video_type"].default == "flat"
