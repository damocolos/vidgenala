"""Configurable logging setup for Nalarka Studio."""
import logging
from typing import Optional
from .constants import LOG_FILE


def setup_logging(
    log_file: Optional[str] = None,
    level: int = logging.DEBUG,
) -> None:
    target_file = log_file or LOG_FILE
    handlers = []
    if target_file:
        from pathlib import Path
        Path(target_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(target_file, encoding="utf-8"))
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers if handlers else None,
        force=True,
    )

    # Suppress verbose HTTP client logs
    for noisy in ("openai", "httpx", "httpcore", "PIL", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
