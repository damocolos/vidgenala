"""FastAPI application entrypoint for Nalarka Web API."""
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .deps import get_dispatcher, set_dispatcher
from .routers import (
    config_router, topics_router, voiceovers_router,
    library_router, renders_router, jobs_router,
)
from ..db.connection import get_connection, run_migrations
from ..db.seed import seed_defaults
from ..jobs.dispatcher import JobDispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Ensure migrations and seeds are applied
    conn = get_connection()
    try:
        run_migrations(conn)
        seed_defaults(conn)
    finally:
        conn.close()

    # Start background job dispatcher
    dispatcher = JobDispatcher()
    set_dispatcher(dispatcher)
    dispatcher.start()

    yield

    # Shutdown: Stop dispatcher and clean up worker processes
    dispatcher.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nalarka Motion Graphics Studio API",
        version="8.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Healthcheck
    @app.get("/healthz")
    def health_check():
        ffmpeg_ok = shutil.which("ffmpeg") is not None
        ffprobe_ok = shutil.which("ffprobe") is not None
        db_ok = False
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            db_ok = cur.fetchone()[0] == 1
            conn.close()
        except Exception:
            pass

        healthy = ffmpeg_ok and ffprobe_ok and db_ok
        status_code = status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=status_code,
            content={
                "status": "ok" if healthy else "degraded",
                "database": db_ok,
                "ffmpeg": ffmpeg_ok,
                "ffprobe": ffprobe_ok,
                "version": "8.1.0",
            },
        )

    # Register API routers under /api
    app.include_router(config_router, prefix="/api")
    app.include_router(topics_router, prefix="/api")
    app.include_router(voiceovers_router, prefix="/api")
    app.include_router(library_router, prefix="/api")
    app.include_router(renders_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")

    return app


app = create_app()
