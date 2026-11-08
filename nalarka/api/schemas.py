"""Pydantic schemas for the REST API."""
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


# Settings & Providers
class ProviderResponse(BaseModel):
    key: str
    label: str
    base_url: str
    coder_model: str
    script_model: str
    is_active: int
    has_key: bool
    masked_key: str
    updated_at: Optional[str] = None


class UpdateProviderRequest(BaseModel):
    label: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    coder_model: Optional[str] = None
    script_model: Optional[str] = None
    is_active: Optional[bool] = None


class TTSSettingsRequest(BaseModel):
    provider: Optional[str] = None
    voice: Optional[str] = None
    model_id: Optional[str] = None
    stability: Optional[float] = Field(None, ge=0.0, le=1.0)
    similarity: Optional[float] = Field(None, ge=0.0, le=1.0)
    style: Optional[float] = Field(None, ge=0.0, le=1.0)
    speaker_boost: Optional[bool] = None
    ssml_breaks: Optional[bool] = None
    api_key: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    settings: Dict[str, Any]


class DurationHintResponse(BaseModel):
    max_duration: int
    word_budget_lo: int
    word_budget_hi: int
    recommended_sections: int
    beats_lo: int
    beats_hi: int
    mode_label: str


# Topics
class CreateTopicRequest(BaseModel):
    title: str = Field(..., min_length=1)
    language: str = Field("Indonesian", pattern=r"^(Indonesian|English)$")
    max_duration: int = Field(60, ge=15, le=900)
    notes: Optional[str] = None


class UpdateTopicRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1)
    language: Optional[str] = Field(None, pattern=r"^(Indonesian|English)$")
    max_duration: Optional[int] = Field(None, ge=15, le=900)
    notes: Optional[str] = None


class TopicResponse(BaseModel):
    id: int
    title: str
    language: str
    max_duration: int
    notes: Optional[str] = None
    created_at: str
    updated_at: str
    archived_at: Optional[str] = None


# Scripts
class EstimateScriptRequest(BaseModel):
    raw_text: str
    max_duration: int = Field(60, ge=15, le=900)


class SaveManualScriptRequest(BaseModel):
    raw_text: str
    max_duration: Optional[int] = Field(60, ge=15, le=900)


class ScriptResponse(BaseModel):
    id: int
    topic_id: int
    version: int
    source: str
    model: Optional[str] = None
    raw_text: str
    clean_text: str
    sections: List[Dict[str, Any]]
    fingerprint: str
    word_count: int
    est_voice_sec: float
    est_video_sec: int
    word_budget_lo: Optional[int] = None
    word_budget_hi: Optional[int] = None
    max_duration_at: int
    is_current: int
    created_at: str


# Voiceover
class GenerateTTSRequest(BaseModel):
    script_text: Optional[str] = None
    provider: Optional[str] = None
    voice: Optional[str] = None
    language: Optional[str] = None
    max_duration: Optional[int] = None


# Renders
class CreateRenderRequest(BaseModel):
    voiceover_id: Optional[int] = None
    vo_path: Optional[str] = None
    ref_path: Optional[str] = None
    orientation: Optional[str] = "Match Reference"
    whisper_size: Optional[str] = "base"
    synth_bgm: Optional[bool] = True
    bgm_path: Optional[str] = None
    workers: Optional[int] = None
    output_name: Optional[str] = "output_motion_graphic.mp4"


class RerollBeatRequest(BaseModel):
    custom_note: Optional[str] = ""


class RenderResponse(BaseModel):
    id: int
    topic_id: int
    storyboard_id: Optional[int] = None
    voiceover_id: Optional[int] = None
    trigger: str
    status: str
    width: Optional[int] = None
    height: Optional[int] = None
    fps: int = 30
    total_dur: Optional[int] = None
    orientation: Optional[str] = None
    output_path: Optional[str] = None
    file_size: Optional[int] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


# Jobs
class JobResponse(BaseModel):
    id: str
    kind: str
    weight: str
    topic_id: Optional[int] = None
    render_id: Optional[int] = None
    status: str
    progress: int
    status_text: Optional[str] = None
    params: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: str = "queued"
    message: str = "Job queued successfully."
