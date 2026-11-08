"""Web frontend routes rendering Jinja2 templates."""
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from starlette.templating import Jinja2Templates

from ..api.deps import get_db_conn
from ..db.repos.topics import get_topic, list_topics
from ..db.repos.scripts import get_current_script, list_scripts_for_topic
from ..db.repos.voiceovers import list_voiceovers_for_topic
from ..db.repos.library import list_library_items
from ..db.repos.renders import list_all_renders, list_renders_for_topic, get_verification_for_render
from ..db.repos.storyboards import get_current_storyboard, list_beats_for_storyboard
from ..db.repos.settings import (
    list_providers, get_provider, get_models_catalog,
    list_tts_voices, get_all_settings, get_setting,
)
from ..core.script import script_word_budget, script_section_count, estimate_video_seconds
from ..core.scripttext import parse_tagged_script
from ..core.render import default_render_workers
from ..services.secrets import decrypt_secret

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

web_router = APIRouter(include_in_schema=False)

DEFAULT_SAMPLE_SCRIPT = (
    "[Hook] Dengar rekaman suara sendiri, rasanya geli, malu, dan ingin langsung tekan skip. Padahal orang lain mendengarnya tiap hari. Kok bisa?\n\n"
    "[Inti 1] Saat kita bicara, suara sampai ke telinga dalam lewat dua jalan sekaligus: lewat udara di luar, dan lewat getaran tulang tenggorak di dalam kepala.\n\n"
    "[Inti 2] Tulang tengkorak memperkuat frekuensi rendah, sehingga di kepala kita sendiri, suara kita terdengar lebih dalam, berat, dan hangat.\n\n"
    "[Inti 3] Sebaliknya, mikrofon rekaman hanya menangkap suara yang lewat udara saja. Tanpa efek bass dari tulang, suara kita terdengar lebih tipis dan asing.\n\n"
    "[Penutup] Otak langsung terkejut karena versi tulang sudah jadi andalan seumur hidup. Jadi tenang, suaramu tidak aneh, hanya beda jalur dengar saja."
)


@web_router.get("/")
def index_redirect():
    return RedirectResponse(url="/topics")


@web_router.get("/topics", response_class=HTMLResponse)
def topics_dashboard(request: Request, conn: sqlite3.Connection = Depends(get_db_conn)):
    topics = list_topics(conn)
    library_items = list_library_items(conn)
    recent_renders = list_all_renders(conn, limit=10)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active_page": "topics",
            "topics": topics,
            "library_count": len(library_items),
            "recent_renders": recent_renders,
        },
    )


