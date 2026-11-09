"""Rendered videos are registered in the DB with facts (size, sha256, poster, resolution, length),
per-check verification results are stored, and a verification failure never loses the MP4."""
import shutil
import subprocess
from pathlib import Path

import pytest

from nalarka.core.verify import VerificationFailed, run_verification_suite
from nalarka.db.connection import get_connection, run_migrations
from nalarka.db.repos.jobs import create_job, get_job, get_job_events
from nalarka.db.repos.renders import (
    create_render, get_render, get_verification_for_render, update_render_metrics,
    verification_summaries, update_render_status,
)
from nalarka.db.repos.scripts import create_script
from nalarka.db.repos.topics import create_topic
from nalarka.db.repos.voiceovers import create_voiceover
from nalarka.db.seed import seed_defaults
from nalarka.services.render_media import probe_video, finalize_render_media

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "vid.db"
    c = get_connection(p); run_migrations(c); seed_defaults(c); c.close()
    return p


@pytest.fixture
def tiny_mp4(tmp_path):
    out = tmp_path / "tiny.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(out)], check=True)
    return out


def test_probe_and_media_facts(tiny_mp4, tmp_path):
    facts = probe_video(tiny_mp4)
    assert (facts["width"], facts["height"]) == (320, 240)
    assert 1.5 < facts["duration"] < 2.6
    poster, digest = finalize_render_media(tiny_mp4, tmp_path, 2)
    assert poster and Path(poster).is_file() and len(digest) == 64


def test_verification_failure_exposes_per_check_results(tiny_mp4, tmp_path):
    with pytest.raises(VerificationFailed) as ei:
        run_verification_suite(str(tiny_mp4), expected_dur=2, expected_w=320, expected_h=240,
                               log_fn=lambda m: None, max_duration=60, check_dir=tmp_path / "chk")
    res = ei.value.result
    assert isinstance(ei.value, AssertionError)          # old `except AssertionError` code keeps working
    assert res["total"] == 7 and len(res["checks"]) == 7
    assert all({"name", "passed", "detail"} <= set(c) for c in res["checks"])
    assert res["passed"] == sum(c["passed"] for c in res["checks"]) < 7


def test_verify_job_stores_checks_even_when_it_fails(db_path, tiny_mp4):
    from nalarka.jobs.jobrunner import run_job
    c = get_connection(db_path)
    tid = create_topic(c, title="T", language="Indonesian", max_duration=60, notes=None)
    rid = create_render(c, topic_id=tid, trigger="full", output_path=str(tiny_mp4))
    update_render_status(c, rid, status="succeeded", output_path=str(tiny_mp4))
    create_job(c, "v-1", "verify_mp4", "heavy", {"mp4_path": str(tiny_mp4)}, topic_id=tid, render_id=rid)
    c.close()
    assert run_job("v-1", str(db_path)) == 1             # black frames -> the job reports failure ...
    c = get_connection(db_path)
    v = get_verification_for_render(c, rid)
    assert v and len(v["results"]) == 7                  # ... but the 7 results are stored for the UI
    assert verification_summaries(c, [rid])[rid]["total"] == 7
    c.close()


def test_failed_verification_in_full_render_keeps_the_video(db_path, tiny_mp4, tmp_path, monkeypatch):
    import nalarka.jobs.jobrunner as jr
    c = get_connection(db_path)
    tid = create_topic(c, title="Keep me", language="Indonesian", max_duration=60, notes=None)
    create_script(c, tid, "[Hook] hai dunia ini naskah uji yang cukup panjang.", source="manual", max_duration=60, is_current=True)
    vo = tmp_path / "vo.mp3"; vo.write_bytes(b"x")
    vid = create_voiceover(c, topic_id=tid, file_path=str(vo), origin="uploaded")
    create_job(c, "f-1", "full_render", "heavy",
               {"voiceover_id": vid, "output_name": "../../escape.mp4"}, topic_id=tid)
    c.close()

    def fake_execute_full_render(**kw):
        out = Path(kw["output_mp4_path"])
        shutil.copy(tiny_mp4, out)                       # the video exists, then verification fails
        raise VerificationFailed("Verification Suite failed 1 check(s).", {
            "passed": 6, "total": 7, "duration": 2.0, "frames": 60, "contact_sheet": None,
            "checks": [{"name": f"{i}. check", "passed": i != 6, "detail": "d"} for i in range(1, 8)]})

    monkeypatch.setattr(jr, "execute_full_render", fake_execute_full_render)
    assert jr.run_job("f-1", str(db_path)) == 1
    c = get_connection(db_path)
    r = get_render(c, get_job(c, "f-1")["render_id"])
    assert r["status"] == "failed" and "Verification" in r["error"]
    assert Path(r["output_path"]).is_file() and r["output_path"].endswith("escape.mp4")   # name sanitised
    assert r["file_size"] > 0 and r["sha256"] and r["poster_path"]
    assert (r["width"], r["height"]) == (320, 240) and r["total_dur"] == 2
    assert len(get_verification_for_render(c, r["id"])["results"]) == 7
    logs = " ".join(e["message"] or "" for e in get_job_events(c, "f-1"))
    assert "[KEEP]" in logs
    c.close()


def test_update_render_metrics_keeps_existing_values(db_path):
    c = get_connection(db_path)
    tid = create_topic(c, title="T", language="Indonesian", max_duration=60, notes=None)
    rid = create_render(c, topic_id=tid, trigger="full", width=1080, height=1920)
    update_render_metrics(c, rid, None, None, 41.6)
    r = get_render(c, rid)
    assert (r["width"], r["height"], r["total_dur"]) == (1080, 1920, 42)
    c.close()


def test_enrich_renders_marks_missing_files(db_path, tiny_mp4):
    pytest.importorskip("fastapi")
    from nalarka.api.routers.renders import enrich_renders
    c = get_connection(db_path)
    tid = create_topic(c, title="Judul", language="Indonesian", max_duration=60, notes=None)
    a = create_render(c, topic_id=tid, trigger="full", output_path=str(tiny_mp4))
    b = create_render(c, topic_id=tid, trigger="full", output_path="/nope/missing.mp4")
    rows = {r["id"]: r for r in enrich_renders(c, [get_render(c, a), get_render(c, b)])}
    assert rows[a]["video_available"] is True and rows[b]["video_available"] is False
    assert rows[a]["topic_title"] == "Judul"
    c.close()
