import re

from .geo import REGION_RE

INVEST_RE = re.compile(r'завод|производств|цех[аеуо]?\b|площадк|инвест|строительств|построит|модерниз|мощност|запуск|'
                       r'расширени|импортозамещ|технопарк|\bОЭЗ\b|экономическ\w* зон|резидент|комбинат|оборудовани|'
                       r'рабочих мест|млрд|логистическ\w* (центр|комплекс)', re.I)


def prefilter(title, body, regional):
    content = f'{title} {body}'
    hits = {match.group(0).lower()[:6] for match in INVEST_RE.finditer(content)}
    if len(hits) < 2:
        return 'Нет признаков инвестпроекта'
    if not regional and not REGION_RE.search(content):
        return 'Нет упоминания Свердловской области'
    return None

