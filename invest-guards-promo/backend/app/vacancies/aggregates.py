"""Per-employer hiring indicators on a given date, daily snapshots for dynamics. Duplicates are never counted."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from ..common import TZ
from ..database import VacancyEmployerRecord, VacancySnapshotRecord
from .model import MANAGEMENT

HORIZON_DAYS = 500  # oldest closed vacancy that still matters (municipality lookback + window)
NO_HISTORY = 'недостаточно истории наблюдения'


@dataclass(frozen=True)
class Row:
    event_id: int
    source_id: str
    title: str
    url: str | None
    municipality: str | None
    profile: str
    category: str | None
    specialisation: str | None
    work_places: int | None
    published: date | None
    first_seen: datetime
    closed_at: datetime | None
    source_employer_id: str | None

    @property
    def places(self):
        return self.work_places or 1

    @property
    def day(self):
        """Publication date, or the day InvestGuard first saw the vacancy when the source gives none."""
        return self.published or self.first_seen.astimezone(TZ).date()

    def brief(self):
        return {'eventId': self.event_id, 'title': self.title, 'url': self.url, 'municipality': self.municipality,
                'category': self.category, 'publishedAt': self.published.isoformat() if self.published else None,
                'workPlaces': self.work_places}


def day_end(day):
    return datetime.combine(day + timedelta(days=1), time(0), TZ)


def load_rows(db, as_of, keys=None):
    """Unique vacancies known by the end of as_of: open ones and those closed or published within the horizon."""
    since = as_of - timedelta(days=HORIZON_DAYS)
    query = ('SELECT d.employer_key, d.event_id, d.source_id, e.title, e.url, d.municipality, d.profile, d.category, d.specialisation, '
             'd.work_places, d.published_at, d.first_seen_at, d.closed_at, d.source_employer_id '
             'FROM vacancy_details d JOIN source_events e ON e.id = d.event_id '
             'WHERE d.duplicate_of IS NULL AND d.first_seen_at < :end '
             'AND (d.closed_at IS NULL OR d.closed_at >= :since OR d.published_at >= :since_day)')
    params = {'end': day_end(as_of), 'since': datetime.combine(since, time(0), TZ), 'since_day': since}
    if keys is not None:
        query += ' AND d.employer_key = ANY(:keys)'
        params['keys'] = list(keys)
    rows = {}
    for key, *values in db.execute(text(query + ' ORDER BY d.event_id'), params):
        rows.setdefault(key, []).append(Row(*values))
    return rows


def snapshots_near(db, keys, day, tolerance):
    """Latest snapshot per employer within [day - tolerance, day]."""
    statement = (select(VacancySnapshotRecord).where(VacancySnapshotRecord.snapshot_date.between(day - timedelta(days=tolerance), day))
                 .order_by(VacancySnapshotRecord.employer_key, VacancySnapshotRecord.snapshot_date.desc())
                 .distinct(VacancySnapshotRecord.employer_key))
    if keys is not None:
        statement = statement.where(VacancySnapshotRecord.employer_key.in_(list(keys)))
    return {s.employer_key: s for s in db.scalars(statement)}


class EmployerView:
    """Indicators of one employer as of a date. History gates follow the rules: no history, no growth figures."""

    def __init__(self, employer, rows, as_of, rules, book, previous=None):
        self.employer, self.rows, self.as_of, self.rules, self.book = employer, rows, as_of, rules, book
        self.previous = previous or {}  # {days: snapshot or None}
        end = day_end(as_of)
        self.active = [r for r in rows if r.closed_at is None or r.closed_at >= end]
        self.history_days = (as_of - employer.first_seen_at.astimezone(TZ).date()).days
        self.has_history = self.history_days >= rules.minHistoryDays

    @property
    def key(self):
        return self.employer.employer_key

    @property
    def source_employer_id(self):
        ids = Counter(r.source_employer_id for r in self.rows if r.source_employer_id)
        return ids.most_common(1)[0][0] if ids else None

    def published_in(self, days, rows=None):
        """Published in (as_of - days, as_of] by the source date: the first export does not make old vacancies new."""
        start = self.as_of - timedelta(days=days)
        return [r for r in (self.rows if rows is None else rows) if r.published and start < r.published <= self.as_of]

    def active_with(self, profiles):
        return [r for r in self.active if r.profile in profiles]

    def specialisation_share(self):
        excluded = set(self.book.excludeEmployers.specialisations)
        known = [r for r in self.rows if r.specialisation]
        return sum(r.specialisation in excluded for r in known) / len(known) if known else None

    def excluded_reason(self):
        return self.book.excluded_reason(self.employer.name, self.employer.hr_agency, self.specialisation_share())

    def dynamics(self, days):
        """Active vacancies now against the snapshot `days` ago; None with a reason when history is short."""
        if not self.has_history:
            return {'days': days, 'ratio': None, 'delta': None, 'reason': NO_HISTORY}
        base = self.previous.get(days)
        if base is None:
            return {'days': days, 'ratio': None, 'delta': None, 'reason': f'нет снимка за {days} дней до даты расчёта'}
        now = len(self.active)
        return {'days': days, 'previous': base.active_count, 'current': now, 'delta': now - base.active_count,
                'ratio': round(now / base.active_count, 3) if base.active_count else None, 'reason': None}

    def episode_start(self, municipality, profiles):
        rows = [r for r in self.active if r.municipality == municipality and r.profile in profiles]
        return min(r.day for r in rows) if rows else None

    def new_municipalities(self):
        """Places of active production, engineering or management vacancies where the employer had nothing before.
        None while history is short: absence before observation began cannot be asserted."""
        if not self.has_history:
            return None
        rules = self.rules.newMunicipality
        observed_from = self.employer.first_seen_at.astimezone(TZ).date()
        result = []
        for place in sorted({r.municipality for r in self.active_with(rules.profiles) if r.municipality}):
            start = self.episode_start(place, rules.profiles)
            if start < observed_from or start <= self.as_of - timedelta(days=rules.windowDays):
                continue
            earlier = [r for r in self.rows if r.municipality == place and start - timedelta(days=rules.lookbackDays) <= r.day < start]
            if not earlier:
                result.append(place)
        return result

    def categories(self):
        return dict(sorted(Counter(r.category for r in self.active if r.category).items()))

    def snapshot_values(self):
        new_30 = self.published_in(30)
        return {'active_count': len(self.active), 'active_work_places': sum(r.places for r in self.active),
                'new_30d': len(new_30), 'new_30d_work_places': sum(r.places for r in new_30), 'new_90d': len(self.published_in(90)),
                'production_count': len(self.active_with(('production',))), 'engineering_count': len(self.active_with(('engineering',))),
                'management_count': len(self.active_with(MANAGEMENT)),
                'municipalities': sorted({r.municipality for r in self.active if r.municipality}),
                'new_municipalities': self.new_municipalities(), 'categories': self.categories()}

    def summary(self):
        """Indicators for the signal raw and the API."""
        values = self.snapshot_values()
        return {'asOf': self.as_of.isoformat(), 'historyDays': self.history_days, 'hasHistory': self.has_history,
                'active': values['active_count'], 'activeWorkPlaces': values['active_work_places'], 'new30d': values['new_30d'],
                'new30dWorkPlaces': values['new_30d_work_places'], 'new90d': values['new_90d'],
                'production': values['production_count'], 'engineering': values['engineering_count'],
                'management': values['management_count'], 'municipalities': values['municipalities'],
                'newMunicipalities': values['new_municipalities'], 'categories': values['categories'],
                'dynamics30d': self.dynamics(30), 'dynamics90d': self.dynamics(90), 'excludedReason': self.employer.excluded_reason}


def build_views(db, as_of, rules, book, keys=None):
    rows = load_rows(db, as_of, keys)
    statement = select(VacancyEmployerRecord).order_by(VacancyEmployerRecord.employer_key)
    if keys is not None:
        statement = statement.where(VacancyEmployerRecord.employer_key.in_(list(keys)))
    employers = [e for e in db.scalars(statement) if e.employer_key in rows]
    known = [e.employer_key for e in employers]
    tolerance = rules.snapshotToleranceDays
    previous = {days: snapshots_near(db, known, as_of - timedelta(days=days), tolerance)
                for days in sorted({30, 90, rules.growth.lookbackDays, rules.massHiring.windowDays})}
    return [EmployerView(e, rows[e.employer_key], as_of, rules, book, {d: s.get(e.employer_key) for d, s in previous.items()})
            for e in employers]


def update_exclusions(views):
    """Staffing agencies and the public sector stay in the counters but get no signals."""
    for view in views:
        reason = view.excluded_reason()
        if reason != view.employer.excluded_reason:
            view.employer.excluded_reason = reason  # flushed with the session


def write_snapshots(db, views):
    """Daily snapshot; a later run on the same day overwrites it."""
    for view in views:
        values = view.snapshot_values()
        db.execute(insert(VacancySnapshotRecord).values(employer_key=view.key, snapshot_date=view.as_of, **values)
                   .on_conflict_do_update(index_elements=['employer_key', 'snapshot_date'], set_=values))
