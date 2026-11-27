"""Hiring signals from employer indicators (seed/hiring_signals.json). Stored as events of the virtual source
hiring-signals, one per episode: dedup key type:employer:municipality:month the episode began."""
import json
import math
import os
import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..common import TZ
from ..database import EventRecord
from ..sources.base import EventItem
from . import SIGNAL_SOURCE
from .model import MANAGEMENT, Profile

ROOT = Path(__file__).resolve().parents[2]
TITLES = {'vacancy_mass_hiring': 'Массовый найм', 'vacancy_growth': 'Рост числа вакансий',
          'vacancy_new_municipality': 'Найм в новом муниципалитете', 'vacancy_production_launch': 'Набор под запуск производства'}
MATCHING = 'Работодатель без ИНН: нужно сопоставить с юрлицом'


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Tier(Strict):
    maxActive: int | None = Field(default=None, ge=0)
    minAbsolute: int = Field(ge=1)


class MassHiring(Strict):
    windowDays: int = Field(default=30, ge=7)
    measure: Literal['vacancies', 'work_places'] = 'vacancies'
    baselineMultiplier: float = Field(default=3.0, ge=1)
    baselineMonths: int = Field(default=6, ge=1)
    minAbsolute: int = Field(default=5, ge=1)
    noHistoryMinAbsolute: int = Field(default=15, ge=1)
    tiers: list[Tier] = Field(default_factory=list)


class Growth(Strict):
    lookbackDays: int = Field(default=90, ge=7)
    minRatio: float = Field(default=1.5, gt=1)
    minDelta: int = Field(default=5, ge=1)


class ProductionLaunch(Strict):
    windowDays: int = Field(default=90, ge=7)
    minDistinctCategories: int = Field(default=3, ge=2)
    requireManagement: bool = True


class NewMunicipality(Strict):
    profiles: list[Profile] = Field(default_factory=lambda: ['production', 'engineering', 'production_management', 'executive'])
    minVacancies: int = Field(default=2, ge=1)
    windowDays: int = Field(default=90, ge=7)
    lookbackDays: int = Field(default=365, ge=30)


class HiringRules(Strict):
    minHistoryDays: int = Field(default=60, ge=1)
    snapshotToleranceDays: int = Field(default=3, ge=0)
    maxBasisVacancies: int = Field(default=50, ge=1)
    massHiring: MassHiring = Field(default_factory=MassHiring)
    growth: Growth = Field(default_factory=Growth)
    productionLaunch: ProductionLaunch = Field(default_factory=ProductionLaunch)
    newMunicipality: NewMunicipality = Field(default_factory=NewMunicipality)


def rules_path():
    return Path(os.environ.get('HIRING_SIGNALS_PATH') or ROOT / 'seed/hiring_signals.json')


@lru_cache(maxsize=4)
def _load(path, mtime):
    return HiringRules.model_validate(json.loads(Path(path).read_text(encoding='utf-8')))


def load_rules():
    path = rules_path()
    return _load(str(path), path.stat().st_mtime)


@dataclass
class Signal:
    type: str
    municipality: str | None
    start: object  # date the condition first held in this evaluation
    value: float
    unit: str
    thresholds: dict
    basis: list = field(default_factory=list)
    note: str | None = None


def measure(rows, how):
    return sum(r.places for r in rows) if how == 'work_places' else len(rows)


def tier_floor(rules, active):
    tiers = rules.tiers
    tier = next((t for t in tiers if t.maxActive is None or active <= t.maxActive), None)
    return max(rules.minAbsolute, tier.minAbsolute if tier else 0)


