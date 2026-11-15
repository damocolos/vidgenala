-- Token usage per AI call. NULL = the provider did not report it (the UI then estimates chars/4 and says so).
ALTER TABLE llm_calls ADD COLUMN prompt_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN completion_tokens INTEGER;
