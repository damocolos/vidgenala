"""Single source of truth for the application data directory (no package imports)."""
import os
from pathlib import Path


def data_root() -> Path:
    """Absolute data directory: ``$NALARKA_DATA_DIR`` or ``./data``."""
    return Path(os.environ.get("NALARKA_DATA_DIR", "data")).resolve()
