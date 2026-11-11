"""Due-diligence flags for an investment project's initiator, built from Kontur.Focus responses.

Verdict: red if any red flag (stop factor), yellow if any yellow flag (needs a closer look), green otherwise.
Thresholds tied to revenue scale with company size; without reporting fixed rouble thresholds apply.
"""
from datetime import date, datetime, timezone

LEVELS = {'red': 0, 'yellow': 1, 'green': 2}
WEAK_RATINGS = {'CCC', 'CC', 'C', 'D'}
STRONG_RATINGS = {'AAA', 'AA', 'A', 'BBB'}


def money(value):
    value = float(value or 0)
    for size, unit in ((1e9, 'млрд'), (1e6, 'млн'), (1e3, 'тыс')):
        if abs(value) >= size:
            return f'{value / size:.1f}'.replace('.0', '').replace('.', ',') + f' {unit} ₽'
    return f'{value:.0f} ₽'


def marker(block, key):
    item = (block or {}).get(key)
    return item.get('data') if isinstance(item, dict) else None


def number(block, key):
    value = marker(block, key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def years_since(value):
    try:
        start = date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
    return (date.today() - start).days / 365.25


def evaluate(inn, raw):
    req = raw.get('req') or {}
    entity = req.get('UL') or req.get('IP') or {}
    legal = (raw.get('legalAnalytics') or {}).get('legalAnalyticsData') or {}
    bank = (raw.get('bankruptcyAnalytics') or {}).get('bankruptcyAnalyticsData') or {}
    court = (raw.get('courtAnalytics') or {}).get('courtAnalyticsData') or {}
    fin = (raw.get('financeAnalytics') or {}).get('financeAnalyticsData') or {}
    fssp = (raw.get('fsspAnalytics') or {}).get('fsspAnalyticsData') or {}
    link = (raw.get('linkAnalytics') or {}).get('linkAnalyticsData') or {}
    blocked = (raw.get('fnsBlockedBankAccounts') or {}).get('blockedAccountsInfo') or {}
    owners = raw.get('beneficialOwners') or {}
    scoring = (raw.get('scoring') or {}).get('scoringData') or []
    flags = []

    def flag(level, code, title, detail=''):
        flags.append({'level': level, 'code': code, 'title': title, 'detail': detail})

    # Status and registration.
    status = entity.get('status') or {}
    for key, level, title in (('dissolved', 'red', 'Компания недействующая'), ('dissolving', 'red', 'Компания в стадии ликвидации'),
                              ('bankrupting', 'red', 'Банкротство по данным ЕГРЮЛ'), ('reorganizing', 'yellow', 'Компания в процессе реорганизации')):
        if status.get(key):
            flag(level, f'status_{key}', title, status.get('statusString', ''))
    if marker(legal, 'documentsForLiquidation'):
        flag('red', 'liquidation_filed', 'За последний месяц подано заявление о ликвидации')
    age = years_since(entity.get('registrationDate'))
    if marker(legal, 'register3Month') or marker(legal, 'register6Month') or marker(legal, 'register12Month'):
        flag('yellow', 'young_company', 'Компания моложе года', f'зарегистрирована {entity.get("registrationDate", "")}')
    elif age is not None and age >= 5:
        flag('green', 'long_history', f'Работает с {str(entity.get("registrationDate"))[:4]} года')

    # Bankruptcy.
    stage = marker(bank, 'currentStage')
    if stage:
        flag('red', 'bankruptcy_stage', 'Идёт процедура банкротства', str(stage))
    if marker(bank, 'signEnd'):
        flag('red', 'bankruptcy_end', 'Деятельность прекращена в результате банкротства')
    if marker(bank, 'message12Month') or marker(bank, 'arbitrationCase3Month'):
        flag('red', 'bankruptcy_messages', 'Сообщения или дела о банкротстве за последний год')
    if number(bank, 'defendantInProgressCount') > 0:
        flag('red', 'bankruptcy_cases', 'Против компании идут дела о банкротстве',
             f'{int(number(bank, "defendantInProgressCount"))} дел на {money(number(bank, "defendantInProgressSum"))}')
    if marker(bank, 'intentionMonth'):
        flag('yellow', 'bankruptcy_intention', 'Кредиторы заявили о намерении подать на банкротство')
    if marker(bank, 'messageManager12Month') or marker(bank, 'messageFounder12Month'):
        flag('yellow', 'person_bankruptcy', 'Банкротство руководителя или учредителя за последний год')

    # Registry reliability, tax authority and account blocks.
    unreliable = [name for key, name in (('unreliableAddress', 'адрес'), ('unreliableManager', 'руководитель'),
                                         ('unreliableFounder', 'учредители'), ('unreliableGroup', 'управляющая компания')) if marker(legal, key)]
    if unreliable:
        flag('red', 'unreliable_egrul', 'Недостоверные сведения в ЕГРЮЛ', ', '.join(unreliable))
    if marker(legal, 'fnsDisqualified'):
        flag('red', 'disqualified', 'Руководитель в реестре дисквалифицированных лиц')
    blocks = int(blocked.get('totalCount') or 0)
    if blocks or marker(legal, 'bankAccountFnsBlock'):
        flag('red', 'blocked_accounts', 'ФНС приостановила операции по счетам', f'решений: {blocks}' if blocks else '')
    if marker(legal, 'highRiskOfBlockingOperations'):
        flag('red', 'kyc_high_risk', 'Высокий риск по платформе «Знай своего клиента»')
    if marker(legal, 'fnsTaxReportMissing'):
        flag('red', 'no_tax_reports', 'Не сдаёт налоговую отчётность больше года')
    if marker(legal, 'fnsHasTaxDebt'):
        flag('yellow', 'tax_debt', 'Есть задолженность по налогам больше 1000 ₽')
    if marker(legal, 'additionalCheckNeeded'):
        flag('yellow', 'focus_extra_check', 'Фокус рекомендует дополнительную проверку')
    changes = [name for key, name in (('documentsForManagementChange', 'руководителя'), ('documentsForOwnerChange', 'владельцев'),
                                      ('documentsForAddressChange', 'адреса')) if marker(legal, key)]
    if changes:
        flag('yellow', 'recent_changes', 'За месяц поданы заявления о смене ' + ', '.join(changes))

    # Finance.
    revenue, profit, net_assets = number(fin, 'incomeEnd'), number(fin, 'profitEnd'), marker(fin, 'netAssetEnd')
    year = marker(fin, 'lastBuhYear')
    if isinstance(net_assets, (int, float)) and net_assets < 0:
        flag('red', 'negative_net_assets', 'Отрицательные чистые активы', f'{money(net_assets)} за {year}')
    if marker(fin, 'lastBuh') is False and year:
        flag('yellow', 'stale_reporting', 'Нет бухотчётности за последний год', f'последняя — за {year}')
    if profit < 0:
        flag('yellow', 'loss', 'Убыток по итогам года', f'{money(profit)} за {year}')
    revenue_start = number(fin, 'incomeStart')
    if revenue_start > 0 and revenue < revenue_start * 0.7:
        flag('yellow', 'revenue_drop', 'Выручка упала больше чем на 30%', f'{money(revenue_start)} → {money(revenue)}')
    rating = marker(fin, 'exRating')
    if rating in WEAK_RATINGS:
        flag('red', 'weak_rating', 'Слабый экспертный рейтинг отчётности', f'{rating} за {marker(fin, "exRatingYear")}')
    elif rating in STRONG_RATINGS:
        flag('green', 'strong_rating', f'Экспертный рейтинг отчётности {rating}')
    if revenue >= 1e9 and profit > 0:
        flag('green', 'scale', 'Крупная прибыльная компания', f'выручка {money(revenue)}, прибыль {money(profit)} за {year}')

    # Courts: claims against the company relative to revenue.
    claims = sum(number(court, key) for key in ('defendantInProgressSum12Month', 'defendantLostSum12Month', 'defendantPartialLostSum12Month'))
    share = claims / revenue if revenue > 0 else None
    detail = f'{money(claims)} за 12 месяцев' + (f' — {share:.0%} выручки' if share is not None else '')
    if claims and ((share is not None and share >= 0.25) or (share is None and claims >= 1e8)):
        flag('red', 'court_claims', 'Крупные иски к компании', detail)
    elif claims and ((share is not None and share >= 0.05) or (share is None and claims >= 1e7)):
        flag('yellow', 'court_claims', 'Заметные иски к компании', detail)
    if number(court, 'defendantTaxSum') >= 1e6:
        flag('yellow', 'tax_disputes', 'Налоговые споры в арбитраже', money(number(court, 'defendantTaxSum')))

    # Enforcement proceedings (FSSP).
    debt = number(fssp, 'sum12Month') or number(fssp, 'sum')
    cases = int(number(fssp, 'count12Month') or number(fssp, 'count'))
    if number(fssp, 'salaryCount') > 0:
        flag('red', 'salary_debts', 'Взыскания по зарплате у приставов', money(number(fssp, 'salarySum')))
    if debt >= max(1e6, revenue * 0.01):
        flag('red', 'fssp_debts', 'Крупные долги у приставов', f'{cases} производств на {money(debt)}')
    elif debt > 0:
        flag('yellow', 'fssp_debts', 'Есть исполнительные производства', f'{cases} на {money(debt)}')
    elif fssp:
        flag('green', 'no_fssp', 'Нет исполнительных производств')
    if not blocks and not marker(legal, 'bankAccountFnsBlock') and blocked:
        flag('green', 'no_blocks', 'Нет блокировок счетов ФНС')

    # Links and ownership.
    if number(link, 'bankruptcyLiquidatedCount') >= 5:
        flag('yellow', 'bankrupt_links', 'Связанные компании ликвидированы через банкротство', f'{int(number(link, "bankruptcyLiquidatedCount"))} компаний')
    if number(link, 'officeAddressTodayCount') >= 10:
        flag('yellow', 'mass_address', 'Массовый адрес регистрации', f'{int(number(link, "officeAddressTodayCount"))} действующих компаний в том же офисе')
    if number(link, 'noLiquidatedManagerInnCount') >= 10:
        flag('yellow', 'mass_manager', 'Массовый руководитель', f'руководит {int(number(link, "noLiquidatedManagerInnCount"))} компаниями')
    current = owners.get('beneficialOwners') or {}
    foreign = current.get('beneficialOwnersForeign') or []
    if foreign:
        flag('yellow', 'foreign_owner', 'Иностранные конечные владельцы',
             ', '.join(f'{o.get("fullName", "")} ({o.get("country", "?")})' for o in foreign[:3]))
    capital = (owners.get('statedCapital') or {}).get('sum')
    if isinstance(capital, (int, float)) and 0 < capital <= 10_000:
        flag('yellow', 'min_capital', 'Минимальный уставный капитал', money(capital))
    top = max((o.get('share') or 0 for group in current.values() if isinstance(group, list) for o in group), default=0)
    if top:
        name = next(o.get('fullName') or o.get('fio') for group in current.values() if isinstance(group, list) for o in group if (o.get('share') or 0) == top)
        flag('green', 'owners_known', 'Конечный владелец известен', f'{name} — {top:.0f}%')

    # Kontur.Focus own scoring models.
    ratings = []
    for model in scoring:
        level, name = model.get('ratingLevel'), model.get('modelName', '')
        ratings.append({'model': name, 'rating': model.get('rating') or 0, 'level': level or ''})
        reliability = 'благонад' in name.lower()
        if level == 'Low':
            flag('red' if reliability else 'yellow', 'focus_rating_low', f'Низкий рейтинг Фокуса: {name.lower()}', f'{model.get("rating")} из 100')
        elif level == 'Middle' and reliability:
            flag('yellow', 'focus_rating_mid', f'Средний рейтинг Фокуса: {name.lower()}', f'{model.get("rating")} из 100')

    flags.sort(key=lambda f: LEVELS[f['level']])
    verdict = flags[0]['level'] if flags and flags[0]['level'] != 'green' else 'green'
    legal_name = entity.get('legalName') or {}
    return {'verdict': verdict, 'inn': inn, 'company': legal_name.get('short') or legal_name.get('full') or entity.get('fio', ''),
            'status': status.get('statusString', ''), 'flags': flags, 'ratings': ratings,
            'checkedAt': datetime.now(timezone.utc).isoformat(), 'source': 'Контур.Фокус'}
