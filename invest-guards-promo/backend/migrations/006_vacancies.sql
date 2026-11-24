-- Vacancies shared by job sources (trudvsem, later hh). Runner splits by semicolons: keep them out of strings and comments.
CREATE TABLE vacancy_employers (
    -- inn:<ИНН>, ogrn:<ОГРН> or <source>:<employer id in source>, never a name
    employer_key TEXT PRIMARY KEY,
    inn TEXT,
    ogrn TEXT,
    name TEXT NOT NULL,
    url TEXT,
    hr_agency BOOLEAN NOT NULL DEFAULT false,
    source_ids TEXT[] NOT NULL DEFAULT '{}',
    -- start of observation in InvestGuard, not of the employer
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    excluded_reason TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX vacancy_employers_inn_idx ON vacancy_employers (inn) WHERE inn IS NOT NULL;
-- One row per vacancy event, last_seen_at lives in source_events
CREATE TABLE vacancy_details (
    event_id BIGINT PRIMARY KEY REFERENCES source_events(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    employer_key TEXT NOT NULL REFERENCES vacancy_employers(employer_key),
    source_employer_id TEXT,
    title_norm TEXT NOT NULL,
    category TEXT,
    profile TEXT NOT NULL DEFAULT 'other' CHECK (profile IN
        ('production', 'engineering', 'production_management', 'executive', 'other')),
    profession_code TEXT,
    specialisation TEXT,
    municipality TEXT,
    work_places INTEGER CHECK (work_places > 0),
    salary_min NUMERIC,
    salary_max NUMERIC,
    currency TEXT,
    employment TEXT,
    schedule TEXT,
    syndicated BOOLEAN NOT NULL DEFAULT false,
    -- date of publication in the source
    published_at DATE,
    modified_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    fingerprint TEXT NOT NULL,
    duplicate_of BIGINT REFERENCES vacancy_details(event_id) ON DELETE SET NULL
);
CREATE INDEX vacancy_details_employer_idx ON vacancy_details (employer_key, published_at);
CREATE INDEX vacancy_details_active_idx ON vacancy_details (employer_key) WHERE closed_at IS NULL AND duplicate_of IS NULL;
CREATE INDEX vacancy_details_fingerprint_idx ON vacancy_details (fingerprint);
CREATE INDEX vacancy_details_updated_idx ON vacancy_details (updated_at);
CREATE INDEX vacancy_details_source_idx ON vacancy_details (source_id) WHERE closed_at IS NULL;
-- Daily per-employer counters: the series behind dynamics
CREATE TABLE vacancy_employer_snapshots (
    employer_key TEXT NOT NULL REFERENCES vacancy_employers(employer_key),
    snapshot_date DATE NOT NULL,
    active_count INTEGER NOT NULL,
    active_work_places INTEGER NOT NULL,
    new_30d INTEGER NOT NULL,
    new_30d_work_places INTEGER NOT NULL DEFAULT 0,
    new_90d INTEGER NOT NULL,
    production_count INTEGER NOT NULL,
    engineering_count INTEGER NOT NULL,
    management_count INTEGER NOT NULL,
    municipalities TEXT[] NOT NULL DEFAULT '{}',
    -- NULL: observation history is too short to tell
    new_municipalities TEXT[],
    categories JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (employer_key, snapshot_date)
);
CREATE INDEX vacancy_employer_snapshots_date_idx ON vacancy_employer_snapshots (snapshot_date)
