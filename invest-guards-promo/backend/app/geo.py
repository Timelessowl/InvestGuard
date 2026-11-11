import re

# Sverdlovsk oblast municipality centres (municipality-level precision).
MUNICIPALITIES = {
    'Екатеринбург': (56.8389, 60.6057), 'Нижний Тагил': (57.9101, 59.9813), 'Каменск-Уральский': (56.4149, 61.9189),
    'Первоуральск': (56.9080, 59.9429), 'Серов': (59.6033, 60.5787), 'Новоуральск': (57.2473, 60.0956),
    'Асбест': (57.0052, 61.4581), 'Верхняя Пышма': (56.9759, 60.5647), 'Ревда': (56.8001, 59.9087),
    'Березовский': (56.9095, 60.8180), 'Полевской': (56.4422, 60.1878), 'Краснотурьинск': (59.7636, 60.1934),
    'Верхняя Салда': (58.0465, 60.5560), 'Арамиль': (56.6944, 60.8342), 'Сысерть': (56.4993, 60.8193),
    'Богданович': (56.7764, 62.0464), 'Ирбит': (57.6705, 63.0710), 'Алапаевск': (57.8475, 61.6950),
    'Качканар': (58.7051, 59.4839), 'Красноуфимск': (56.6125, 57.7637), 'Кушва': (58.2826, 59.7646),
    'Невьянск': (57.4910, 60.2181), 'Реж': (57.3717, 61.3970), 'Сухой Лог': (56.9076, 62.0358),
    'Тавда': (58.0433, 65.2742), 'Камышлов': (56.8456, 62.7094), 'Среднеуральск': (56.9918, 60.4774),
    'Заречный': (56.8103, 61.3380), 'Лесной': (58.6350, 59.7980), 'Североуральск': (60.1533, 59.9525),
    'Карпинск': (59.7660, 59.9970), 'Ивдель': (60.6944, 60.4245), 'Талица': (57.0129, 63.7292),
    'Туринск': (58.0440, 63.6960), 'Артемовский': (57.3384, 61.8945), 'Дегтярск': (56.7046, 60.0790),
    'Кировград': (57.4300, 60.0670), 'Нижняя Тура': (58.6250, 59.8530), 'Верхотурье': (58.8622, 60.8103),
    'Михайловск': (56.4370, 59.1170), 'Нижние Серги': (56.6540, 59.2960), 'Белоярский': (56.7593, 61.3836),
}
ALIASES = {'титановая долина': 'Верхняя Салда', 'березовский': 'Березовский', 'артемовский': 'Артемовский'}


def _key(name):
    name = name.lower().replace('ё', 'е')
    name = re.sub(r'\b(городской округ|муниципальный округ|город|район|поселок|пгт|го)\b|\bг\.|\bпос\.', ' ', name)
    return re.sub(r'[\s-]+', ' ', name).strip()


def _stem_pattern(name):
    stems = [word[:-1] if len(word) <= 5 else word[:max(4, len(word) - 2)] for word in _key(name).split(' ')]
    return r'\b' + r'\w*[\s-]+'.join(re.escape(stem) for stem in stems)


_INDEX = {_key(name): name for name in MUNICIPALITIES} | {_key(k): v for k, v in ALIASES.items()}


def canonical_municipality(name):
    key = _key(name or '')
    if key in _INDEX:
        return _INDEX[key]
    found = next((canonical for k, canonical in sorted(_INDEX.items(), key=lambda item: -len(item[0])) if k in key), None)
    return found or next((name for name in MUNICIPALITIES if re.search(_stem_pattern(name), key)), None)


def lookup(name):
    canonical = canonical_municipality(name)
    if not canonical:
        return None
    latitude, longitude = MUNICIPALITIES[canonical]
    return {'latitude': latitude, 'longitude': longitude, 'precision': 'municipality'}


# Name stems for the rough "is the region mentioned" prefilter; homonyms like Реж/Лесной are left out.
AMBIGUOUS = {'Реж', 'Лесной', 'Заречный', 'Михайловск', 'Березовский', 'Белоярский', 'Асбест', 'Серов'}
REGION_RE = re.compile(r'свердловск|средн\w* урал|титанов\w* долин|'
                       + '|'.join(_stem_pattern(name) for name in MUNICIPALITIES if name not in AMBIGUOUS), re.I)
