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

from starlette.staticfiles import StaticFiles

from .security import LocalSecurityMiddleware
from .deps import get_dispatcher, set_dispatcher
from .routers import (
    config_router, topics_router, voiceovers_router,
    library_router, renders_router, jobs_router,
)
from ..web.routes import web_router
from ..db.connection import get_connection, run_migrations
from ..db.seed import seed_defaults
from ..jobs.dispatcher import JobDispatcher
from ..core.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize logging to nalarka_debug.log
    setup_logging()

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

    # The bundled UI is same-origin, so CORS is only needed for local dev servers.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Added last => outermost: Host/Origin/token checks run before anything else.
    app.add_middleware(LocalSecurityMiddleware)

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

    # Static files and Web UI
    static_dir = Path(__file__).resolve().parent.parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(web_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "nalarka.api.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["nalarka"],
        reload_excludes=["data/*", "data/**", "*.log", "*.mp4", "*.wav", "*.mp3", "*.tmp"],
    )