def mass_hiring(view, rules):
    """New vacancies in the window against the employer's own monthly baseline, with a floor by employer size."""
    config = rules.massHiring
    window = view.published_in(config.windowDays)
    current = measure(window, config.measure)
    base = view.previous.get(config.windowDays)
    size = base.active_count if base is not None else len(view.active)
    floor = tier_floor(config, size)
    baseline, months = None, []
    if view.has_history:
        observed_from = view.employer.first_seen_at.astimezone(TZ).date()
        for k in range(1, config.baselineMonths + 1):
            end = view.as_of - timedelta(days=config.windowDays * k)
            start = end - timedelta(days=config.windowDays)
            if start < observed_from:
                break  # only months fully inside observation
            months.append(measure([r for r in view.rows if r.published and start < r.published <= end], config.measure))
        baseline = statistics.median(months) if months else None
    if baseline is not None:
        threshold = max(floor, math.ceil(config.baselineMultiplier * baseline))
        note = f'Относительно собственной базы: медиана {baseline:g} за {len(months)} мес.'
    else:
        threshold = max(floor, config.noHistoryMinAbsolute)
        note = f'Без базы сравнения: история наблюдения {view.history_days} дн., нужно {rules.minHistoryDays}'
    thresholds = {'measure': config.measure, 'windowDays': config.windowDays, 'threshold': threshold, 'floorBySize': floor,
                  'sizeActive': size, 'baseline': baseline, 'baselineMonths': months, 'multiplier': config.baselineMultiplier}
    if current < threshold:
        return None
    unit = 'раб. мест' if config.measure == 'work_places' else 'вакансий'
    return Signal('vacancy_mass_hiring', None, min(r.published for r in window), current, unit, thresholds, window, note)


def growth(view, rules):
    """Active vacancies against the snapshot lookbackDays ago; never computed without history."""
    config = rules.growth
    if not view.has_history:
        return None
    base = view.previous.get(config.lookbackDays)
    if base is None:
        return None
    now, before = len(view.active), base.active_count
    delta = now - before
    ratio = now / before if before else None
    if delta < config.minDelta or (ratio is not None and ratio < config.minRatio):
        return None
    thresholds = {'lookbackDays': config.lookbackDays, 'minRatio': config.minRatio, 'minDelta': config.minDelta,
                  'previous': before, 'previousDate': base.snapshot_date.isoformat(), 'current': now,
                  'ratio': round(ratio, 3) if ratio is not None else None, 'delta': delta}
    note = f'{before} → {now} активных вакансий' + ('' if ratio is not None else ': роста в разах нет, база была нулевой')
    recent = view.published_in(config.lookbackDays, view.active)
    return Signal('vacancy_growth', None, view.as_of, delta, 'вакансий', thresholds, recent, note)


def new_municipality(view, rules):
    config = rules.newMunicipality
    result = []
    for place in view.new_municipalities() or []:
        rows = [r for r in view.active_with(config.profiles) if r.municipality == place]
        if len(rows) < config.minVacancies:
            continue
        thresholds = {'minVacancies': config.minVacancies, 'profiles': config.profiles, 'lookbackDays': config.lookbackDays,
                      'windowDays': config.windowDays}
        result.append(Signal('vacancy_new_municipality', place, view.episode_start(place, config.profiles), len(rows), 'вакансий',
                             thresholds, rows, f'Раньше в {place} у работодателя вакансий не было'))
    return result


def production_launch(view, rules, book):
    """Technologists, engineers, line operators, setters and production heads hired together in one place."""
    config = rules.productionLaunch
    combo = set(book.launchCombo)
    window = [r for r in view.published_in(config.windowDays) if r.category in combo]
    result = []
    for place in sorted({r.municipality for r in window}, key=lambda p: p or ''):
        rows = [r for r in window if r.municipality == place]
        categories = sorted({r.category for r in rows})
        management = sorted({c for c in categories if book.by_code(c).profile in MANAGEMENT})
        if len(categories) < config.minDistinctCategories or (config.requireManagement and not management):
            continue
        thresholds = {'windowDays': config.windowDays, 'minDistinctCategories': config.minDistinctCategories,
                      'requireManagement': config.requireManagement, 'categories': categories, 'management': management}
        titles = ', '.join(book.by_code(c).title.lower() for c in categories)
        result.append(Signal('vacancy_production_launch', place, min(r.published for r in rows), len(categories), 'категорий',
                             thresholds, rows, f'За {config.windowDays} дней: {titles}'))
    return result


