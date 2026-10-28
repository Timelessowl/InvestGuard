import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from .common import display_date, initials, normalize_company, overall_score, urgency_for
from .database import CandidateRecord, DocumentRecord, SourceRecord, insert_candidate
from .geo import REGION_RE, canonical_municipality, lookup
from .llm import BudgetExceeded, LlmError
from .schemas import Candidate, Evidence

log = logging.getLogger(__name__)

INVEST_RE = re.compile(r'завод|производств|цех[аеуо]?\b|площадк|инвест|строительств|построит|модерниз|мощност|запуск|'
                       r'расширени|импортозамещ|технопарк|\bОЭЗ\b|экономическ\w* зон|резидент|комбинат|оборудовани|'
                       r'рабочих мест|млрд|логистическ\w* (центр|комплекс)', re.I)
SIGNAL_TYPES = ['Расширение производства', 'Новый объект', 'Модернизация', 'Импортозамещение', 'Новая технология', 'Другое']
STAGES = ['Анонс', 'Подготовка', 'Проектирование', 'Строительство', 'Запуск', 'Неизвестно']
SECTORS = ['Машиностроение', 'Металлургия', 'Химическая промышленность', 'Композитные материалы', 'Строительные материалы',
           'Пищевая промышленность', 'Сельское хозяйство', 'Логистика', 'Энергетика', 'IT и связь', 'Медицина и фармацевтика',
           'Лесная и деревообработка', 'Туризм и гостиницы', 'Торговля и сервис', 'Другое']
ACTIONS = ['Связаться с компанией', 'Запросить данные', 'Передать эксперту', 'Продолжить мониторинг']
DUPLICATE_SIMILARITY = 0.93
LOCK_ID = 1847302

TRIAGE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'is_investment_signal': {'type': 'boolean'}, 'in_sverdlovsk_region': {'type': 'boolean'},
    'company': {'type': 'string'}, 'municipality': {'type': 'string'}, 'signal_title': {'type': 'string'},
    'signal_type': {'type': 'string', 'enum': SIGNAL_TYPES}, 'amount_rub': {'type': ['number', 'null']},
    'key_quote': {'type': 'string'}, 'confidence': {'type': 'integer'}, 'reason': {'type': 'string'}},
    'required': ['is_investment_signal', 'in_sverdlovsk_region', 'company', 'municipality', 'signal_title', 'signal_type',
                 'amount_rub', 'key_quote', 'confidence', 'reason']}
TRIAGE_PROMPT = (
    'Ты аналитик Корпорации развития Среднего Урала. Найди в тексте сигнал о конкретном инвестиционном проекте бизнеса '
    'в Свердловской области: строительство или расширение производства, логистического, агро- или энергетического объекта, '
    'гостиницы, модернизация с закупкой оборудования, выход на новую площадку, резидентство ОЭЗ. '
    'Не сигнал: бюджетный ремонт дорог, школ и ЖКХ, социальные объекты, жилые комплексы, форумы, выставки и презентации опыта, '
    'программы господдержки и фондов без конкретного проекта компании, назначения, криминал, суды, банкротства, '
    'события за пределами Свердловской области, обзоры без конкретной компании. '
    'Заполняй поля строго по тексту, ничего не выдумывай. company — коммерческая компания-инициатор проекта '
    '(не министерство, фонд или администрация) или пустая строка. '
    'municipality — город или округ в именительном падеже. signal_title — суть сигнала до 90 символов. '
    'key_quote — дословный фрагмент текста до 300 символов. confidence — 0–100, насколько текст уверенно описывает проект. '
    'reason — одно короткое предложение, почему это сигнал или нет.')

SCORE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'company': {'type': 'string'}, 'sector': {'type': 'string', 'enum': SECTORS}, 'summary': {'type': 'string'},
    'amount_display': {'type': 'string'}, 'stage': {'type': 'string', 'enum': STAGES},
    'relevance': {'type': 'integer'}, 'confidence': {'type': 'integer'}, 'regional_effect': {'type': 'integer'},
    'urgency': {'type': 'integer'}, 'recommendation': {'type': 'string'}, 'rationale': {'type': 'string'},
    'next_action': {'type': 'string', 'enum': ACTIONS}},
    'required': ['company', 'sector', 'summary', 'amount_display', 'stage', 'relevance', 'confidence', 'regional_effect',
                 'urgency', 'recommendation', 'rationale', 'next_action']}
