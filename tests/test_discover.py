import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

SPEC = importlib.util.spec_from_file_location('discover', Path(__file__).parents[1] / 'scripts' / 'discover.py')
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)

NOW = '2026-09-20T01:00:00+00:00'
LATER = '2026-09-27T01:00:00+00:00'

INDEX_XML = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
             '<sitemap><loc>https://brand.test/sitemap-a.xml</loc></sitemap></sitemapindex>')
URLSET_XML = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
              '<url><loc>https://brand.test/docs/benefits</loc></url>'
              '<url><loc>https://brand.test/docs/benefits/new</loc></url>'
              '<url><loc>https://brand.test/docs/developer/x</loc></url>'
              '<url><loc>https://brand.test/blog/post</loc></url></urlset>')
URLSET_XML_RUN2 = URLSET_XML.replace('</urlset>', '<url><loc>https://brand.test/docs/activity/launch</loc></url></urlset>')

UNDATED_ARTICLE = ('<html><head><title>福利</title></head><body><article><h1>千问办公新用户福利</h1>'
                   '<p>新用户登录即可领取免费额度，具体规则以官方页面说明为准。</p></article></body></html>')
DATED_ARTICLE = ('<html><head><title>更新</title>'
                 '<meta property="article:published_time" content="2026-09-25T09:00:00+08:00"></head>'
                 '<body><article><h1>版本更新说明</h1>'
                 '<p>本次更新新增了多项实用功能，并修复了若干已知问题，提升整体使用体验。</p></article></body></html>')


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def get(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise d.AccessRestricted('missing fixture')
        return BeautifulSoup(self.pages[url], 'html.parser')


def candidate(url, product='qwenwork', brand='TestBrand', region='cn', timezone='Asia/Shanghai'):
    return {'brand': brand, 'productId': product, 'url': url, 'region': region,
            'timezone': timezone, 'hosts': ['brand.test'],
            'contentSelector': 'article,main', 'titleSelector': 'h1'}


class ParsingTests(unittest.TestCase):
    def test_parse_urlset_and_index(self):
        kind, locs = d.parse_sitemap(URLSET_XML)
        self.assertEqual(kind, 'urlset')
        self.assertIn('https://brand.test/docs/benefits', locs)
        kind, locs = d.parse_sitemap(INDEX_XML)
        self.assertEqual(kind, 'index')
        self.assertEqual(locs, ['https://brand.test/sitemap-a.xml'])

    def test_url_matches(self):
        include = [r'^https://brand\.test/docs/(benefits|activity)(/|$)']
        exclude = ['/docs/developer/']
        self.assertTrue(d.url_matches('https://brand.test/docs/benefits', include, exclude))
        self.assertTrue(d.url_matches('https://brand.test/docs/activity/launch', include, exclude))
        self.assertFalse(d.url_matches('https://brand.test/docs/developer/x', include, exclude))
        self.assertFalse(d.url_matches('https://brand.test/blog/post', include, exclude))
        self.assertTrue(d.url_matches('https://brand.test/anything', [], []))

    def test_url_matches_is_case_insensitive(self):
        self.assertTrue(d.url_matches('https://brand.test/docs/Changelog', ['/changelog'], []))


class EvaluateTests(unittest.TestCase):
    def test_baseline_is_silent_and_marks_seen(self):
        candidates = [candidate('https://x/a'), candidate('https://x/b')]
        ledger = {}
        new, ledger, first = d.evaluate(candidates, {'https://x/a'}, ledger, NOW)
        self.assertTrue(first)
        self.assertEqual(new, [])
        self.assertIn('https://x/a', ledger['urls'])
        self.assertIn('https://x/b', ledger['urls'])
        self.assertEqual(ledger['urls']['https://x/b']['status'], 'baseline')

    def test_delta_returns_new_without_marking_seen(self):
        ledger = {}
        d.evaluate([candidate('https://x/a')], set(), ledger, NOW)
        later = [candidate('https://x/a'), candidate('https://x/c')]
        new, ledger, first = d.evaluate(later, set(), ledger, LATER)
        self.assertFalse(first)
        self.assertEqual([item['url'] for item in new], ['https://x/c'])
        self.assertNotIn('https://x/c', ledger['urls'])

    def test_tracked_source_not_returned(self):
        ledger = {'baselineAt': NOW, 'urls': {}}
        new, _, _ = d.evaluate([candidate('https://x/a')], {'https://x/a'}, ledger, LATER)
        self.assertEqual(new, [])


class PendingTests(unittest.TestCase):
    def test_pending_when_no_date_and_no_benefit_date(self):
        self.assertTrue(d.is_pending({'publishedAt': None, 'benefit': None}))
        self.assertTrue(d.is_pending({'publishedAt': None, 'benefit': {'startAt': None, 'endAt': None, 'ongoing': False}}))

    def test_not_pending_when_dated_or_benefit_dated(self):
        self.assertFalse(d.is_pending({'publishedAt': '2026-09-25', 'benefit': None}))
        self.assertFalse(d.is_pending({'publishedAt': None, 'benefit': {'endAt': '2026-10-01'}}))
        self.assertFalse(d.is_pending({'publishedAt': None, 'benefit': {'ongoing': True}}))


class BuildSourceTests(unittest.TestCase):
    def test_build_source_fields(self):
        source = d.build_source(candidate('https://brand.test/docs/activity/launch'))
        self.assertEqual(source['id'], 'discover-qwenwork')
        self.assertEqual(source['productId'], 'qwenwork')
        self.assertEqual(source['parser'], 'article')
        self.assertEqual(source['region'], 'cn')
        self.assertEqual(source['timezone'], 'Asia/Shanghai')
        self.assertEqual(source['allowedHosts'], ['brand.test'])
        self.assertEqual(source['categories'], [])


class PublishNewTests(unittest.TestCase):
    def _feed(self, root):
        return str(Path(root) / 'feed.json')

    def test_publishes_undated_page_as_pending(self):
        ledger = {}
        pages = {'https://brand.test/docs/benefits/new': UNDATED_ARTICLE}
        with tempfile.TemporaryDirectory() as root:
            feed_path = self._feed(root)
            published, attempted, errors = d.publish_new(
                [candidate('https://brand.test/docs/benefits/new')], ledger, feed_path,
                lambda hosts: FakeClient(pages), d.datetime.fromisoformat(LATER), LATER)
            self.assertEqual(attempted, 1)
            self.assertEqual(errors, [])
            self.assertEqual(len(published), 1)
            item = published[0]
            self.assertTrue(item['pending'])
            self.assertEqual(item['productId'], 'qwenwork')
            self.assertEqual(item['sourceId'], 'discover-qwenwork')
            self.assertEqual(item['summaryMethod'], 'extractive')
            feed = json.loads(Path(feed_path).read_text(encoding='utf-8'))
            self.assertEqual(len(feed['items']), 1)
            self.assertEqual(ledger['urls']['https://brand.test/docs/benefits/new']['status'], 'published')

    def test_publishes_dated_page_not_pending(self):
        ledger = {}
        pages = {'https://brand.test/docs/activity/launch': DATED_ARTICLE}
        with tempfile.TemporaryDirectory() as root:
            feed_path = self._feed(root)
            published, _, _ = d.publish_new(
                [candidate('https://brand.test/docs/activity/launch')], ledger, feed_path,
                lambda hosts: FakeClient(pages), d.datetime.fromisoformat(LATER), LATER)
            self.assertEqual(len(published), 1)
            self.assertFalse(published[0]['pending'])
            self.assertEqual(published[0]['publishedAt'], '2026-09-25T09:00:00+08:00')

    def test_merges_into_existing_feed_without_dropping(self):
        ledger = {}
        pages = {'https://brand.test/docs/activity/launch': DATED_ARTICLE}
        with tempfile.TemporaryDirectory() as root:
            feed_path = self._feed(root)
            existing = {'schemaVersion': 1, 'items': [{'id': 'old1', 'productId': 'qoder', 'title': '旧内容',
                        'summary': ['旧'], 'url': 'https://qoder.com/x', 'sourceId': 's', 'region': 'international',
                        'publishedAt': '2026-09-01T00:00:00+08:00', 'firstSeenAt': '2026-09-01T00:00:00+08:00',
                        'categories': ['other'], 'benefit': None}], 'sources': [], 'lastSuccessfulCollectionAt': NOW}
            Path(feed_path).write_text(json.dumps(existing, ensure_ascii=False), encoding='utf-8')
            d.publish_new([candidate('https://brand.test/docs/activity/launch')], ledger, feed_path,
                          lambda hosts: FakeClient(pages), d.datetime.fromisoformat(LATER), LATER)
            feed = json.loads(Path(feed_path).read_text(encoding='utf-8'))
            ids = {item['id'] for item in feed['items']}
            self.assertIn('old1', ids)
            self.assertEqual(len(feed['items']), 2)
            self.assertEqual(feed['items'][0]['productId'], 'qwenwork')

    def test_fetch_failure_marks_seen_and_continues(self):
        ledger = {}
        pages = {'https://brand.test/docs/activity/launch': DATED_ARTICLE}
        new_items = [candidate('https://brand.test/docs/missing'), candidate('https://brand.test/docs/activity/launch')]
        with tempfile.TemporaryDirectory() as root:
            feed_path = self._feed(root)
            published, attempted, errors = d.publish_new(
                new_items, ledger, feed_path, lambda hosts: FakeClient(pages),
                d.datetime.fromisoformat(LATER), LATER)
            self.assertEqual(attempted, 2)
            self.assertEqual(len(published), 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(ledger['urls']['https://brand.test/docs/missing']['status'], 'fetch-failed:AccessRestricted')

    def test_no_content_marks_seen(self):
        ledger = {}
        pages = {'https://brand.test/docs/empty': '<html><body><div>no article here</div></body></html>'}
        with tempfile.TemporaryDirectory() as root:
            published, _, _ = d.publish_new([candidate('https://brand.test/docs/empty')], ledger, self._feed(root),
                                            lambda hosts: FakeClient(pages), d.datetime.fromisoformat(LATER), LATER)
            self.assertEqual(published, [])
            self.assertEqual(ledger['urls']['https://brand.test/docs/empty']['status'], 'no-content')
            self.assertFalse(Path(self._feed(root)).exists())

    def test_cap_limits_attempts_and_leaves_rest_unseen(self):
        ledger = {}
        pages = {'https://brand.test/docs/activity/launch': DATED_ARTICLE}
        new_items = [candidate('https://brand.test/docs/activity/launch'), candidate('https://brand.test/docs/benefits/new')]
        with tempfile.TemporaryDirectory() as root:
            published, attempted, _ = d.publish_new(new_items, ledger, self._feed(root),
                                                    lambda hosts: FakeClient(pages),
                                                    d.datetime.fromisoformat(LATER), LATER, cap=1)
            self.assertEqual(attempted, 1)
            self.assertEqual(len(published), 1)
            self.assertIn('https://brand.test/docs/activity/launch', ledger['urls'])
            self.assertNotIn('https://brand.test/docs/benefits/new', ledger['urls'])


class RunDiscoveryTests(unittest.TestCase):
    def _write(self, root, name, value):
        path = Path(root) / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_end_to_end_baseline_then_auto_publish(self):
        config = {'sitemaps': [{'brand': 'TestBrand', 'productId': 'qwenwork', 'url': 'https://brand.test/sitemap.xml',
                                'hosts': ['brand.test'], 'region': 'cn', 'timezone': 'Asia/Shanghai',
                                'include': [r'^https://brand\.test/docs/(benefits|activity)(/|$)'],
                                'exclude': ['/docs/developer/']}]}
        sources = {'sources': [{'url': 'https://brand.test/docs/benefits'}]}
        with tempfile.TemporaryDirectory() as root:
            config_path = self._write(root, 'discovery.json', config)
            sources_path = self._write(root, 'sources.json', sources)
            state_path = str(Path(root) / 'discovery-state.json')
            feed_path = str(Path(root) / 'feed.json')

            pages = {'https://brand.test/sitemap.xml': INDEX_XML, 'https://brand.test/sitemap-a.xml': URLSET_XML}
            result = d.run_discovery(config_path, sources_path, state_path, feed_path,
                                     client_factory=lambda hosts: FakeClient(pages),
                                     now=d.datetime.fromisoformat(NOW))
            self.assertTrue(result['first_run'])
            self.assertEqual(result['published'], [])
            self.assertEqual(result['candidates'], 2)
            self.assertFalse(Path(feed_path).exists())
            ledger = json.loads(Path(state_path).read_text(encoding='utf-8'))
            self.assertEqual(ledger['urls']['https://brand.test/docs/benefits/new']['status'], 'baseline')

            pages['https://brand.test/sitemap-a.xml'] = URLSET_XML_RUN2
            pages['https://brand.test/docs/activity/launch'] = DATED_ARTICLE
            result = d.run_discovery(config_path, sources_path, state_path, feed_path,
                                     client_factory=lambda hosts: FakeClient(pages),
                                     now=d.datetime.fromisoformat(LATER))
            self.assertFalse(result['first_run'])
            self.assertEqual(result['new'], 1)
            self.assertEqual(len(result['published']), 1)
            self.assertEqual(result['published'][0]['url'], 'https://brand.test/docs/activity/launch')
            self.assertFalse(result['published'][0]['pending'])
            feed = json.loads(Path(feed_path).read_text(encoding='utf-8'))
            self.assertEqual(len(feed['items']), 1)
            ledger = json.loads(Path(state_path).read_text(encoding='utf-8'))
            self.assertEqual(ledger['urls']['https://brand.test/docs/activity/launch']['status'], 'published')

    def test_sitemap_fetch_failure_is_skipped_not_fatal(self):
        config = {'sitemaps': [{'brand': 'Down', 'url': 'https://down.test/sitemap.xml', 'hosts': ['down.test'],
                                'include': ['.*'], 'exclude': []}]}

        def failing_client(hosts):
            raise d.AccessRestricted('Robots disallow access')

        with tempfile.TemporaryDirectory() as root:
            config_path = self._write(root, 'discovery.json', config)
            sources_path = self._write(root, 'sources.json', {'sources': []})
            result = d.run_discovery(config_path, sources_path, str(Path(root) / 's.json'), str(Path(root) / 'f.json'),
                                     client_factory=failing_client, now=d.datetime.fromisoformat(NOW))
            self.assertEqual(result['published'], [])
            self.assertTrue(any('Down' in entry for entry in result['skipped']))


class SummaryTests(unittest.TestCase):
    def test_summary_variants(self):
        self.assertIn('基线', d.build_summary([], True, 5, 0, []))
        self.assertIn('无新增', d.build_summary([], False, 0, 0, []))
        summary = d.build_summary([{'productId': 'qwenwork', 'title': '福利', 'url': 'https://x/a', 'pending': True}],
                                  False, 1, 1, [])
        self.assertIn('自动发布', summary)
        self.assertIn('待确认', summary)
        self.assertIn('https://x/a', summary)


if __name__ == '__main__':
    unittest.main()
