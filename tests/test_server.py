import concurrent.futures
import gzip
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

def article(title='Ukraine conflict update', hours=2, url='https://example.org/news'):
    return {'title': title, 'published': (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(),
            'url': url, 'source': 'Test source', 'kind': 'media'}

class NewsTests(unittest.TestCase):
    def setUp(self):
        # Existing RSS/cache tests must not make live GDELT/ReliefWeb requests.
        self.structured_sources_patch = patch.dict('os.environ', {'CONFLICTSCOPE_STRUCTURED_SOURCES': '0'})
        self.structured_sources_patch.start()
        self.temp = tempfile.TemporaryDirectory()
        self.old_cache_dir = server.CACHE_DIR
        server.CACHE_DIR = Path(self.temp.name)
        server.FEED_CACHE.clear()

    def tearDown(self):
        server.CACHE_DIR = self.old_cache_dir
        self.temp.cleanup()
        self.structured_sources_patch.stop()

    def test_rss_direct_link_gzip_and_reject_unsafe(self):
        raw = b'''<rss xmlns:n="urn:bing"><channel><item><title>Ukraine &amp; war</title><link>https://www.bing.com/news/apiclick?url=https%3A%2F%2Fexample.org%2Fstory</link><pubDate>Tue, 13 Oct 2026 03:00:00 GMT</pubDate><n:Source>Publisher</n:Source></item><item><title>Bad</title><link>javascript:alert(1)</link><pubDate>Tue, 13 Oct 2026 03:00:00 GMT</pubDate></item><item><title>Undated</title><link>https://example.org/undated</link></item></channel></rss>'''
        result = server.parse_rss(gzip.compress(raw), 'Bing News')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['url'], 'https://example.org/story')
        self.assertEqual(result[0]['source'], 'Publisher')
        self.assertEqual(result[0]['title'], 'Ukraine & war')

    def test_dates_sorted_and_old_and_future_excluded(self):
        result = server.within_days([article(hours=6), article(hours=1), article(hours=1000), article(hours=-24)], 7)
        self.assertEqual(len(result), 2)
        self.assertGreater(result[0]['published'], result[1]['published'])

    def test_country_names_do_not_match_other_countries(self):
        self.assertFalse(server.relevant(article('Nigeria attack'), server.COUNTRIES['NER']))
        self.assertFalse(server.relevant(article('Somalia conflict'), server.COUNTRIES['MLI']))
        self.assertTrue(server.relevant(article('Niger attack'), server.COUNTRIES['NER']))
        self.assertTrue(server.relevant(article('Ukrainian forces'), server.COUNTRIES['UKR']))

    def test_live_deduplication_cache_and_force_refresh(self):
        with patch.object(server, 'fetch_feed', return_value=[article()]) as fetch:
            result = server.get_news('UKR', 30)
            self.assertEqual(result['status'], 'live')
            self.assertEqual(len(result['articles']), 1)
            self.assertEqual(fetch.call_count, 3)
            cached = server.get_news('UKR', 30)
            self.assertTrue(cached['from_cache'])
            self.assertEqual(fetch.call_count, 3)
            server.get_news('UKR', 30, force=True)
            self.assertEqual(fetch.call_count, 6)
            self.assertTrue(all(x.args[2] is True for x in fetch.call_args_list[-3:]))

    def test_structured_source_survives_rss_outage(self):
        with patch.dict('os.environ', {'CONFLICTSCOPE_STRUCTURED_SOURCES': '1'}):
            with patch.object(server, 'fetch_feed', side_effect=OSError('rss offline')):
                with patch.object(server.source_collectors, 'collect',
                                  return_value=([article()], ['GDELT'], ['ReliefWeb'])):
                    result = server.get_news('UKR')
        self.assertEqual(result['status'], 'live')
        self.assertIn('GDELT', result['providers'])
        self.assertIn('ReliefWeb', result['failed_providers'])
        self.assertEqual(len(result['articles']), 1)

    def test_failed_feeds_use_stale_cache_with_original_fetch_time(self):
        with patch.object(server, 'fetch_feed', return_value=[article()]):
            original = server.get_news('UKR')
        data = server.load_cache('UKR'); data['fetched_epoch'] = 0
        server.cache_path('UKR').write_text(json.dumps(data))
        with patch.object(server, 'fetch_feed', side_effect=OSError('offline')):
            result = server.get_news('UKR')
        self.assertEqual(result['status'], 'stale')
        self.assertEqual(result['fetched_at'], original['fetched_at'])
        self.assertEqual(len(result['failed_providers']), 3)

    def test_snapshot_is_never_labeled_live_or_newly_fetched(self):
        seed = [{**article(), 'ids': ['UKR']}]
        with patch.object(server, 'fetch_feed', side_effect=OSError('offline')), patch.object(server, 'SEED', seed):
            result = server.get_news('UKR')
        self.assertEqual(result['status'], 'snapshot')
        self.assertIsNone(result['fetched_at'])
        self.assertEqual(len(result['articles']), 1)

    def test_no_recent_results_does_not_fabricate_articles(self):
        with patch.object(server, 'fetch_feed', return_value=[article(hours=4000)]), patch.object(server, 'SEED', []):
            result = server.get_news('UKR', 7)
        self.assertEqual(result['articles'], [])
        self.assertEqual(result['status'], 'empty')

    def test_parallel_same_country_fetches_only_once(self):
        with patch.object(server, 'fetch_feed', return_value=[article()]) as fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: server.get_news('UKR'), range(4)))
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sum(not r['from_cache'] for r in results), 1)

class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Ephemeral integration fixture, not a preview/development server.
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def read(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as response:
            return response.status, response.headers, response.read()

    def test_page_assets_and_config(self):
        self.assertIn('冲突观察'.encode(), self.read('/')[2])
        self.assertGreater(len(self.read('/world.json')[2]), 10000)
        self.assertEqual(len(json.loads(self.read('/api/conflicts')[2])['countries']), 19)
        self.assertTrue(json.loads(self.read('/api/health')[2])['ok'])
        for path in ('/app.js', '/style.css'):
            self.assertEqual(self.read(path)[0], 200)

    def test_reject_invalid_country_and_range(self):
        for path in ('/api/news?country=../../server.py', '/api/news?country=UKR&days=bad', '/api/news?country=UKR&days=999'):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.read(path)
            self.assertEqual(context.exception.code, 400)

    def test_news_interface_and_refresh_flag(self):
        with patch.object(server, 'get_news', return_value={'status': 'live', 'articles': []}) as fn:
            status, headers, raw = self.read('/api/news?country=UKR&days=7&refresh=1')
        self.assertEqual(status, 200)
        self.assertIn('application/json', headers['Content-Type'])
        self.assertEqual(json.loads(raw)['status'], 'live')
        fn.assert_called_once_with('UKR', 7, True)

class DataTests(unittest.TestCase):
    def test_tracked_countries_all_have_geometry_and_sources(self):
        world = json.loads((server.ROOT/'static/world.json').read_text())
        ids = {f['properties']['ISO3166-1-Alpha-3'] for f in world['features']}
        for country in server.CONFIG['countries']:
            self.assertIn(country['id'], ids)
            self.assertIn(country['category'], ('persistent', 'localized', 'spillover'))
            self.assertTrue(country['areas'])
            self.assertTrue(country['sources'])
            for source in country['sources']:
                self.assertTrue(server.safe_url(source['url']))
                self.assertTrue(source['date'])

if __name__ == '__main__':
    unittest.main()
