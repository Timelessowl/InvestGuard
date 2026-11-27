-- Structured events from external sources. Runner splits this file by semicolons: keep them out of strings.
CREATE TABLE event_types (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative', 'neutral')),
    link_policy TEXT NOT NULL CHECK (link_policy IN ('company', 'context', 'aggregate')),
    stage TEXT,
    description TEXT NOT NULL DEFAULT '',
    deprecated BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE source_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- no FK: sync_sources deletes sources removed from seed, their events stay
    source_id TEXT NOT NULL,
    external_id TEXT,
    dedup_key TEXT NOT NULL,
    event_type TEXT NOT NULL REFERENCES event_types(code),
    title TEXT NOT NULL,
    company_name TEXT,
    inn TEXT CHECK (inn ~ '^([0-9]{10}|[0-9]{12})$'),
    ogrn TEXT CHECK (ogrn ~ '^([0-9]{13}|[0-9]{15})$'),
    source_company_id TEXT,
    municipality TEXT,
    address TEXT,
    cadastral_number TEXT,
    latitude DOUBLE PRECISION CHECK (latitude BETWEEN -90 AND 90),
    longitude DOUBLE PRECISION CHECK (longitude BETWEEN -180 AND 180),
    location_precision TEXT CHECK (location_precision IN ('exact', 'address', 'municipality')),
    event_date DATE,
    status TEXT,
    value NUMERIC,
    value_unit TEXT,
    metrics JSONB NOT NULL DEFAULT '[]'::jsonb,
    url TEXT,
    raw JSONB NOT NULL,
    content_hash TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processing_status TEXT NOT NULL DEFAULT 'new' CHECK (processing_status IN
        ('new', 'linked', 'waiting', 'review', 'context', 'aggregated', 'ignored', 'error')),
    processing_reason TEXT,
    processed_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    duplicate_of BIGINT REFERENCES source_events(id) ON DELETE SET NULL,
    UNIQUE (source_id, dedup_key)
);
CREATE INDEX source_events_type_idx ON source_events (event_type, event_date DESC);
CREATE INDEX source_events_inn_idx ON source_events (inn) WHERE inn IS NOT NULL;
CREATE INDEX source_events_municipality_idx ON source_events (municipality);
CREATE INDEX source_events_processing_idx ON source_events (processing_status, collected_at);
CREATE INDEX source_events_source_idx ON source_events (source_id, collected_at DESC);
CREATE INDEX source_events_company_idx ON source_events (source_id, source_company_id);
CREATE TABLE source_event_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES source_events(id) ON DELETE CASCADE,
    replaced_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    content_hash TEXT NOT NULL,
    status TEXT,
    fields JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw JSONB NOT NULL
);
CREATE INDEX source_event_versions_event_idx ON source_event_versions (event_id, replaced_at DESC);
CREATE TABLE candidate_events (
    candidate_number BIGINT NOT NULL REFERENCES candidates(number) ON DELETE CASCADE,
    event_id BIGINT NOT NULL REFERENCES source_events(id) ON DELETE CASCADE,
    confidence TEXT NOT NULL CHECK (confidence IN ('confirmed', 'probable', 'manual_review')),
    linked_by TEXT NOT NULL CHECK (linked_by IN ('rule', 'analyst')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (candidate_number, event_id)
);
CREATE INDEX candidate_events_event_idx ON candidate_events (event_id);
CREATE TABLE source_sync_state (
    source_id TEXT PRIMARY KEY,
    sync_cursor JSONB,
    last_attempt_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    -- manual run requested from the API, picked up by the worker
    requested_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_error_kind TEXT,
    last_check_at TIMESTAMPTZ,
    last_check_ok BOOLEAN,
    last_check_message TEXT
);
CREATE TABLE source_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL,
    run_trigger TEXT NOT NULL CHECK (run_trigger IN ('schedule', 'manual', 'assistant')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN
        ('running', 'ok', 'partial', 'error', 'not_configured', 'skipped')),
    fetched INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    unchanged INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX source_runs_source_idx ON source_runs (source_id, started_at DESC);
ALTER TABLE candidates ADD COLUMN inn_key TEXT GENERATED ALWAYS AS (payload ->> 'inn') STORED;
CREATE INDEX candidates_inn_idx ON candidates (inn_key) WHERE inn_key IS NOT NULL
