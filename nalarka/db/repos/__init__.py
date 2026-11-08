"""Repositories for SQLite models."""

from .settings import (
    get_setting, set_setting, get_all_settings,
    get_provider, set_provider, list_providers,
    get_active_provider, get_models_catalog, add_catalog_model,
    list_tts_voices, add_tts_voice,
)
from .topics import (
    create_topic, get_topic, update_topic, archive_topic,
    delete_topic, list_topics,
)
from .scripts import (
    create_script, get_script, get_current_script,
    list_scripts_for_topic, set_current_script,
)
from .voiceovers import (
    create_voiceover, get_voiceover, list_voiceovers_for_topic,
    create_transcript, get_transcript, get_transcript_for_voiceover,
)
from .library import (
    upsert_library_item, get_library_item, get_library_item_by_kind_and_filename,
    list_library_items, delete_library_item, sync_library_folder,
)
from .storyboards import (
    create_storyboard, get_storyboard, get_current_storyboard,
    list_storyboards_for_topic, insert_beat, get_beat, get_beat_by_number,
    list_beats_for_storyboard, update_beat_code, insert_helper,
    list_helpers_for_storyboard, create_beat_revision, list_revisions_for_beat,
)
from .renders import (
    create_render, update_render_status, get_render,
    list_renders_for_topic, list_all_renders, create_verification,
    get_verification_for_render,
)
from .jobs import (
    create_job, get_job, update_job_status, list_active_jobs,
    list_jobs, append_job_event, batch_append_job_events, get_job_events,
)

__all__ = [
    "get_setting", "set_setting", "get_all_settings",
    "get_provider", "set_provider", "list_providers",
    "get_active_provider", "get_models_catalog", "add_catalog_model",
    "list_tts_voices", "add_tts_voice",
    "create_topic", "get_topic", "update_topic", "archive_topic",
    "delete_topic", "list_topics",
    "create_script", "get_script", "get_current_script",
    "list_scripts_for_topic", "set_current_script",
    "create_voiceover", "get_voiceover", "list_voiceovers_for_topic",
    "create_transcript", "get_transcript", "get_transcript_for_voiceover",
    "upsert_library_item", "get_library_item", "get_library_item_by_kind_and_filename",
    "list_library_items", "delete_library_item", "sync_library_folder",
    "create_storyboard", "get_storyboard", "get_current_storyboard",
    "list_storyboards_for_topic", "insert_beat", "get_beat", "get_beat_by_number",
    "list_beats_for_storyboard", "update_beat_code", "insert_helper",
    "list_helpers_for_storyboard", "create_beat_revision", "list_revisions_for_beat",
    "create_render", "update_render_status", "get_render",
    "list_renders_for_topic", "list_all_renders", "create_verification",
    "get_verification_for_render",
    "create_job", "get_job", "update_job_status", "list_active_jobs",
    "list_jobs", "append_job_event", "batch_append_job_events", "get_job_events",
]
