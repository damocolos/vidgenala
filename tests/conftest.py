"""Shared test config: Starlette's TestClient uses Host 'testserver'."""
import os

os.environ.setdefault("NALARKA_ALLOWED_HOSTS", "testserver")