SCORE_PROMPT = (
    'Ты эксперт по инвестиционным проектам Свердловской области. По сигналу составь карточку для аналитика. '
    'Оцени факторы от 0 до 100: relevance — насколько это реальный инвестпроект в интересах региона; '
    'confidence — насколько факты подтверждены (официальное заявление выше, слухи ниже); '
    'regional_effect — вклад в экономику региона (инвестиции, рабочие места, налоги); '
    'urgency — насколько срочно связаться (ранняя стадия, площадка и меры поддержки ещё не выбраны — выше). '
    'summary — 2–3 предложения по фактам. amount_display — сумма в виде «1,8 млрд ₽» или «Не указано». '
    'recommendation — что сделать аналитику, одним предложением. rationale — почему, одно-два предложения. '
    'Пиши по-русски, кратко, без выдумок.')


def prefilter(title, body, regional):
    content = f'{title} {body}'
    hits = {match.group(0).lower()[:6] for match in INVEST_RE.finditer(content)}
    if len(hits) < 2:
        return 'Нет признаков инвестпроекта'
    if not regional and not REGION_RE.search(content):
        return 'Нет упоминания Свердловской области'
    return None


def unit(vector):
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [round(v / norm, 5) for v in vector]


def similarity(a, b):
    return sum(x * y for x, y in zip(a, b))


def clamp(value):
    return max(0, min(100, int(value or 0)))


def evidence_for(doc, source_name, quote=None):
    excerpt = (quote or '').strip() or doc.title
    return {'source': f'{source_name} · {doc.title[:90]}', 'date': display_date(doc.published_at or doc.collected_at),
            'excerpt': excerpt[:400], 'highlights': [], 'url': doc.url}


def attach_evidence(record, evidence):
    candidate = Candidate.model_validate(record.payload)
    if any(e.url == evidence['url'] for e in candidate.evidence):
        return candidate
    new_source = evidence['source'].split(' · ')[0] not in {e.source.split(' · ')[0] for e in candidate.evidence}
    candidate.evidence.append(Evidence.model_validate(evidence))
    if new_source:
        candidate.factors['Уверенность'] = min(100, candidate.factors.get('Уверенность', 50) + 6)
        candidate.score = overall_score(candidate.factors)
    record.payload = candidate.model_dump()
    record.updated_at = datetime.now(timezone.utc)
    return candidate


def find_candidate(db, company):
    key = normalize_company(company)
    if len(key) < 3:
        return None
    for record in db.scalars(select(CandidateRecord)):
        if normalize_company(record.payload.get('company', '')) == key:
            return record
    return None


def build_candidate(doc, source_name, triage, card):
    factors = {'Релевантность': clamp(card['relevance']), 'Уверенность': clamp(card['confidence']),
               'Региональный эффект': clamp(card['regional_effect']), 'Срочность': clamp(card['urgency'])}
    urgency, label = urgency_for(factors['Срочность'])
    municipality = canonical_municipality(triage['municipality']) or triage['municipality'].strip() or 'Не указан'
    company = card['company'].strip() or triage['company'].strip()
    return Candidate(id='pending', company=company, initials=initials(company), sector=card['sector'], city=municipality,
                     location=lookup(municipality), signalType=triage['signal_type'],
                     signal=triage['signal_title'].strip()[:160] or doc.title[:160], source=source_name,
                     sourceDate=display_date(doc.published_at or doc.collected_at), score=overall_score(factors),
                     urgency=urgency, urgencyLabel=label, status='new', owner='Не назначен',
                     amount=card['amount_display'].strip() or 'Не указано', stage=card['stage'], summary=card['summary'].strip(),
                     factors=factors, recommendation=card['recommendation'].strip(), rationale=card['rationale'].strip(),
                     nextAction=card['next_action'], evidence=[evidence_for(doc, source_name, triage['key_quote'])], origin='ai')


def document_prompt(doc, source_name):
    published = (doc.published_at or doc.collected_at).astimezone(timezone.utc).date().isoformat()
    return f'Источник: {source_name}\nДата: {published}\nЗаголовок: {doc.title}\nТекст: {doc.body[:3000]}'


def finish(doc, stage, reason, candidate_number=None):
    doc.stage, doc.reason, doc.processed_at = stage, reason[:500] if reason else None, datetime.now(timezone.utc)
    if candidate_number:
        doc.candidate_number = candidate_number


