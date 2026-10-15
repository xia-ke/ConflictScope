#!/usr/bin/env python3
"""Conflict Atlas: Python 3.10+ standard library, local-only HTTP + RSS aggregation."""
import argparse
import concurrent.futures
import gzip
import html
import json
import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import source_collectors

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'data/conflicts.json').read_text(encoding='utf-8'))
COUNTRIES = {x['id']: x for x in CONFIG['countries']}
SEED = json.loads((ROOT / 'data/seed-news.json').read_text(encoding='utf-8'))
CACHE_DIR = ROOT / '.cache'
LOCK = threading.RLock()
COUNTRY_LOCKS = {k: threading.Lock() for k in COUNTRIES}
FEED_CACHE = {}
TTL = 900
UTC = timezone.utc

def now_iso():
    return datetime.now(UTC).isoformat()

def plain(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value or '')).strip()

def safe_url(value):
    try:
        p = urllib.parse.urlsplit(value)
        return value if p.scheme in ('https', 'http') and p.hostname and not p.username else ''
    except ValueError:
        return ''

def parse_rss(raw, provider):
    if raw.startswith(b'\x1f\x8b'):
        raw = gzip.decompress(raw)
    if len(raw) > 5_000_000:
        raise ValueError('Feed too large')
    root = ET.fromstring(raw)
    result = []
    for item in root.findall('.//item')[:100]:
        title = plain(item.findtext('title'))
        url = item.findtext('link') or ''
        if provider == 'Bing News':
            url = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get('url', [url])[0]
        url = safe_url(url)
        try:
            published = parsedate_to_datetime(item.findtext('pubDate') or '').astimezone(UTC).isoformat()
        except (ValueError, TypeError, OverflowError):
            continue  # Undated articles cannot be represented as recent.
        source = provider
        for node in item:
            if node.tag.split('}')[-1] in ('Source', 'source') and node.text:
                source = plain(node.text)
        if title and url:
            result.append({'title': title, 'url': url, 'source': source,
                           'published': published, 'kind': 'official' if provider == 'UN News' else 'media'})
    return result

def fetch_feed(url, provider, force=False):
    with LOCK:
        old = FEED_CACHE.get(url)
        if old and not force and time.time() - old[0] < TTL:
            return old[1]
    request = urllib.request.Request(url, headers={'User-Agent': 'ConflictAtlas/1.0 (+local RSS reader)', 'Accept': 'application/rss+xml,application/xml'})
    with urllib.request.urlopen(request, timeout=9) as response:
        raw = response.read(5_000_001)
    articles = parse_rss(raw, provider)
    if not articles:
        raise ValueError('Empty RSS feed')
    with LOCK:
        FEED_CACHE[url] = (time.time(), articles)
    return articles

def cache_path(country_id):
    return CACHE_DIR / (country_id + '.json')

def load_cache(country_id):
    try:
        return json.loads(cache_path(country_id).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None

def relevant(article, country):
    text = article['title'].lower()
    # Word boundaries prevent Niger/Nigeria and Mali/Somalia mismatches.
    return any(re.search(r'\b' + re.escape(k) + (r'\b' if k in ('niger', 'mali', 'chad') else ''), text) for k in country['keywords'])

def within_days(articles, days):
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=days)
    valid = []
    for article in articles:
        try:
            when = datetime.fromisoformat(article['published'])
            if cutoff <= when <= now + timedelta(hours=2):
                valid.append(article)
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(valid, key=lambda x: x['published'], reverse=True)[:30]

