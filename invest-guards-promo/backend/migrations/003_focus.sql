CREATE TABLE focus_checks (
    inn TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    raw JSONB NOT NULL,
    result JSONB NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
