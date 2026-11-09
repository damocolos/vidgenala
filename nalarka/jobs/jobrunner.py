"""Subprocess execution entrypoint for background jobs."""
import os
import sys
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..datadir import data_root
from ..core.logging_setup import setup_logging

from ..db.connection import get_connection, run_migrations
from ..db.repos.jobs import (
    get_job, update_job_status, append_job_event,
)
from ..db.repos.settings import (
    get_provider, get_active_provider, get_setting,
    add_catalog_model, add_tts_voice,
)
from ..db.repos.topics import get_topic
from ..db.repos.scripts import (
    create_script, get_script, get_current_script,
)
from ..db.repos.voiceovers import (
    create_voiceover, get_voiceover, create_transcript,
)
from ..db.repos.library import (
    sync_library_folder, get_library_item,
)
from ..db.repos.storyboards import (
    get_current_storyboard, get_storyboard, update_beat_code,
)
from ..db.repos.renders import (
    create_render, update_render_status, create_verification,
)
from ..services.context import RunContext
from .events import JobEventEmitter
from ..services.plan_assembler import save_plan, load_plan
from ..services.pipeline import (
    execute_generate_script, execute_generate_voiceover,
    execute_full_render, execute_reroll_beat,
    execute_preview_beat, execute_verify_only,
)
from ..core.llm import (
    call_ai_json, fetch_openrouter_models,
)
from ..core.tts import (
    fetch_elevenlabs_voices,
)
from ..services.secrets import decrypt_secret
from ..services.paths import safe_output_name
from ..services.llm_ledger import DbLLMRecorder
from ..services.render_media import finalize_render_media, probe_video
from ..db.repos.llm_calls import job_chain
from ..db.repos.renders import (
    bind_render_to_job, update_render_media, end_unfinished_render_for_job,
    update_render_metrics, get_render,
)
from ..core.verify import VerificationFailed
from ..core.llm import set_llm_recorder
from ..core.llm_guard import ProviderGuard, ProviderCircuitOpen, set_llm_guard


def _save_verification(conn, rid, ver_res: dict, max_duration) -> int:
    return create_verification(
        conn, render_id=rid, passed=ver_res["passed"], total=ver_res["total"],
        duration_sec=ver_res.get("duration"), frames=ver_res.get("frames"),
        results=ver_res.get("checks"), contact_sheet_path=ver_res.get("contact_sheet"),
        max_duration_used=max_duration,
    )


def _store_video_facts(conn, rid, video, workdir, total_dur=None) -> None:
    """Poster, checksum and ffprobe facts (resolution/length) for a rendered MP4. Best effort."""
    try:
        poster, digest = finalize_render_media(Path(video), Path(workdir), total_dur)
        update_render_media(conn, rid, poster, digest)
        facts = probe_video(Path(video))
        update_render_metrics(conn, rid, facts.get("width"), facts.get("height"), facts.get("duration") or total_dur)
    except Exception:
        pass


