-- Start of the current full export: it may span several runs continued by cursor
ALTER TABLE source_sync_state ADD COLUMN snapshot_started_at TIMESTAMPTZ
