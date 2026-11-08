"""Routers package for Nalarka REST API."""

from .config import router as config_router
from .topics import router as topics_router
from .voiceovers import router as voiceovers_router
from .library import router as library_router
from .renders import router as renders_router
from .jobs import router as jobs_router

__all__ = [
    "config_router",
    "topics_router",
    "voiceovers_router",
    "library_router",
    "renders_router",
    "jobs_router",
]
