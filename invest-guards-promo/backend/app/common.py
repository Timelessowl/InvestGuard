import re
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Yekaterinburg')
MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
LEGAL_FORMS = r'(ООО|ОАО|ЗАО|АО|ПАО|НАО|ПК|НПО|НПП|ГК|ИП|ФГУП|МУП|ГУП)'


def display_date(moment=None):
    now = (moment or datetime.now(TZ)).astimezone(TZ)
    return f'{now.day} {MONTHS[now.month - 1]}, {now:%H:%M}'


def schedule_label(minutes):
    if minutes % 60:
        return f'Каждые {minutes} минут'
    hours = minutes // 60
    if hours == 1:
        return 'Каждый час'
    return f'Каждые {hours} часа' if hours in (2, 3, 4) else f'Каждые {hours} часов'


def initials(company):
    return re.sub(r'[«»"\'\s-]', '', re.sub(rf'^{LEGAL_FORMS}\s*', '', company.strip()))[:2].upper() or '??'


def normalize_company(name):
    name = name.lower().replace('ё', 'е')
    name = re.sub(rf'\b{LEGAL_FORMS.lower()}\b', ' ', name)
    name = re.sub(r'[«»"\'“”„()]', ' ', name)
    name = re.sub(r'\b(компания|группа компаний|холдинг|завод|предприятие)\b', ' ', name)
    return re.sub(r'[\s-]+', ' ', name).strip()


def urgency_for(value):
    if value >= 85:
        return 'high', 'Критическая'
    if value >= 70:
        return 'high', 'Высокая'
    if value >= 55:
        return 'medium', 'Средняя'
    if value >= 40:
        return 'low', 'Плановая'
    return 'low', 'Низкая'


def overall_score(factors):
    weights = {'Релевантность': 0.35, 'Уверенность': 0.25, 'Региональный эффект': 0.2, 'Срочность': 0.2}
    return round(sum(factors.get(name, 0) * weight for name, weight in weights.items()))
