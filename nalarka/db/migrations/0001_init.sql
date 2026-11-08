-- ===== 0001_init.sql: Initial Nalarka SQLite Schema =====

-- ===== CONFIG =====
CREATE TABLE IF NOT EXISTS app_settings (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  value_type  TEXT NOT NULL DEFAULT 'string'
              CHECK (value_type IN ('string','int','float','bool','json')),
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_providers (
  key          TEXT PRIMARY KEY CHECK (key IN ('custom','openrouter')),
  label        TEXT NOT NULL,
  base_url     TEXT NOT NULL,
  api_key_enc  BLOB,
  coder_model  TEXT NOT NULL,
  script_model TEXT NOT NULL,
  is_active    INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_active ON ai_providers(is_active) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS model_catalog (
  provider_key TEXT NOT NULL REFERENCES ai_providers(key) ON DELETE CASCADE,
  model_id     TEXT NOT NULL,
  role         TEXT NOT NULL CHECK (role IN ('coder','script')),
  is_free      INTEGER NOT NULL DEFAULT 0,
  source       TEXT NOT NULL CHECK (source IN ('preset','fetched')),
  fetched_at   TEXT,
  PRIMARY KEY (provider_key, model_id, role)
);

CREATE TABLE IF NOT EXISTS tts_voices (
  voice_id   TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  category   TEXT,
  label      TEXT NOT NULL,
  source     TEXT NOT NULL CHECK (source IN ('preset','fetched')),
  fetched_at TEXT
);

-- ===== TOPIK =====
CREATE TABLE IF NOT EXISTS topics (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  title        TEXT NOT NULL CHECK (length(trim(title)) > 0),
  language     TEXT NOT NULL DEFAULT 'Indonesian' CHECK (language IN ('Indonesian','English')),
  max_duration INTEGER NOT NULL DEFAULT 60 CHECK (max_duration BETWEEN 15 AND 900),
  notes        TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  archived_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_topics_updated ON topics(updated_at DESC) WHERE archived_at IS NULL;

-- ===== SCRIPT =====
CREATE TABLE IF NOT EXISTS scripts (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id         INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  version          INTEGER NOT NULL,
  source           TEXT NOT NULL CHECK (source IN ('ai','manual','edited')),
  model            TEXT,
  raw_text         TEXT NOT NULL,
  clean_text       TEXT NOT NULL,
  sections_json    TEXT NOT NULL,
  fingerprint      TEXT NOT NULL,
  word_count       INTEGER NOT NULL,
  est_voice_sec    REAL NOT NULL,
  est_video_sec    INTEGER NOT NULL,
  word_budget_lo   INTEGER,
  word_budget_hi   INTEGER,
  max_duration_at  INTEGER NOT NULL,
  is_current       INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (topic_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_scripts_current ON scripts(topic_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS voiceovers (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id           INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  script_id          INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
  origin             TEXT NOT NULL CHECK (origin IN ('generated','uploaded')),
  provider           TEXT,
  voice              TEXT,
  model_id           TEXT,
  params_json        TEXT,
  file_path          TEXT NOT NULL,
  duration_sec       REAL,
  total_dur_locked   INTEGER,
  script_fingerprint TEXT,
  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_voiceovers_topic ON voiceovers(topic_id, created_at DESC);

CREATE TABLE IF NOT EXISTS transcripts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  voiceover_id  INTEGER NOT NULL REFERENCES voiceovers(id) ON DELETE CASCADE,
  whisper_size  TEXT NOT NULL CHECK (whisper_size IN ('tiny','base','small')),
  language      TEXT,
  words_json    TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ===== LIBRARY =====
CREATE TABLE IF NOT EXISTS library_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  kind          TEXT NOT NULL CHECK (kind IN ('reference','bgm','voiceover_upload')),
  filename      TEXT NOT NULL,
  file_path     TEXT NOT NULL,
  media_type    TEXT NOT NULL CHECK (media_type IN ('video','image','audio')),
  size_bytes    INTEGER,
  sha256        TEXT,
  width         INTEGER,
  height        INTEGER,
  profile_json  TEXT,
  origin        TEXT NOT NULL DEFAULT 'upload' CHECK (origin IN ('upload','scan')),
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (kind, filename)
);

-- ===== VIDEO GENERATION =====
CREATE TABLE IF NOT EXISTS storyboards (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id          INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  script_id         INTEGER REFERENCES scripts(id) ON DELETE SET NULL,
  voiceover_id      INTEGER REFERENCES voiceovers(id) ON DELETE SET NULL,
  transcript_id     INTEGER REFERENCES transcripts(id) ON DELETE SET NULL,
  reference_item_id INTEGER REFERENCES library_items(id) ON DELETE SET NULL,
  coder_model       TEXT NOT NULL,
  total_dur         INTEGER NOT NULL,
  palette_json      TEXT NOT NULL,
  is_current        INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0,1)),
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_storyboards_current ON storyboards(topic_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS beats (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  storyboard_id    INTEGER NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
  beat_no          INTEGER NOT NULL,
  start_sec        REAL NOT NULL,
  end_sec          REAL NOT NULL,
  layout           TEXT,
  bg_color         TEXT,
  transition       TEXT,
  summary          TEXT,
  visual_blueprint TEXT,
  dot_index        INTEGER,
  lines_json       TEXT,
  extra_json       TEXT,
  source_code      TEXT,
  code_status      TEXT NOT NULL DEFAULT 'missing'
                   CHECK (code_status IN ('ok','rejected','missing','fallback')),
  audit_json       TEXT,
  UNIQUE (storyboard_id, beat_no)
);

CREATE TABLE IF NOT EXISTS storyboard_helpers (
  storyboard_id INTEGER NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  source        TEXT NOT NULL,
  PRIMARY KEY (storyboard_id, name)
);

CREATE TABLE IF NOT EXISTS beat_revisions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  beat_id     INTEGER NOT NULL REFERENCES beats(id) ON DELETE CASCADE,
  revision    INTEGER NOT NULL,
  reason      TEXT NOT NULL CHECK (reason IN ('pass2','audit','reroll')),
  note        TEXT,
  blueprint   TEXT,
  source_code TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (beat_id, revision)
);

CREATE TABLE IF NOT EXISTS renders (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  topic_id        INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  storyboard_id   INTEGER REFERENCES storyboards(id) ON DELETE SET NULL,
  voiceover_id    INTEGER REFERENCES voiceovers(id) ON DELETE SET NULL,
  bgm_item_id     INTEGER REFERENCES library_items(id) ON DELETE SET NULL,
  trigger         TEXT NOT NULL CHECK (trigger IN ('full','reroll')),
  status          TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','succeeded','failed','canceled')),
  width           INTEGER,
  height          INTEGER,
  fps             INTEGER NOT NULL DEFAULT 30,
  total_dur       INTEGER,
  workers         INTEGER,
  whisper_size    TEXT,
  synth_bgm       INTEGER,
  orientation     TEXT,
  output_path     TEXT,
  file_size       INTEGER,
  error           TEXT,
  started_at      TEXT,
  finished_at     TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS verifications (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  render_id          INTEGER NOT NULL REFERENCES renders(id) ON DELETE CASCADE,
  passed             INTEGER NOT NULL,
  total              INTEGER NOT NULL,
  duration_sec       REAL,
  frames             INTEGER,
  results_json       TEXT NOT NULL,
  contact_sheet_path TEXT,
  max_duration_used  INTEGER,
  created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ===== JOB & EVENT =====
CREATE TABLE IF NOT EXISTS jobs (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL CHECK (kind IN
               ('test_ai','fetch_openrouter_models','fetch_el_voices','generate_script',
                'generate_tts','full_render','reroll_beat','preview_beat','verify_mp4','scan_library')),
  weight       TEXT NOT NULL CHECK (weight IN ('light','heavy')),
  topic_id     INTEGER REFERENCES topics(id) ON DELETE SET NULL,
  render_id    INTEGER REFERENCES renders(id) ON DELETE SET NULL,
  status       TEXT NOT NULL DEFAULT 'queued'
               CHECK (status IN ('queued','running','succeeded','failed','canceled')),
  progress     INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
  status_text  TEXT,
  params_json  TEXT NOT NULL,
  result_json  TEXT,
  error        TEXT,
  pid          INTEGER,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  started_at   TEXT,
  finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS job_events (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  ts      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  kind    TEXT NOT NULL CHECK (kind IN ('log','progress','result')),
  message TEXT,
  pct     INTEGER
);
CREATE INDEX IF NOT EXISTS ix_job_events_job ON job_events(job_id, id);