def evaluate(view, rules, book):
    """All signals that hold for the employer now; none for staffing agencies and the public sector."""
    if view.employer.excluded_reason:
        return []
    found = [mass_hiring(view, rules), growth(view, rules)]
    found += new_municipality(view, rules) + production_launch(view, rules, book)
    return [s for s in found if s]


def episode_id(kind, key, place, month):
    return f'{kind}:{key}:{place or "*"}:{month}'


def latest_signals(db, keys):
    """Latest stored signal per (type, employer, municipality)."""
    statement = select(EventRecord).where(EventRecord.source_id == SIGNAL_SOURCE).order_by(EventRecord.id)
    if keys is not None:
        statement = statement.where(EventRecord.raw['employerKey'].astext.in_(list(keys)))
    latest = {}
    for record in db.scalars(statement):
        latest[(record.event_type, record.raw.get('employerKey'), record.municipality)] = record
    return latest


def signal_item(view, signal, external_id, start, basis_limit):
    employer = view.employer
    place = f' — {signal.municipality}' if signal.municipality else ''
    raw = {'employerKey': view.key, 'employerName': employer.name, 'state': 'active', 'episodeStart': start.isoformat(),
           'value': signal.value, 'unit': signal.unit, 'note': signal.note, 'thresholds': signal.thresholds,
           'indicators': view.summary(), 'basis': [r.brief() for r in signal.basis[:basis_limit]], 'basisTotal': len(signal.basis),
           'sources': list(employer.source_ids or []), 'matching': None if employer.inn else MATCHING}
    metrics = [{'name': 'value', 'title': TITLES[signal.type], 'value': signal.value, 'unit': signal.unit},
               {'name': 'active', 'title': 'Актуальных вакансий', 'value': len(view.active), 'unit': 'вакансий'}]
    return EventItem(event_type=signal.type, title=f'{TITLES[signal.type]}: {employer.name}{place}', external_id=external_id,
                     company_name=employer.name, inn=employer.inn, ogrn=employer.ogrn, source_company_id=view.source_employer_id,
                     municipality=signal.municipality, location_precision='municipality' if signal.municipality else None,
                     event_date=start, status='active', value=Decimal(str(signal.value)), value_unit=signal.unit,
                     metrics=tuple(metrics), url=employer.url, raw=raw)


def ended_item(record, as_of, reason):
    return EventItem(event_type=record.event_type, title=record.title, external_id=record.external_id, company_name=record.company_name,
                     inn=record.inn, ogrn=record.ogrn, source_company_id=record.source_company_id, municipality=record.municipality,
                     location_precision=record.location_precision, event_date=record.event_date, status='ended', value=record.value,
                     value_unit=record.value_unit, metrics=tuple(record.metrics or ()), url=record.url,
                     raw={**record.raw, 'state': 'ended', 'endedOn': as_of.isoformat(), 'endReason': reason})


def collect(db, views, rules, book, as_of, keys=None):
    """EventItems for hiring-signals: current episodes continue under their key, finished ones are marked ended."""
    latest = latest_signals(db, keys)
    items, seen = [], set()
    for view in views:
        for signal in evaluate(view, rules, book):
            group = (signal.type, view.key, signal.municipality)
            seen.add(group)
            previous = latest.get(group)
            if previous is not None and previous.status == 'active':
                external_id, start = previous.external_id, previous.event_date
            else:
                start = signal.start or as_of
                external_id = episode_id(signal.type, view.key, signal.municipality, as_of.strftime('%Y-%m'))
            items.append(signal_item(view, signal, external_id, start, rules.maxBasisVacancies))
    evaluated = {view.key: view for view in views}
    for group, record in latest.items():
        view = evaluated.get(group[1])
        if group in seen or record.status != 'active' or (keys is not None and view is None):
            continue
        reason = view.employer.excluded_reason if view is not None and view.employer.excluded_reason else 'Условие сигнала больше не выполняется'
        items.append(ended_item(record, as_of, reason))
    return items
