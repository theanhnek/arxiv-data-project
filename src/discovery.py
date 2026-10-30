"""Resolve assigned arXiv IDs and enumerate all source versions."""
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
import requests
from .config import CrawlConfig

API = 'https://export.arxiv.org/api/query'
NS = {'a': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
PATTERN = re.compile(r'(?P<base>(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7}))(?:v[1-9]\d*)?', re.I)

def normalize_id(value):
    value = value.strip()
    if value.lower().startswith('arxiv:'):
        value = value[6:].strip()
    match = PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f'Invalid arXiv ID: {value!r}')
    return match['base']

def load_ids(path):
    ids, invalid, duplicates = [], [], []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            base = normalize_id(line)
        except ValueError as exc:
            invalid.append({'line': number, 'input': line, 'error': str(exc)})
            continue
        if base in ids:
            duplicates.append({'line': number, 'input': line, 'arxiv_id': base})
        else:
            ids.append(base)
    return {'ids': ids, 'invalid': invalid, 'duplicates': duplicates}

def parse_paper(content, expected):
    root = ET.fromstring(content)
    for entry in root.findall('a:entry', NS):
        identifier = entry.findtext('a:id', '', NS)
        if identifier.endswith('/errors'):
            raise ValueError(entry.findtext('a:summary', 'API error', NS))
        identifier = identifier.split('/abs/')[-1]
        try:
            base = normalize_id(identifier)
        except ValueError:
            continue
        if base != expected:
            continue
        version = re.search(r'v([1-9]\d*)$', identifier)
        if not version:
            raise ValueError('Missing latest-version suffix')
        latest = int(version[1])
        def text(field):
            return ' '.join(entry.findtext(field, '', NS).split())
        return {
            'arxiv_id': base, 'title': text('a:title'),
            'authors': [' '.join(a.findtext('a:name', '', NS).split()) for a in entry.findall('a:author', NS)],
            'abstract': text('a:summary'), 'submission_date': text('a:published') or None,
            'latest_updated_date': text('a:updated') or None,
            'revised_dates': [], 'revision_dates_complete': latest == 1,
            'venue': text('x:journal_ref') or None, 'doi': text('x:doi') or None,
            'latest_version': latest,
            'versions': [{'version': n, 'version_id': f'{base}v{n}',
                          'source_url': f'https://export.arxiv.org/src/{base}v{n}',
                          'download_status': 'pending', 'extraction_status': 'pending'}
                         for n in range(1, latest + 1)],
            'metadata_source': API,
        }
    raise LookupError(f'No matching entry for {expected}')

class ArxivClient:
    """Sequential requests; reuse this client when integrating downloader."""
    def __init__(self, config):
        if config.arxiv_delay_seconds < 3 or config.max_attempts < 1 or config.request_timeout_seconds <= 0:
            raise ValueError('Require delay >= 3s, attempts >= 1, timeout > 0')
        self.config = config
        self.session = requests.Session()
        self.session.headers['User-Agent'] = config.user_agent
        self.last_request = None

    def fetch(self, base):
        for attempt in range(self.config.max_attempts):
            if self.last_request is not None:
                time.sleep(max(0, self.config.arxiv_delay_seconds - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(API, params={'id_list': base, 'max_results': 1}, timeout=self.config.request_timeout_seconds)
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 == self.config.max_attempts:
                        response.raise_for_status()
                    delay = 3 * 2 ** attempt
                    try:
                        delay = max(delay, float(response.headers.get('Retry-After', '0')))
                    except ValueError:
                        pass
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                return response.content
            except (requests.Timeout, requests.ConnectionError):
                if attempt + 1 == self.config.max_attempts:
                    raise
                time.sleep(3 * 2 ** attempt)
        raise RuntimeError('Attempts exhausted')

    def close(self):
        self.session.close()

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)

def discover_papers(ids, config, *, resume=False, client=None):
    bases = list(dict.fromkeys(normalize_id(value) for value in ids))
    path = config.output_dir / 'state' / 'discovery.json'
    cached = {}
    if resume and path.exists():
        previous = json.loads(path.read_text(encoding='utf-8'))
        cached = {p['arxiv_id']: p for p in previous.get('papers', []) if p.get('status') == 'resolved'}
    started = time.monotonic()
    report = {'schema_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(), 'papers': []}
    owned = client is None
    client = client or ArxivClient(config)
    try:
        for base in bases:
            paper_start = time.monotonic()
            if base in cached:
                record = dict(cached[base], cache_hit=True)
            else:
                try:
                    record = dict(parse_paper(client.fetch(base), base), status='resolved', cache_hit=False,
                                  fetched_at=datetime.now(timezone.utc).isoformat())
                except (requests.RequestException, ET.ParseError, ValueError, LookupError) as exc:
                    record = {'arxiv_id': base, 'status': 'failed', 'error_type': type(exc).__name__, 'error': str(exc), 'cache_hit': False}
            record['discovery_seconds'] = time.monotonic() - paper_start
            report['papers'].append(record)
            report['elapsed_seconds'] = time.monotonic() - started
            write_json(path, report)
    finally:
        if owned:
            client.close()
    resolved = [p for p in report['papers'] if p['status'] == 'resolved']
    report['summary'] = {'assigned_unique_ids': len(bases), 'resolved': len(resolved), 'failed': len(bases)-len(resolved),
                         'expected_versions': sum(p['latest_version'] for p in resolved),
                         'cache_hits': sum(p['cache_hit'] for p in report['papers'])}
    report['elapsed_seconds'] = time.monotonic() - started
    write_json(path, report)
    return report
