"""Stop point for AI providers: stop a job early when the provider keeps failing or is too slow.

Why: each failed or timed-out call can cost minutes (and money) and the beat loops would otherwise
keep going for an hour. When a limit trips, ProviderCircuitOpen unwinds the whole job; everything the
provider already answered is in the AI ledger, so the user can fix the provider and press Resume.

ProviderCircuitOpen derives from BaseException on purpose: the audit loops do ``except Exception``
around each beat and would otherwise swallow it and carry on calling the provider.
"""
import threading
import time
from typing import Optional


class ProviderCircuitOpen(BaseException):
    """Raised when the provider guard decides to stop the job."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def short_error(err: BaseException, limit: int = 180) -> str:
    code = getattr(err, "status_code", None)
    text = " ".join(str(err).split())
    if code is None:
        import re
        m = re.search(r"Error code:\s*(\d{3})", text)
        code = m.group(1) if m else None
    text = text[:limit] + ("..." if len(text) > limit else "")
    return f"HTTP {code}: {text}" if code and not text.startswith("HTTP") else text


HINT = ("Stopped to protect your budget and time. Everything the AI already returned is saved: "
        "wait a few minutes (or switch provider/model in Settings) and press Resume.")


class ProviderGuard:
    def __init__(self, max_consecutive: int = 3, max_total: int = 6, max_minutes: float = 45.0):
        self.max_consecutive = max(0, int(max_consecutive))   # 0 = off
        self.max_total = max(0, int(max_total))
        self.max_seconds = max(0.0, float(max_minutes)) * 60.0
        self._lock = threading.Lock()
        self.consecutive = 0
        self.total = 0
        self.ok_calls = 0
        self.first_call_at: Optional[float] = None
        self.tripped: Optional[str] = None
        self.last_error = ""

    def _trip(self, why: str) -> str:
        if self.tripped is None:
            self.tripped = f"{why} {HINT}"
        return self.tripped

    def before_call(self) -> None:
        with self._lock:
            if self.tripped:
                raise ProviderCircuitOpen(self.tripped)
            now = time.time()
            if self.first_call_at is None:
                self.first_call_at = now
            if self.max_seconds and now - self.first_call_at > self.max_seconds:
                raise ProviderCircuitOpen(self._trip(
                    f"AI work in this run has taken over {self.max_seconds / 60:.0f} minutes"
                    + (f" (last error: {self.last_error})." if self.last_error else ".")))

    def on_success(self) -> None:
        with self._lock:
            self.consecutive = 0
            self.ok_calls += 1

    def on_failure(self, err: BaseException) -> None:
        """Count a failed call. Raises ProviderCircuitOpen when a limit is reached."""
        with self._lock:
            self.consecutive += 1
            self.total += 1
            self.last_error = short_error(err)
            why = None
            if self.max_consecutive and self.consecutive >= self.max_consecutive:
                why = f"The AI provider failed {self.consecutive} calls in a row (last error: {self.last_error})."
            elif self.max_total and self.total >= self.max_total:
                why = f"The AI provider failed {self.total} calls in this run (last error: {self.last_error})."
            if why:
                raise ProviderCircuitOpen(self._trip(why)) from err


_GUARD: Optional[ProviderGuard] = None


def set_llm_guard(guard: Optional[ProviderGuard]) -> None:
    global _GUARD
    _GUARD = guard


def get_llm_guard() -> Optional[ProviderGuard]:
    return _GUARD
