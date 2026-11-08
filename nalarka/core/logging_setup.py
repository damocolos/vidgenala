"""Configurable logging setup for Nalarka Studio."""
import logging
from typing import Optional
from .constants import LOG_FILE


def setup_logging(
    log_file: Optional[str] = LOG_FILE,
    level: int = logging.DEBUG
) -> None:
    handlers = []
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    
    # Configure root logger
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers if handlers else None
    )

    # Suppress verbose HTTP client logs
    for noisy in ("openai", "httpx", "httpcore", "PIL", "asyncio", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
