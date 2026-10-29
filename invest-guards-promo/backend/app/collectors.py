import email.utils
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .common import TZ, display_date
from .database import DocumentRecord, SourceRecord

log = logging.getLogger(__name__)
NS = {'atom': 'http://www.w3.org/2005/Atom'}
FULL_TEXT_TAGS = ['{http://news.yandex.ru}full-text', '{http://purl.org/rss/1.0/modules/content/}encoded', 'description']
MAX_BODY = 6000


class CollectError(Exception):
    pass


def clean_html(value):
    value = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', value or '', flags=re.S | re.I)
    value = html.unescape(re.sub(r'<[^>]+>', ' ', value))
    return re.sub(r'\s+', ' ', value).strip()


def parse_date(value):
    if not value:
        return None
    try:
        moment = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=TZ)


def node_text(parent, tag, namespaces=None):
    node = parent.find(tag, namespaces)
    return clean_html(''.join(node.itertext())) if node is not None else ''


def parse_feed(xml_text):
    if '<!ENTITY' in xml_text:
        raise CollectError('Лента содержит DTD-сущности, разбор отклонён.')
    try:
        root = ET.fromstring(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text))
    except ET.ParseError as error:
        raise CollectError(f'Некорректный XML: {error}') from error
    items = []
    for item in root.iter('item'):
        bodies = [node_text(item, tag) for tag in FULL_TEXT_TAGS]
        items.append({'title': node_text(item, 'title'), 'url': (item.findtext('link') or '').strip(),
                      'body': max(bodies, key=len), 'published_at': parse_date(item.findtext('pubDate'))})
    for entry in root.iter('{http://www.w3.org/2005/Atom}entry'):
        link = entry.find('atom:link', NS)
        bodies = [node_text(entry, f'atom:{tag}', NS) for tag in ('content', 'summary')]
        items.append({'title': node_text(entry, 'atom:title', NS),
                      'url': (link.get('href') if link is not None else '').strip(), 'body': max(bodies, key=len),
                      'published_at': parse_date(entry.findtext('atom:published', namespaces=NS) or entry.findtext('atom:updated', namespaces=NS))})
    return [{**i, 'title': i['title'][:500], 'body': i['body'][:MAX_BODY]} for i in items if i['url'].startswith('http') and i['title']]


def collect_source(db, source_id, http):
    record = db.get(SourceRecord, source_id, with_for_update=True)
    if not record:
        raise LookupError(source_id)
    payload = dict(record.payload)
    now = datetime.now(timezone.utc)
    added = 0
    try:
        response = http.get(payload['url'])
        response.raise_for_status()
        items = parse_feed(response.text)
        if items:
            result = db.execute(insert(DocumentRecord).values([{'source_id': source_id, **item} for item in items])
                                .on_conflict_do_nothing(index_elements=['url']).returning(DocumentRecord.id))
            added = len(result.all())
        payload.update(status='active', lastError=None)
    except (httpx.HTTPError, CollectError) as error:
        message = f'HTTP {error.response.status_code}' if isinstance(error, httpx.HTTPStatusError) else str(error) or type(error).__name__
        log.warning('Source %s failed: %s', source_id, message)
        payload.update(status='retry', lastError=message[:300])
    payload.update(lastRun=display_date(), lastRunAt=now.isoformat())
    record.payload = payload
    db.commit()
    return payload, added


def due_sources(db):
    now = datetime.now(timezone.utc)
    due = []
    for record in db.scalars(select(SourceRecord).order_by(SourceRecord.id)):
        payload = record.payload
        last = payload.get('lastRunAt')
        if payload.get('enabled', True) and (not last or (now - datetime.fromisoformat(last)).total_seconds() >= payload.get('scheduleMinutes', 60) * 60):
            due.append(record.id)
    return due
