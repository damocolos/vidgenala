"""Scene 3D: AI-written JavaScript scenes (one function per beat) that are checked in a sandbox before any render.

Phase 2 (this package so far): the `v` API (v3_core.js), the dry-run validator, JS extraction, prompts and the
batched generator with automatic repair. Rendering to frames comes in a later phase.
"""
from .runtime import Scene3DRuntime, Scene3DUnavailable
from .extract import extract_beat_functions

__all__ = ["Scene3DRuntime", "Scene3DUnavailable", "extract_beat_functions"]
