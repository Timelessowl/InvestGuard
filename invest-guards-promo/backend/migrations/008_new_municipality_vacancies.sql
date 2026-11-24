-- Active vacancies of the rule profiles in municipalities new for the employer, NULL while history is too short
ALTER TABLE vacancy_employer_snapshots ADD COLUMN new_municipality_vacancies INTEGER
