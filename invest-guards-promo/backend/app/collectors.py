import email.utils
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from .common import TZ, display_date

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