def get_news(country_id, days=30, force=False):
    country = COUNTRIES[country_id]
    with COUNTRY_LOCKS[country_id]:
        old = load_cache(country_id)
        if old and not force and time.time() - old.get('fetched_epoch', 0) < TTL:
            return {**old, 'articles': within_days(old['articles'], days), 'from_cache': True}
        query = urllib.parse.urlencode({'q': country['query'], 'format': 'rss', 'setlang': 'en-US'})
        feeds = [
            ('https://www.bing.com/news/search?' + query, 'Bing News'),
            ('https://feeds.bbci.co.uk/news/world/rss.xml', 'BBC News'),
            ('https://news.un.org/feed/subscribe/en/news/all/rss.xml', 'UN News'),
        ]
        articles, providers, errors = [], [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            jobs = {pool.submit(fetch_feed, url, provider, force): provider for url, provider in feeds}
            for future in concurrent.futures.as_completed(jobs):
                provider = jobs[future]
                try:
                    articles.extend(x for x in future.result() if relevant(x, country))
                    providers.append(provider)
                except Exception:
                    errors.append(provider)
        # Supplement RSS with structured discovery; disable with CONFLICTSCOPE_STRUCTURED_SOURCES=0.
        if os.getenv('CONFLICTSCOPE_STRUCTURED_SOURCES', '1') == '1':
            extra, ok, bad = source_collectors.collect(country, days)
            articles.extend(x for x in extra if relevant(x, country))
            providers.extend(ok)
            errors.extend(bad)
        seen = set()
        unique = []
        for x in sorted(articles, key=lambda a: a['published'], reverse=True):
            if x['url'] not in seen:
                seen.add(x['url']); unique.append(x)
        recent = within_days(unique, days)
        if recent:
            payload = {'country': country_id, 'articles': unique, 'status': 'live',
                       'fetched_at': now_iso(), 'fetched_epoch': time.time(),
                       'providers': providers, 'failed_providers': errors, 'from_cache': False,
                       'message': '已从新闻订阅源获取；聚合结果可能遗漏报道。'}
            CACHE_DIR.mkdir(exist_ok=True)
            temp = cache_path(country_id).with_suffix('.tmp')
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            temp.replace(cache_path(country_id))
            return {**payload, 'articles': recent}
        if old and within_days(old.get('articles', []), days):
            return {**old, 'articles': within_days(old['articles'], days), 'status': 'stale',
                    'failed_providers': errors, 'from_cache': True,
                    'message': '本次未取得近期结果，显示上次成功获取的缓存。'}
        seed = within_days([{**x, 'kind': 'media'} for x in SEED if country_id in x['ids']], days)
        return {'country': country_id, 'articles': seed, 'status': 'snapshot' if seed else 'empty',
                'fetched_at': None, 'attempted_at': now_iso(), 'snapshot_date': CONFIG['updated'],
                'providers': providers, 'failed_providers': errors, 'from_cache': False,
                'message': '本次未取得近期结果，显示已核实快照；不是实时新闻。' if seed else '本次未取得近期新闻，也没有符合时间范围的快照。'}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'static'), **kwargs)

    def json_response(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == '/api/conflicts':
            self.json_response(CONFIG); return
        if parsed.path == '/api/health':
            self.json_response({'ok': True, 'time': now_iso()}); return
        if parsed.path == '/api/news':
            args = urllib.parse.parse_qs(parsed.query)
            country_id = args.get('country', [''])[0]
            try:
                days = int(args.get('days', ['30'])[0])
            except ValueError:
                self.json_response({'error': '无效时间范围'}, 400); return
            if country_id not in COUNTRIES or days not in (7, 30, 90):
                self.json_response({'error': '无效国家或时间范围'}, 400); return
            try:
                self.json_response(get_news(country_id, days, args.get('refresh', ['0'])[0] == '1'))
            except (OSError, ValueError):
                self.json_response({'error': '新闻服务暂时不可用，请重试'}, 503)
            return
        if parsed.path.startswith('/api/'):
            self.json_response({'error': '接口不存在'}, 404); return
        super().do_GET()

def main():
    parser = argparse.ArgumentParser(description='本地冲突地图与新闻聚合')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as e:
        parser.exit(1, f'启动失败：{e}\n可使用 --port 8766 更换端口。\n')
    url = f'http://127.0.0.1:{args.port}'
    print(f'Conflict Atlas 已启动：{url}\n按 Ctrl+C 停止。', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
