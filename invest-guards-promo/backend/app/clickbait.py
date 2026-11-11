import re

# Weighted headline patterns typical for clickbait; a headline scoring >= threshold is dropped before the LLM.
PATTERNS = [
    (3, 'интрига вместо факта', re.compile(
        r'\b(вы не поверите|никто не ожидал|все ахнули|шок|шокир\w*|сенсаци\w*|раскрыт\w* секрет|вот почему|вот что|вот как|'
        r'мало кто знает|никто не знает|об этом молчат|это нужно знать|что будет дальше)\b', re.I)),
    (2, 'обращение к читателю', re.compile(r'\b(узнайте|смотрите|представьте|угадайте|проверьте себя|запомните)\b', re.I)),
    (2, 'размытый объект', re.compile(r'\b(этот|эта|это|эти|такой|такая)\s+(простой|простая|странн\w+|уникальн\w+|удивительн\w+|необычн\w+|секретн\w+)\b', re.I)),
    (2, 'подборка-список', re.compile(r'^\s*(топ[- ]?\d+|\d+\s+(способ\w*|причин\w*|факт\w*|вещ\w*|ошиб\w*|секрет\w*|совет\w*))\b', re.I)),
    (2, 'призыв ко всем', re.compile(r'\b(должен|может|стоит|надо|нужно)\s+(увидеть|знать|попробовать|посетить)\s+каждый|каждый\s+(должен|может)\b', re.I)),
    (1, 'оценочные эпитеты', re.compile(r'\b(уникальн\w+|удивительн\w+|невероятн\w+|потрясающ\w+|красив\w+|грандиозн\w+)\b', re.I)),
    (1, 'вводная вместо новости', re.compile(r'^\s*(во-первых|оказывается|представьте|интересно|кстати|итак)\b', re.I)),
    (1, 'местоимения «вы»', re.compile(r'\b(вы|вас|вам|ваш\w*)\b', re.I)),
    (1, 'эмоциональная пунктуация', re.compile(r'(!{2,}|\?!|!\?|\.{3}\s*$|…\s*$)')),
    (1, 'эмодзи', re.compile('[\U0001F300-\U0001FAFF☀-➿]')),
]
CAPS_WORD = re.compile(r'\b[А-ЯЁ]{5,}\b')


def score(title):
    """Return (score, reasons) for a headline."""
    total, reasons = 0, []
    for weight, label, pattern in PATTERNS:
        if pattern.search(title or ''):
            total += weight
            reasons.append(label)
    if len(CAPS_WORD.findall(title or '')) >= 2:
        total += 1
        reasons.append('слова капслоком')
    return total, reasons


def verdict(title, strict=False):
    """Reason string if the headline looks like clickbait, else None. Strict mode is for UGC platforms like Dzen."""
    total, reasons = score(title)
    return f'Кликбейт: {", ".join(reasons)}' if total >= (2 if strict else 3) else None
