"""Run script for Nalarka Motion Graphics Studio Web & API Server.

Configures uvicorn file watching to exclusively monitor application source code ('nalarka/'),
ensuring that file writes to 'data/renders/' never trigger server reloads or interrupt rendering jobs.
"""
import os
import sys

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("NALARKA_HOST", "127.0.0.1")
    port = int(os.environ.get("NALARKA_PORT", "8000"))
    reload = os.environ.get("NALARKA_RELOAD", "0").lower() in ("1", "true", "yes")

    print(f"Starting Nalarka Studio server on http://{host}:{port} (reload={reload})...")
    uvicorn.run(
        "nalarka.api.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=["nalarka"] if reload else None,
        reload_excludes=["data/*", "data/**", "*.log", "*.mp4", "*.wav", "*.mp3", "*.tmp"] if reload else None,
    )