@web_router.get("/topics/{topic_id}", response_class=HTMLResponse)
def topic_studio(topic_id: int, request: Request, conn: sqlite3.Connection = Depends(get_db_conn)):
    topic = get_topic(conn, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")

    # Scripts
    current_script = get_current_script(conn, topic_id)
    raw_script = current_script["raw_text"] if current_script else DEFAULT_SAMPLE_SCRIPT
    clean_text, sections = parse_tagged_script(raw_script)
    words = len(clean_text.split())
    est_vo, est_vid = estimate_video_seconds(words)
    b_lo, b_hi = script_word_budget(topic["max_duration"])

    script_stats = {
        "word_count": words,
        "est_voice_seconds": est_vo,
        "est_video_seconds": est_vid,
        "word_budget_lo": b_lo,
        "word_budget_hi": b_hi,
        "over_cap": est_vid > topic["max_duration"],
    }

    # Duration hint
    beats_lo = max(1, int(round(topic["max_duration"] / 7.0)))
    beats_hi = max(beats_lo, int(round(topic["max_duration"] / 4.5)))
    duration_hint = {
        "word_budget_lo": b_lo,
        "word_budget_hi": b_hi,
        "recommended_sections": script_section_count(topic["max_duration"]),
        "beats_lo": beats_lo,
        "beats_hi": beats_hi,
        "mode_label": "Shorts / Reels" if topic["max_duration"] <= 60 else "Extended Video",
    }

    # Providers & Models
    providers = list_providers(conn)
    active_provider = next((p for p in providers if p["is_active"]), providers[0] if providers else None)
    models = get_models_catalog(conn, provider_key=active_provider["key"] if active_provider else None)

    # TTS Settings
    enc = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
    plain_tts_key = decrypt_secret(enc.encode("latin1")) if enc else ""
    tts_settings = {
        "provider": get_setting(conn, "tts.provider", "ElevenLabs API v1"),
        "voice": get_setting(conn, "tts.voice", "Bagas (Indonesia)"),
        "model_id": get_setting(conn, "tts.model_id", "eleven_multilingual_v2"),
        "stability": get_setting(conn, "tts.stability", 0.50),
        "similarity": get_setting(conn, "tts.similarity", 0.75),
        "style": get_setting(conn, "tts.style", 0.15),
        "speaker_boost": get_setting(conn, "tts.speaker_boost", True),
        "ssml_breaks": get_setting(conn, "tts.ssml_breaks", False),
        "has_api_key": bool(plain_tts_key),
    }
    tts_voices = list_tts_voices(conn)

    # Voiceovers
    vo_list = list_voiceovers_for_topic(conn, topic_id)
    current_vo = vo_list[0] if vo_list else None

    # Library items
    ref_items = list_library_items(conn, kind="reference")
    selected_ref = get_setting(conn, "selected_reference", "")

    # Storyboard & Beats
    sb = get_current_storyboard(conn, topic_id)
    beats = list_beats_for_storyboard(conn, sb["id"]) if sb else []

    # Latest Render
    renders = list_renders_for_topic(conn, topic_id)
    latest_render = renders[0] if renders else None

    return templates.TemplateResponse(
        request,
        "topic.html",
        {
            "active_page": "topics",
            "topic": topic,
            "current_script": current_script,
            "default_sample_script": DEFAULT_SAMPLE_SCRIPT,
            "script_stats": script_stats,
            "duration_hint": duration_hint,
            "providers": providers,
            "active_provider": active_provider,
            "models": models,
            "tts_settings": tts_settings,
            "tts_voices": tts_voices,
            "current_voiceover": current_vo,
            "ref_items": ref_items,
            "selected_ref": selected_ref,
            "beats": beats,
            "latest_render": latest_render,
            "default_workers": default_render_workers(),
        },
    )


@web_router.get("/library", response_class=HTMLResponse)
def library_view(request: Request, conn: sqlite3.Connection = Depends(get_db_conn)):
    lib_dir = get_setting(conn, "library.dir", "references_library")
    ref_items = list_library_items(conn, kind="reference")
    bgm_items = list_library_items(conn, kind="bgm")

    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "active_page": "library",
            "library_dir": lib_dir,
            "ref_items": ref_items,
            "bgm_items": bgm_items,
        },
    )


@web_router.get("/history", response_class=HTMLResponse)
def history_view(request: Request, conn: sqlite3.Connection = Depends(get_db_conn)):
    renders = list_all_renders(conn, limit=100)
    # Attach verification status
    for r in renders:
        v = get_verification_for_render(conn, r["id"])
        r["verification_passed"] = v["passed"] if v else None

    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "active_page": "history",
            "renders": renders,
        },
    )


@web_router.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request, conn: sqlite3.Connection = Depends(get_db_conn)):
    providers = list_providers(conn)
    enc = get_setting(conn, "tts.elevenlabs_api_key_enc", "")
    plain_tts_key = decrypt_secret(enc.encode("latin1")) if enc else ""
    tts_settings = {
        "provider": get_setting(conn, "tts.provider", "ElevenLabs API v1"),
        "voice": get_setting(conn, "tts.voice", "Bagas (Indonesia)"),
        "model_id": get_setting(conn, "tts.model_id", "eleven_multilingual_v2"),
        "stability": get_setting(conn, "tts.stability", 0.50),
        "similarity": get_setting(conn, "tts.similarity", 0.75),
        "style": get_setting(conn, "tts.style", 0.15),
        "speaker_boost": get_setting(conn, "tts.speaker_boost", True),
        "ssml_breaks": get_setting(conn, "tts.ssml_breaks", False),
        "has_api_key": bool(plain_tts_key),
    }

    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_page": "settings",
            "providers": providers,
            "tts": tts_settings,
        },
    )
