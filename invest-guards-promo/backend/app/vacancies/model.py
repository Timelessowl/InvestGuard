"""Normalised vacancy: employer key, title normalisation, fingerprint, municipality from an address."""
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from ..common import valid_ogrn
from ..focus import valid_inn
from ..geo import canonical_municipality
from ..sources.base import EventItem

Profile = Literal['production', 'engineering', 'production_management', 'executive', 'other']
MANAGEMENT = ('production_management', 'executive')
SALARY_STEP = 5000
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(\.[\w-]+)+')
PHONE_RE = re.compile(r'(\+7|\b8)([\s()-]*\d){10}\b')  # +7 or 8 and ten more digits with any separators
CITY_RE = re.compile(r'^(г|г\.|город)\s+(.+)$', re.I)
DISTRICT_RE = re.compile(r'\b(район|р-н|городской округ|муниципальный округ|го)\b', re.I)


def text_norm(value):
    """Lowercase, ё → е, punctuation to spaces."""
    value = (value or '').lower().replace('ё', 'е')
    return ' '.join(re.sub(r'[\W_]+', ' ', value).split())


def title_norm(title):
    """Comparable title: without brackets (gender marks, store addresses) and punctuation."""
    return text_norm(re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', title or ''))


def employer_key(source_id, inn=None, ogrn=None, source_employer_id=None):
    """inn:<ИНН>, ogrn:<ОГРН> or <source>:<employer id>. Names never form a key."""
    if inn and valid_inn(inn):
        return f'inn:{inn}'
    if ogrn and valid_ogrn(ogrn):
        return f'ogrn:{ogrn}'
    if source_employer_id:
        return f'{source_id}:{source_employer_id}'
    return None


def salary_bucket(value):
    return None if value is None else int(round(Decimal(value) / SALARY_STEP)) * SALARY_STEP


def fingerprint(key, title, municipality, salary_min=None, salary_max=None):
    """Same vacancy in two sources: employer, title, place and salary range rounded to 5 000."""
    parts = [key or '', title_norm(title), municipality or '', str(salary_bucket(salary_min)), str(salary_bucket(salary_max))]
    return hashlib.sha1('|'.join(parts).encode()).hexdigest()


def municipality_from_address(location):
    """Municipality from 'Свердловская область, [район,] г Город, улица…': the reference name, the city as written
    when it is not in the reference, None when only a village or nothing is given."""
    parts = [p.strip() for p in (location or '').split(',') if p.strip()]
    if parts and 'свердловск' in parts[0].lower():
        parts = parts[1:]
    for part in parts[:3]:
        city = CITY_RE.match(part)
        if city:
            name = city.group(2).strip()
            return canonical_municipality(name) or name
    district = next((p for p in parts[:2] if DISTRICT_RE.search(p)), None)
    return canonical_municipality(district) if district else None


def scrub_contacts(value, limit):
    """Free text may carry a contact person's phone or e-mail: they are not stored."""
    if not value:
        return None
    value = PHONE_RE.sub('[телефон]', EMAIL_RE.sub('[e-mail]', str(value)))
    return value[:limit]


class Vacancy(BaseModel):
    """One vacancy as every job source sees it."""
    model_config = ConfigDict(frozen=True)
    source_id: str
    external_id: str
    title: str
    employer_name: str
    inn: str | None = None
    ogrn: str | None = None
    kpp: str | None = None
    source_employer_id: str | None = None
    employer_url: str | None = None
    hr_agency: bool = False
    region_code: str | None = None
    municipality: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    published_at: date | None = None
    modified_at: datetime | None = None
    work_places: int | None = None
    salary_min: Decimal | None = None
    salary_max: Decimal | None = None
    currency: str | None = None
    employment: str | None = None
    schedule: str | None = None
    specialisation: str | None = None
    profession_code: str | None = None
    url: str | None = None
    syndicated: bool = False  # republished from another job site: first candidate for a cross-source duplicate

    @field_validator('inn')
    @classmethod
    def _inn(cls, value):
        # A broken checksum is not fixed or guessed: the INN is simply unknown.
        return value if value and valid_inn(value) else None

    @field_validator('ogrn')
    @classmethod
    def _ogrn(cls, value):
        return value if value and valid_ogrn(value) else None

    @field_validator('work_places')
    @classmethod
    def _places(cls, value):
        return value if value and value > 0 else None

    @property
    def employer_key(self):
        return employer_key(self.source_id, self.inn, self.ogrn, self.source_employer_id)

    @property
    def fingerprint(self):
        return fingerprint(self.employer_key, self.title, self.municipality, self.salary_min, self.salary_max)

    def details(self):
        """Extension record for vacancy_details and vacancy_employers (store.save_details)."""
        return {'source_id': self.source_id, 'employer_key': self.employer_key, 'employer_name': self.employer_name,
                'inn': self.inn, 'ogrn': self.ogrn, 'source_employer_id': self.source_employer_id, 'employer_url': self.employer_url,
                'hr_agency': self.hr_agency, 'title': self.title, 'title_norm': title_norm(self.title),
                'specialisation': self.specialisation, 'profession_code': self.profession_code, 'municipality': self.municipality,
                'work_places': self.work_places, 'salary_min': self.salary_min, 'salary_max': self.salary_max, 'currency': self.currency,
                'employment': self.employment, 'schedule': self.schedule, 'syndicated': self.syndicated,
                'published_at': self.published_at, 'modified_at': self.modified_at, 'fingerprint': self.fingerprint}

    def event(self, raw):
        metrics = [{'name': 'work_places', 'title': 'Рабочих мест', 'value': self.work_places, 'unit': 'чел.'}] if self.work_places else []
        for name, title, value in (('salary_min', 'Зарплата от', self.salary_min), ('salary_max', 'Зарплата до', self.salary_max)):
            if value:
                metrics.append({'name': name, 'title': title, 'value': float(value), 'unit': 'руб.'})
        coordinates = self.latitude is not None and self.longitude is not None
        return EventItem(event_type='vacancy_opened', title=self.title, external_id=self.external_id, company_name=self.employer_name,
                         inn=self.inn, ogrn=self.ogrn, source_company_id=self.source_employer_id, municipality=self.municipality,
                         address=self.address, latitude=self.latitude, longitude=self.longitude,
                         location_precision='address' if coordinates else ('municipality' if self.municipality else None),
                         event_date=self.published_at, value=Decimal(self.work_places) if self.work_places else None,
                         value_unit='раб. мест' if self.work_places else None, metrics=tuple(metrics), url=self.url, raw=raw,
                         details_kind='vacancy', details=self.details())
