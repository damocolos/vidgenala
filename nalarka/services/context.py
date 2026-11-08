"""Execution context for parameterized job and render runs."""
import threading
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class RunContext:
    workdir: Path = field(default_factory=lambda: Path("."))
    log_fn: Callable[[str], None] = print
    progress_fn: Callable[[int, str], None] = lambda p, s: None
    cancel_event: Optional[threading.Event] = None

    def log(self, message: str) -> None:
        if self.log_fn:
            self.log_fn(message)

    def set_progress(self, pct: int, status: str = "") -> None:
        if self.progress_fn:
            self.progress_fn(pct, status)

    def is_cancelled(self) -> bool:
        return self.cancel_event.is_set() if self.cancel_event else False

    def check_cancelled(self) -> None:
        if self.is_cancelled():
            raise InterruptedError("Operation was cancelled by user.")