def process_document(db, llm, doc, sources):
    source = sources.get(doc.source_id, {})
    source_name = source.get('name', doc.source_id)
    skip = prefilter(doc.title, doc.body, source.get('regional', True))
    if skip:
        return finish(doc, 'skipped', skip)
    if doc.embedding is None:
        doc.embedding = unit(llm.embed([f'{doc.title}. {doc.body[:1500]}'])[0])
    since = datetime.now(timezone.utc) - timedelta(days=30)
    neighbours = db.execute(select(DocumentRecord.id, DocumentRecord.stage, DocumentRecord.candidate_number, DocumentRecord.embedding)
                            .where(DocumentRecord.id != doc.id, DocumentRecord.embedding.is_not(None),
                                   DocumentRecord.stage.in_(['signal', 'merged', 'rejected']), DocumentRecord.collected_at >= since)).all()
    best = max(neighbours, key=lambda n: similarity(doc.embedding, n.embedding), default=None)
    if best and similarity(doc.embedding, best.embedding) >= DUPLICATE_SIMILARITY:
        if best.stage == 'rejected':
            return finish(doc, 'rejected', f'Повтор отклонённого документа #{best.id}')
        if best.candidate_number:
            record = db.get(CandidateRecord, best.candidate_number, with_for_update=True)
            if record:
                attach_evidence(record, evidence_for(doc, source_name))
                return finish(doc, 'merged', f'Повтор документа #{best.id}', best.candidate_number)
    triage = llm.chat_json([{'role': 'system', 'content': TRIAGE_PROMPT}, {'role': 'user', 'content': document_prompt(doc, source_name)}],
                           TRIAGE_SCHEMA, purpose='triage', model=llm.settings.triage_model, max_tokens=500)
    doc.extraction = triage
    if not (triage['is_investment_signal'] and triage['in_sverdlovsk_region'] and triage['company'].strip()):
        return finish(doc, 'rejected', triage.get('reason') or 'Не инвестиционный сигнал')
    if clamp(triage['confidence']) < 50:
        return finish(doc, 'rejected', f'Низкая уверенность ({clamp(triage["confidence"])}): {triage.get("reason", "")}')
    existing = find_candidate(db, triage['company'])
    if existing:
        db.refresh(existing, with_for_update=True)
        attach_evidence(existing, evidence_for(doc, source_name, triage['key_quote']))
        return finish(doc, 'merged', f'Добавлено к {existing.payload["id"]}', existing.number)
    card = llm.chat_json([{'role': 'system', 'content': SCORE_PROMPT},
                          {'role': 'user', 'content': f'{document_prompt(doc, source_name)}\n\nИзвлечённые факты: {json.dumps(triage, ensure_ascii=False)}'}],
                         SCORE_SCHEMA, purpose='scoring', model=llm.settings.smart_model, max_tokens=900)
    record = insert_candidate(db, build_candidate(doc, source_name, triage, card))
    return finish(doc, 'signal', f'Создана карточка {record.payload["id"]}', record.number)


def process_pending(sessions, llm, limit=20):
    """Process up to `limit` new documents; an advisory lock keeps it single-runner."""
    counts = {'processed': 0, 'skipped': 0, 'rejected': 0, 'signal': 0, 'merged': 0, 'error': 0}
    with sessions() as lock:
        if not lock.execute(text('SELECT pg_try_advisory_lock(:id)'), {'id': LOCK_ID}).scalar():
            return {**counts, 'busy': True}
        try:
            with sessions() as db:
                sources = {r.id: r.payload for r in db.scalars(select(SourceRecord))}
                ids = db.scalars(select(DocumentRecord.id).where(DocumentRecord.stage == 'new')
                                 .order_by(DocumentRecord.published_at.desc().nulls_last()).limit(limit)).all()
            for doc_id in ids:
                with sessions() as db:
                    doc = db.get(DocumentRecord, doc_id, with_for_update=True)
                    if doc is None or doc.stage != 'new':
                        continue
                    try:
                        process_document(db, llm, doc, sources)
                    except BudgetExceeded as error:
                        db.rollback()
                        return {**counts, 'stopped': str(error)}
                    except (LlmError, KeyError, TypeError, ValueError) as error:
                        db.rollback()
                        doc = db.get(DocumentRecord, doc_id, with_for_update=True)
                        doc.attempts += 1
                        if doc.attempts >= 3:
                            finish(doc, 'error', str(error) or type(error).__name__)
                        log.warning('Document %s failed: %s', doc_id, error)
                    counts[doc.stage if doc.stage != 'new' else 'error'] += 1
                    counts['processed'] += 1
                    db.commit()
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(:id)'), {'id': LOCK_ID})
            lock.commit()
    return counts


def stage_counts(db):
    return {stage: count for stage, count in db.execute(text('SELECT stage, count(*) FROM documents GROUP BY stage'))}
