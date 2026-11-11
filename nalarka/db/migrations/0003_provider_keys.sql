-- Allow more AI providers than 'custom' and 'openrouter' (DeepSeek direct, and any later one) without a new
-- migration each time. SQLite cannot alter a CHECK, so the table is rebuilt with SQLite's documented procedure;
-- foreign keys are switched off meanwhile so model_catalog rows are not cascaded away.
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE ai_providers_new (
  key          TEXT PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 40 AND key NOT GLOB '*[^a-z0-9_-]*'),
  label        TEXT NOT NULL,
  base_url     TEXT NOT NULL,
  api_key_enc  BLOB,
  coder_model  TEXT NOT NULL,
  script_model TEXT NOT NULL,
  is_active    INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO ai_providers_new (key, label, base_url, api_key_enc, coder_model, script_model, is_active, updated_at)
  SELECT key, label, base_url, api_key_enc, coder_model, script_model, is_active, updated_at FROM ai_providers;
DROP TABLE ai_providers;
ALTER TABLE ai_providers_new RENAME TO ai_providers;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_active ON ai_providers(is_active) WHERE is_active = 1;
COMMIT;
PRAGMA foreign_keys = ON;
