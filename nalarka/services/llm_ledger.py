"""DB-backed recorder plugged into core.llm: stores every AI call and replays accepted ones on resume."""
import threading
from typing import Iterable, Optional

from ..db.connection import get_connection
from ..db.repos import llm_calls as repo


class DbLLMRecorder:
    def __init__(self, db_path, job_id: str, topic_id: Optional[int], replay_job_ids: Iterable[str] = ()):
        self.db_path = db_path
        self.job_id = job_id
        self.topic_id = topic_id
        self.replay_job_ids = [j for j in replay_job_ids if j]
        self._lock = threading.Lock()  # pass-2 batches call the LLM from a thread pool

    @property
    def replay_enabled(self) -> bool:
        return bool(self.replay_job_ids)

    def _run(self, fn, *a, **kw):
        with self._lock:
            conn = get_connection(self.db_path)
            try:
                return fn(conn, *a, **kw)
            finally:
                conn.close()

    # --- API used by core.llm -------------------------------------------------------
    def lookup(self, request_hash: str) -> Optional[dict]:
        if not self.replay_enabled:
            return None
        return self._run(repo.find_replay, request_hash, self.replay_job_ids)

    def record(self, **kw) -> int:
        return self._run(repo.record_call, job_id=self.job_id, topic_id=self.topic_id, **kw)

    def accept(self, call_id: int) -> None:
        self._run(repo.mark_accepted, call_id)


def cost_report(conn, *, job_id: Optional[str] = None, topic_id: Optional[int] = None) -> dict:
    """Tokens and (when a price is set) USD, per model: what was paid for and what replays saved.

    Prices come only from the user's `ai.prices` setting; a model without a price shows tokens but no
    dollar figure. Tokens are the provider's own counts when it sent them, otherwise characters / 4."""
    from ..core.llm_guard import estimate_cost, parse_prices
    from ..db.repos.settings import get_setting
    prices = parse_prices(get_setting(conn, "ai.prices", "{}"))
    rows, paid_usd, saved_usd, unpriced = [], 0.0, 0.0, []
    for m in repo.usage_by_model(conn, job_id=job_id, topic_id=topic_id):
        paid = estimate_cost(prices, m["model"], m["paid_prompt"], m["paid_completion"])
        saved = estimate_cost(prices, m["model"], m["saved_prompt"], m["saved_completion"])
        if paid is None:
            unpriced.append(m["model"])
        else:
            paid_usd += paid
            saved_usd += saved or 0.0
        rows.append({**m, "paid_tokens": m["paid_prompt"] + m["paid_completion"],
                     "saved_tokens": m["saved_prompt"] + m["saved_completion"],
                     "paid_usd": paid, "saved_usd": saved})
    return {"models": rows, "paid_usd": round(paid_usd, 4), "saved_usd": round(saved_usd, 4),
            "unpriced_models": unpriced, "has_prices": bool(prices)}
