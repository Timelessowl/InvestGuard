CREATE TABLE documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    stage TEXT NOT NULL DEFAULT 'new',
    reason TEXT,
    extraction JSONB,
    embedding JSONB,
    candidate_number BIGINT REFERENCES candidates(number) ON DELETE SET NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    processed_at TIMESTAMPTZ
);
CREATE INDEX documents_stage_idx ON documents (stage, collected_at);
CREATE INDEX documents_source_idx ON documents (source_id, collected_at);
CREATE TABLE llm_usage (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd NUMERIC(12, 6) NOT NULL
);
CREATE INDEX llm_usage_created_idx ON llm_usage (created_at);
