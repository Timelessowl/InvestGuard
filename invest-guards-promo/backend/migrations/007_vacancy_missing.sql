-- Start of the first complete full export that missed the vacancy: it is closed only if the next one misses it too
ALTER TABLE vacancy_details ADD COLUMN missing_since TIMESTAMPTZ
