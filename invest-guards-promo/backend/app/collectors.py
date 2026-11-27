import email.utils
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from .common import TZ
from .sources.base import SourceError

log = logging.getLogger(__name__)
NS = {'atom': 'http://www.w3.org/2005/Atom'}
FULL_TEXT_TAGS = ['{http://news.yandex.ru}full-text', '{http://purl.org/rss/1.0/modules/content/}encoded', 'full-text', 'encoded', 'description']
MAX_BODY = 6000


class CollectError(SourceError):
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


def parse_root(xml_text):
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as error:
        if 'unbound prefix' not in str(error):
            raise
        # Some feeds use yandex:/content: tags without declaring the namespace; drop prefixes and retry.
        return ET.fromstring(re.sub(r'(</?)[A-Za-z_][\w.-]*:', r'\1', re.sub(r'\s[A-Za-z_][\w.-]*:[\w.-]+="[^"]*"', '', xml_text)))


def parse_feed(xml_text):
    if '<!ENTITY' in xml_text:
        raise CollectError('Лента содержит DTD-сущности, разбор отклонён.')
    xml_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', xml_text)
    try:
        root = parse_root(xml_text)
    except ET.ParseError as error:
        # One malformed item should not cost the whole feed: parse items one by one and skip the broken ones.
        root = ET.Element('rss')
        for block in re.findall(r'<item[\s>].*?</item>', xml_text, re.S):
            try:
                root.extend(parse_root(f'<rss>{block}</rss>').iter('item'))
            except ET.ParseError:
                continue
        if not len(root):
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


def fetch_items(payload, http):
    if payload.get('kind') == 'dzen':
        from . import dzen
        return dzen.search(payload.get('queries') or [])
    response = http.get(payload['url'])
    response.raise_for_status()
    return parse_feed(response.text)


def collect_source(db, source_id, http, trigger='manual'):
    """Compatibility wrapper over the source runner: (source payload, new documents)."""
    from .sources.runner import sync_source
    run = sync_source(db, source_id, http, trigger)
    return run['payload'], run['inserted']


def due_sources(db, kinds=('rss',)):
    from .sources.runner import due_sources as due
    return due(db, kinds)
