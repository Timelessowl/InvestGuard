"""Structured data from regional organisations: rows are pushed by an upload, nothing is fetched on schedule."""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..common import valid_ogrn
from ..focus import valid_inn
from ..geo import canonical_municipality
from . import register
from .base import EventItem, SourceAdapter, SourceDataError

COLUMNS = ('external_id', 'event_type', 'title', 'company_name', 'inn', 'ogrn', 'municipality', 'address', 'cadastral_number',
           'event_date', 'status', 'value', 'value_unit', 'url', 'latitude', 'longitude')
CADASTRAL_RE = re.compile(r'^[0-9]{2}:[0-9]{2}:[0-9]{6,7}:[0-9]+$')


def parse_day(value):
    for parse in (date.fromisoformat, lambda v: datetime.strptime(v, '%d.%m.%Y').date()):
        try:
            return parse(value)
        except ValueError:
            continue
    raise ValueError(value)


def event_from_row(row, event_types):
    """Validated EventItem from one import row; all problems of the row are reported together."""
    values = {key: str(row.get(key) or '').strip() for key in COLUMNS}
    unknown = sorted(set(row) - set(COLUMNS))
    errors = [f'незнакомые колонки: {", ".join(unknown)}'] if unknown else []
    fields = {'event_type': values['event_type'], 'title': values['title'][:500]}
    if values['event_type'] not in event_types:
        errors.append(f'неизвестный тип события «{values["event_type"]}»')
    if not values['title']:
        errors.append('нет названия события')
    if values['inn'] and not valid_inn(values['inn']):
        errors.append('ИНН: не сходится контрольная сумма')
    if values['ogrn'] and not valid_ogrn(values['ogrn']):
        errors.append('ОГРН: не сходится контрольная сумма')
    if values['municipality']:
        fields['municipality'] = canonical_municipality(values['municipality'])
        if not fields['municipality']:
            errors.append(f'муниципалитет «{values["municipality"]}» не найден в справочнике')
    if values['cadastral_number'] and not CADASTRAL_RE.match(values['cadastral_number']):
        errors.append('кадастровый номер не в формате 66:41:0000000:1')
    if values['url'] and not re.match(r'^https?://', values['url']):
        errors.append('ссылка должна начинаться с http:// или https://')
    if values['event_date']:
        try:
            fields['event_date'] = parse_day(values['event_date'])
        except ValueError:
            errors.append('дата не в формате ГГГГ-ММ-ДД или ДД.ММ.ГГГГ')
    if values['value']:
        try:
            fields['value'] = Decimal(values['value'].replace(' ', '').replace(',', '.'))
        except InvalidOperation:
            errors.append('показатель не является числом')
    for axis, limit in (('latitude', 90), ('longitude', 180)):
        if values[axis]:
            try:
                number = float(values[axis].replace(',', '.'))
                if not -limit <= number <= limit:
                    raise ValueError
                fields[axis] = number
            except ValueError:
                errors.append(f'{axis}: некорректная координата')
    if errors:
        raise SourceDataError('; '.join(errors))
    for key in ('external_id', 'company_name', 'inn', 'ogrn', 'address', 'cadastral_number', 'status', 'value_unit', 'url'):
        fields[key] = values[key] or None
    if 'latitude' in fields and 'longitude' in fields:
        fields['location_precision'] = 'exact'
    elif fields.get('municipality'):
        fields['location_precision'] = 'municipality'
    return EventItem(**fields, raw={key: values[key] for key in COLUMNS if values[key]})


@register('import')
class ImportAdapter(SourceAdapter):
    produces = 'events'
    scheduled = False

    def availability(self):
        return 'manual_only'

    def check(self):
        return True, 'Данные загружаются файлом от организации, сетевой проверки нет.'

    def fetch(self, cursor):
        return iter(())
