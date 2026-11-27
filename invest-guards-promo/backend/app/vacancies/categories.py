"""Configurable profession categories and employer exclusions (seed/vacancy_categories.json)."""
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .model import Profile, text_norm

ROOT = Path(__file__).resolve().parents[2]


def compile_any(patterns, boundary=True):
    """One case-insensitive regex; keywords match from a word start."""
    if not patterns:
        return None
    prefix = r'(?<!\w)' if boundary else ''
    try:
        return re.compile(prefix + '(?:' + '|'.join(f'(?:{p})' for p in patterns) + ')', re.I)
    except re.error as error:
        raise ValueError(f'некорректное регулярное выражение: {error}') from error


class CategoryRule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: str = Field(pattern=r'^[a-z][a-z0-9_]{1,40}$')
    title: str
    profile: Literal['production', 'engineering', 'production_management', 'executive']
    keywords: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)
    _match = PrivateAttr()
    _exclude = PrivateAttr()

    def model_post_init(self, context):
        self._match = compile_any(self.keywords)
        self._exclude = compile_any(self.exclude, boundary=False)

    def matches(self, text):
        return bool(self._match.search(text)) and not (self._exclude and self._exclude.search(text))


class EmployerExclusion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    hrAgency: bool = True
    specialisations: list[str] = Field(default_factory=list)
    specialisationShare: float = Field(default=0.5, gt=0, le=1)
    namePatterns: list[str] = Field(default_factory=list)
    _names = PrivateAttr()

    def model_post_init(self, context):
        self._names = compile_any(self.namePatterns, boundary=False)


class CategoryBook(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int
    categories: list[CategoryRule] = Field(min_length=1)
    specialisationProfiles: dict[str, Profile] = Field(default_factory=dict)
    launchCombo: list[str] = Field(default_factory=list)
    excludeEmployers: EmployerExclusion = Field(default_factory=EmployerExclusion)

    @model_validator(mode='after')
    def _codes(self):
        codes = [c.code for c in self.categories]
        if len(set(codes)) != len(codes):
            raise ValueError('коды категорий повторяются')
        unknown = sorted(set(self.launchCombo) - set(codes))
        if unknown:
            raise ValueError(f'launchCombo ссылается на неизвестные категории: {", ".join(unknown)}')
        return self

    @field_validator('specialisationProfiles')
    @classmethod
    def _keys(cls, value):
        return {key.strip(): profile for key, profile in value.items()}

    def by_code(self, code):
        return next((c for c in self.categories if c.code == code), None)

    def classify(self, title, specialisation=None):
        """(category code or None, profile): the first matching category wins, then the source specialisation."""
        text = text_norm(title)
        for rule in self.categories:
            if rule.matches(text):
                return rule.code, rule.profile
        return None, self.specialisationProfiles.get((specialisation or '').strip(), 'other')

    def excluded_reason(self, name, hr_agency=False, specialisation_share=None):
        """Why the employer gets no hiring signals: staffing agency or public sector. None when it is in scope."""
        rules = self.excludeEmployers
        if rules.hrAgency and hr_agency:
            return 'Кадровое агентство: публикует вакансии других работодателей'
        if rules._names and rules._names.search(text_norm(name)):
            return 'Бюджетная или государственная организация (по названию)'
        if specialisation_share is not None and rules.specialisations and specialisation_share >= rules.specialisationShare:
            return 'Бюджетная сфера: большинство вакансий в образовании, здравоохранении или госслужбе'
        return None


def book_path():
    return Path(os.environ.get('VACANCY_CATEGORIES_PATH') or ROOT / 'seed/vacancy_categories.json')


@lru_cache(maxsize=4)
def _load(path, mtime):
    return CategoryBook.model_validate(json.loads(Path(path).read_text(encoding='utf-8')))


def load_book():
    """Validated dictionary; a broken file fails startup instead of being skipped silently."""
    path = book_path()
    return _load(str(path), path.stat().st_mtime)
