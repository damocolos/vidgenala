-- AI request ledger: every LLM call is stored so a failed job can resume without paying again.
CREATE TABLE IF NOT EXISTS llm_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id        TEXT,
  topic_id      INTEGER,
  tag           TEXT,
  model         TEXT,
  base_url      TEXT,
  request_hash  TEXT NOT NULL,
  system_prompt TEXT,
  user_prompt   TEXT,
  temperature   REAL,
  max_tokens    INTEGER,
  response_text TEXT,
  finish_reason TEXT,
  status        TEXT NOT NULL CHECK (status IN ('ok','error','replayed')),
  accepted      INTEGER NOT NULL DEFAULT 0,   -- 1 once the caller parsed/used the response successfully
  error         TEXT,
  req_chars     INTEGER,
  resp_chars    INTEGER,
  duration_sec  REAL,
  replay_of     INTEGER,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_llm_calls_hash ON llm_calls(request_hash, accepted);
CREATE INDEX IF NOT EXISTS ix_llm_calls_job ON llm_calls(job_id, id);
CREATE INDEX IF NOT EXISTS ix_llm_calls_topic ON llm_calls(topic_id, id);

ALTER TABLE renders ADD COLUMN poster_path TEXT;
ALTER TABLE renders ADD COLUMN sha256 TEXT;
ALTER TABLE renders ADD COLUMN job_id TEXT;