def run_job(job_id: str, db_path: Optional[str] = None) -> int:
    """Executes a single job by id inside this process."""
    setup_logging()
    conn = get_connection(db_path)
    run_migrations(conn)

    job = get_job(conn, job_id)
    if not job:
        print(f"[JOB RUNNER ERROR] Job {job_id} not found.", file=sys.stderr)
        conn.close()
        return 1

    params = job.get("params", {})
    kind = job["kind"]
    topic_id = job.get("topic_id")

    # Set up dedicated workdir
    workdir = data_root() / "renders" / job_id
    workdir.mkdir(parents=True, exist_ok=True)

    # Mark running with pid
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    update_job_status(conn, job_id, status="running", pid=os.getpid(), started_at=now_str)

    emitter = JobEventEmitter(conn, job_id)
    ctx = RunContext(
        workdir=workdir,
        log_fn=emitter.log,
        progress_fn=emitter.set_progress,
    )

    # AI request ledger: every LLM call of this job is saved; a resumed job replays the calls the
    # previous run already got answers for (no second charge).
    resume_of = params.get("resume_of")
    chain = job_chain(conn, resume_of) if resume_of else []
    set_llm_recorder(DbLLMRecorder(db_path, job_id, topic_id, replay_job_ids=chain))
    # Stop point: too many failed AI calls, or AI work taking too long, ends the job (resumable).
    def _int_setting(key: str, default: int) -> int:
        try:
            return int(get_setting(conn, key, default))
        except (TypeError, ValueError):
            return default
    set_llm_guard(ProviderGuard(
        max_consecutive=_int_setting("ai.max_consecutive_failures", 3),
        max_total=_int_setting("ai.max_total_failures", 6),
        max_minutes=_int_setting("ai.max_minutes_per_run", 45),
    ))
    resume_workdir = (data_root() / "renders" / resume_of) if resume_of else None
    active_render = {"id": None}

    def _bind_render(render_id: int) -> None:
        active_render["id"] = render_id
        try:
            bind_render_to_job(conn, render_id, job_id)
        except Exception as e:
            ctx.log(f"[WARN] Could not link render #{render_id} to job: {e}")

    try:
        ctx.log(f"[JOB START] Running {kind} (job_id: {job_id}, pid: {os.getpid()})")
        if chain:
            ctx.log(f"[RESUME] Resuming job {resume_of}: AI responses it already received are replayed from the database (no cost).")
        result = None

        if kind == "test_ai":
            prov_key = params.get("provider")
            prov = get_provider(conn, prov_key) if prov_key else get_active_provider(conn)
            if not prov:
                raise ValueError("No AI provider configured.")
            ctx.log(f"[TEST AI] Testing connection to {prov['label']} ({prov['base_url']})...")
            ping_data = call_ai_json(
                base_url=prov["base_url"],
                api_key=prov["api_key"] or "",
                model=prov["script_model"],
                system_prompt="Return JSON {\"status\":\"ok\"}",
                user_prompt="ping",
                log_fn=ctx.log,
            )
            result = {"status": "ok", "provider": prov["key"], "response": ping_data}

        elif kind == "fetch_openrouter_models":
            prov = get_provider(conn, "openrouter")
            api_key = (prov.get("api_key") if prov else None) or params.get("api_key") or ""
            ctx.log("Fetching available models from OpenRouter...")
            fetched = fetch_openrouter_models(api_key, ctx.log)
            for m in fetched:
                m_id = m.get("id")
                if m_id:
                    add_catalog_model(conn, "openrouter", m_id, role="coder", source="fetched")
                    add_catalog_model(conn, "openrouter", m_id, role="script", source="fetched")
            result = {"count": len(fetched)}

        elif kind == "fetch_el_voices":
            el_enc = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
            el_key = decrypt_secret(el_enc.encode("latin1")) if el_enc else ""
            if not el_key:
                el_key = params.get("el_api_key") or ""
            ctx.log("Fetching voices from ElevenLabs...")
            voices = fetch_elevenlabs_voices(el_key, ctx.log)
            for v in voices:
                label = f"{v['name']} [{v.get('category', 'generated')}] ({v['voice_id']})"
                add_tts_voice(conn, v["voice_id"], v["name"], v.get("category"), label, source="fetched")
            result = {"count": len(voices)}

        elif kind == "generate_script":
            if not topic_id:
                raise ValueError("generate_script requires topic_id.")
            topic = get_topic(conn, topic_id)
            if not topic:
                raise ValueError(f"Topic {topic_id} not found.")

            prov = get_active_provider(conn)
            if not prov:
                raise ValueError("No active AI provider configured.")

            data = execute_generate_script(
                ctx=ctx,
                base_url=prov["base_url"],
                api_key=prov["api_key"] or "",
                model=prov["script_model"],
                topic=topic["title"],
                language=topic["language"],
                max_duration=topic["max_duration"],
            )
            s_row = create_script(
                conn,
                topic_id=topic_id,
                raw_text=data["tagged_text"],
                source="ai",
                model=prov["script_model"],
                max_duration=topic["max_duration"],
                is_current=True,
            )
            result = {
                "script_id": s_row["id"],
                "version": s_row["version"],
                "word_count": s_row["word_count"],
            }

        elif kind == "generate_tts":
            raw_text = params.get("script_text") or ""
            if not raw_text and topic_id:
                cur_sc = get_current_script(conn, topic_id)
                if cur_sc:
                    raw_text = cur_sc["raw_text"]
            if not raw_text:
                raise ValueError("No script text provided for TTS synthesis.")

            tts_provider = params.get("tts_provider") or get_setting(conn, "tts.provider", "ElevenLabs API v1")
            language = params.get("language") or "Indonesian"
            max_dur = params.get("max_duration") or get_setting(conn, "render.max_duration", 60)
            voice = params.get("voice") or get_setting(conn, "tts.voice", "Bagas")

            el_enc = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
            el_key = decrypt_secret(el_enc.encode("latin1")) if el_enc else ""

            out_audio = workdir / "Suara_Voiceover.mp3"
            vo_res = execute_generate_voiceover(
                ctx=ctx,
                raw_script_text=raw_text,
                out_vo_path=out_audio,
                provider=tts_provider,
                language=language,
                max_duration=max_dur,
                el_api_key=el_key or "",
                el_voice=voice,
                el_model=get_setting(conn, "tts.model_id", "eleven_multilingual_v2"),
                el_stability=get_setting(conn, "tts.stability", 0.50),
                el_similarity=get_setting(conn, "tts.similarity", 0.75),
                el_style=get_setting(conn, "tts.style", 0.15),
                el_speaker_boost=get_setting(conn, "tts.speaker_boost", True),
                el_ssml_breaks=get_setting(conn, "tts.ssml_breaks", False),
            )

            vid = None
            if topic_id:
                cur_sc = get_current_script(conn, topic_id)
                vid = create_voiceover(
                    conn,
                    topic_id=topic_id,
                    file_path=str(out_audio),
                    origin="generated",
                    script_id=cur_sc["id"] if cur_sc else None,
                    provider=tts_provider,
                    voice=voice,
                    duration_sec=vo_res["duration"],
                    total_dur_locked=vo_res["total_video_duration"],
                    script_fingerprint=vo_res["fingerprint"],
                )
            result = {
                "voiceover_id": vid,
                "file_path": str(out_audio),
                "duration": vo_res["duration"],
                "total_video_duration": vo_res["total_video_duration"],
            }

        elif kind == "full_render":
            if not topic_id:
                raise ValueError("full_render requires topic_id.")
            topic = get_topic(conn, topic_id)
            if not topic:
                raise ValueError(f"Topic {topic_id} not found.")

            cur_sc = get_current_script(conn, topic_id)
            if not cur_sc:
                raise ValueError(f"No current script found for topic {topic_id}.")

            vo_id = params.get("voiceover_id")
            vo_data = get_voiceover(conn, vo_id) if vo_id else None
            vo_path = vo_data["file_path"] if vo_data else params.get("vo_path")
            if not vo_path or not os.path.exists(vo_path):
                raise FileNotFoundError(f"Voiceover audio file not found: {vo_path}")

            prov = get_active_provider(conn)
            if not prov:
                raise ValueError("No active AI provider configured.")

            ref_path = params.get("ref_path")
            orientation = params.get("orientation") or get_setting(conn, "render.orientation", "Match Reference")
            whisper_size = params.get("whisper_size") or get_setting(conn, "render.whisper_size", "base")
            bgm_path = params.get("bgm_path")
            synth_bgm = params.get("synth_bgm", get_setting(conn, "render.synth_bgm", True))
            workers = params.get("workers", get_setting(conn, "render.workers", None))
            out_mp4 = workdir / safe_output_name(params.get("output_name") or get_setting(conn, "render.output_name", "output_motion_graphic.mp4"))

            # Create render record
            rid = create_render(
                conn,
                topic_id=topic_id,
                trigger="full",
                voiceover_id=vo_id,
                whisper_size=whisper_size,
                synth_bgm=synth_bgm,
                orientation=orientation,
                output_path=str(out_mp4),
            )
            update_render_status(conn, rid, status="running", started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _bind_render(rid)

            # Check if an existing storyboard can be reused
            reuse_sb = params.get("reuse_storyboard", True)
            specified_sb_id = params.get("storyboard_id")
            existing_sb = None
            if specified_sb_id:
                existing_sb = get_storyboard(conn, specified_sb_id)
            elif reuse_sb:
                existing_sb = get_current_storyboard(conn, topic_id)

            existing_plan = None
            existing_ns = None
            sb_id = None
            tr_id = None

            if existing_sb:
                try:
                    existing_plan, existing_ns = load_plan(conn, existing_sb["id"], ctx.log)
                    sb_id = existing_sb["id"]
                    ctx.log(
                        f"[STORYBOARD REUSE] Reusing storyboard #{existing_sb['id']} for Topic #{topic_id} "
                        f"({len(existing_plan.get('scenes', []))} beats). Zero LLM tokens required!"
                    )
                except Exception as e:
                    ctx.log(f"[STORYBOARD REUSE WARN] Could not load existing storyboard #{existing_sb['id']}: {e}. Will generate fresh.")
                    existing_plan = None
                    existing_ns = None
                    sb_id = None

            def _on_plan_ready(plan_dict: dict, ns_dict: dict, words_list: list):
                nonlocal tr_id, sb_id
                if vo_id and words_list and not tr_id:
                    try:
                        tr_id = create_transcript(
                            conn,
                            voiceover_id=vo_id,
                            whisper_size=whisper_size,
                            language=topic["language"],
                            words=words_list,
                        )
                    except Exception as err:
                        ctx.log(f"[WARN] Failed to create transcript: {err}")
                if not sb_id:
                    sb_id = save_plan(
                        conn,
                        topic_id=topic_id,
                        plan=plan_dict,
                        script_id=cur_sc["id"],
                        voiceover_id=vo_id,
                        transcript_id=tr_id,
                        coder_model=prov["coder_model"],
                        is_current=True,
                    )
                    ctx.log(f"[DB] Storyboard #{sb_id} saved to database immediately (safe from interruptions).")

            render_res = execute_full_render(
                ctx=ctx,
                base_url=prov["base_url"],
                api_key=prov["api_key"] or "",
                coder_model=prov["coder_model"],
                topic=topic["title"],
                script_raw_text=cur_sc["raw_text"],
                vo_path=vo_path,
                ref_path=ref_path,
                orientation=orientation,
                whisper_size=whisper_size,
                language=topic["language"],
                max_duration=topic["max_duration"],
                bgm_path=bgm_path,
                synth_bgm=synth_bgm,
                workers=workers,
                output_mp4_path=out_mp4,
                check_script_fingerprint=bool(vo_data and vo_data.get("script_fingerprint")),
                existing_plan=existing_plan,
                existing_namespace=existing_ns,
                on_plan_ready=_on_plan_ready,
                resume_workdir=resume_workdir,
            )

            # Persist transcript & storyboard if not already saved
            if vo_id and render_res.get("words") and not tr_id:
                tr_id = create_transcript(
                    conn,
                    voiceover_id=vo_id,
                    whisper_size=whisper_size,
                    language=topic["language"],
                    words=render_res["words"],
                )

            if not sb_id:
                sb_id = save_plan(
                    conn,
                    topic_id=topic_id,
                    plan=render_res["plan"],
                    script_id=cur_sc["id"],
                    voiceover_id=vo_id,
                    transcript_id=tr_id,
                    coder_model=prov["coder_model"],
                    is_current=True,
                )
            else:
                # Update any beats that were modified during audit loop
                save_plan(
                    conn,
                    topic_id=topic_id,
                    plan=render_res["plan"],
                    script_id=cur_sc["id"],
                    voiceover_id=vo_id,
                    transcript_id=tr_id,
                    coder_model=prov["coder_model"],
                    is_current=True,
                )

            # Link storyboard to render record
            if sb_id:
                cur = conn.cursor()
                cur.execute("UPDATE renders SET storyboard_id = ? WHERE id = ?", (sb_id, rid))
                conn.commit()

            # Persist verification
            ver_res = render_res["verification"]
            v_id = _save_verification(conn, rid, ver_res, topic["max_duration"])

            f_size = out_mp4.stat().st_size if out_mp4.exists() else 0
            update_render_status(
                conn, rid, status="succeeded",
                output_path=str(out_mp4),
                file_size=f_size,
                finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            _store_video_facts(conn, rid, out_mp4, workdir, render_res.get("total_dur"))

            result = {
                "render_id": rid,
                "storyboard_id": sb_id,
                "output_mp4": str(out_mp4),
                "passed": ver_res["passed"],
                "total": ver_res["total"],
            }

        elif kind == "reroll_beat":
            beat_id = params["beat_id"]
            custom_note = params.get("custom_note", "")

            # Look up beat and storyboard
            cur = conn.cursor()
            cur.execute("SELECT * FROM beats WHERE id = ?", (beat_id,))
            beat_row = cur.fetchone()
            if not beat_row:
                raise ValueError(f"Beat id {beat_id} not found.")

            sb_id = beat_row["storyboard_id"]
            plan, namespace = load_plan(conn, sb_id, log_fn=ctx.log)

            sb_data = get_storyboard(conn, sb_id)
            topic_id = sb_data["topic_id"]
            topic = get_topic(conn, topic_id)
            prov = get_active_provider(conn)

            vo_id = sb_data["voiceover_id"]
            vo_data = get_voiceover(conn, vo_id) if vo_id else None
            vo_path = vo_data["file_path"] if vo_data else params.get("vo_path")

            # Words from transcript
            tr_id = sb_data.get("transcript_id")
            cur.execute("SELECT words_json FROM transcripts WHERE id = ?", (tr_id,))
            tr_row = cur.fetchone()
            words = json.loads(tr_row["words_json"]) if tr_row else []

            out_mp4 = workdir / safe_output_name(params.get("output_name"), "output_motion_graphic_reroll.mp4")
            beat_idx = int(beat_row["beat_no"]) - 1

            rid = create_render(
                conn, topic_id=topic_id, trigger="reroll", storyboard_id=sb_id,
                voiceover_id=vo_id, output_path=str(out_mp4),
            )
            update_render_status(conn, rid, status="running", started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            _bind_render(rid)

            reroll_res = execute_reroll_beat(
                ctx=ctx,
                plan=plan,
                namespace=namespace,
                words=words,
                beat_idx=beat_idx,
                custom_note=custom_note,
                base_url=prov["base_url"],
                api_key=prov["api_key"] or "",
                coder_model=prov["coder_model"],
                topic=topic["title"],
                vo_path=vo_path,
                ref_path=params.get("ref_path"),
                orientation=params.get("orientation") or "portrait",
                bgm_path=params.get("bgm_path"),
                workers=params.get("workers"),
                output_mp4_path=out_mp4,
                max_duration=topic["max_duration"],
            )

            # Update beat code in SQLite
            if reroll_res.get("new_source"):
                update_beat_code(
                    conn,
                    beat_id=beat_id,
                    source_code=reroll_res["new_source"],
                    code_status="ok",
                    note=custom_note,
                    reason="reroll",
                )

            ver_res = reroll_res["verification"]
            _save_verification(conn, rid, ver_res, topic["max_duration"])

            f_size = out_mp4.stat().st_size if out_mp4.exists() else 0
            update_render_status(
                conn, rid, status="succeeded", output_path=str(out_mp4),
                file_size=f_size, finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            _store_video_facts(conn, rid, out_mp4, workdir)

            result = {
                "render_id": rid,
                "beat_idx": beat_idx,
                "output_mp4": str(out_mp4),
                "passed": ver_res["passed"],
            }

        elif kind == "preview_beat":
            beat_id = params["beat_id"]
            cur = conn.cursor()
            cur.execute("SELECT storyboard_id, beat_no FROM beats WHERE id = ?", (beat_id,))
            beat_row = cur.fetchone()
            if not beat_row:
                raise ValueError(f"Beat id {beat_id} not found.")

            sb_id = beat_row["storyboard_id"]
            plan, namespace = load_plan(conn, sb_id, log_fn=ctx.log)

            sb_data = get_storyboard(conn, sb_id)
            tr_id = sb_data.get("transcript_id")
            cur.execute("SELECT words_json FROM transcripts WHERE id = ?", (tr_id,))
            tr_row = cur.fetchone()
            words = json.loads(tr_row["words_json"]) if tr_row else []

            out_png = workdir / f"preview_beat_{beat_row['beat_no']:02d}.png"
            sheet_path = execute_preview_beat(
                ctx=ctx,
                plan=plan,
                namespace=namespace,
                words=words,
                beat_idx=int(beat_row["beat_no"]) - 1,
                orientation=params.get("orientation") or "portrait",
                ref_path=params.get("ref_path"),
                out_png_path=out_png,
            )
            result = {"sheet_path": sheet_path}

        elif kind == "verify_mp4":
            mp4_path = params["mp4_path"]
            facts = probe_video(Path(mp4_path))
            vr = (get_job(conn, job_id) or {}).get("render_id")
            try:
                res = execute_verify_only(
                    ctx=ctx,
                    mp4_path=mp4_path,
                    expected_w=params.get("expected_w") or facts.get("width") or 1080,
                    expected_h=params.get("expected_h") or facts.get("height") or 1920,
                    expected_dur=params.get("expected_dur") or (int(round(facts["duration"])) if facts.get("duration") else None),
                    max_duration=params.get("max_duration", 900),
                )
            except VerificationFailed as vf:
                if vr:
                    _save_verification(conn, vr, vf.result, params.get("max_duration", 900))
                raise
            result = res
            if vr:
                _save_verification(conn, vr, res, params.get("max_duration", 900))

        elif kind == "scan_library":
            folder = Path(params.get("folder") or get_setting(conn, "library.dir", "references_library"))
            res = sync_library_folder(conn, folder, ctx.log)
            result = res

        else:
            raise ValueError(f"Unsupported job kind: {kind}")

        # Finalize success
        emitter.flush()
        now_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_job_status(
            conn, job_id, status="succeeded", progress=100,
            status_text="Completed successfully", result=result,
            finished_at=now_end,
        )
        append_job_event(conn, job_id, "result", json.dumps(result or {}, ensure_ascii=False))
        ctx.log(f"[JOB COMPLETE] Finished {kind} successfully.")
        return 0

    except ProviderCircuitOpen as stop:
        err_msg = str(stop)
        ctx.log(f"[STOPPED] {err_msg}")
        emitter.flush()
        update_job_status(
            conn, job_id, status="failed", error=err_msg,
            status_text="Stopped: AI provider unstable (resumable)",
            finished_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        try:
            end_unfinished_render_for_job(conn, job_id, "failed", err_msg[:1000])
        except Exception:
            pass
        return 3

    except Exception as exc:
        err_msg = str(exc)
        stack = traceback.format_exc()
        ctx.log(f"[JOB ERROR] {err_msg}\n{stack}")
        try:
            logging.error(f"[JOB ERROR] {job_id}: {err_msg}\n{stack}")
        except Exception:
            pass
        emitter.flush()
        now_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        update_job_status(
            conn, job_id, status="failed", error=err_msg,
            finished_at=now_end,
        )
        try:
            end_unfinished_render_for_job(conn, job_id, "failed", err_msg[:1000])
            if isinstance(exc, VerificationFailed) and active_render["id"]:
                # The MP4 exists (the AI work was paid for): keep it, store the per-check results.
                rid_f = active_render["id"]
                row = get_render(conn, rid_f) or {}
                vid = Path(row.get("output_path") or "")
                if vid.is_file():
                    _save_verification(conn, rid_f, exc.result, (get_topic(conn, topic_id) or {}).get("max_duration"))
                    update_render_status(conn, rid_f, status="failed", error=err_msg[:1000],
                                         output_path=str(vid), file_size=vid.stat().st_size)
                    _store_video_facts(conn, rid_f, vid, vid.parent)
                    ctx.log("[KEEP] The MP4 was kept and registered in the library even though verification failed.")
        except Exception:
            pass
        return 1

    finally:
        set_llm_recorder(None)
        set_llm_guard(None)
        emitter.close()
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m nalarka.jobs.jobrunner <job_id> [db_path]")
        sys.exit(1)
    target_job_id = sys.argv[1]
    custom_db = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(run_job(target_job_id, custom_db))
