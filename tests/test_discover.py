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


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def get(self, url):
        self.fetched.append(url)
        return BeautifulSoup(self.pages[url], 'html.parser')


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


class EvaluateTests(unittest.TestCase):
    def test_baseline_is_silent_then_delta_reports(self):
        candidates = [{'brand': 'B', 'productId': 'p', 'url': 'https://x/a'},
                      {'brand': 'B', 'productId': 'p', 'url': 'https://x/b'}]
        ledger = {}
        new, ledger, first = d.evaluate(candidates, {'https://x/a'}, ledger, NOW)
        self.assertTrue(first)
        self.assertEqual(new, [])
        self.assertIn('https://x/b', ledger['urls'])
        self.assertNotIn('https://x/a', ledger['urls'])

        later = candidates + [{'brand': 'B', 'productId': 'p', 'url': 'https://x/c'}]
        new, ledger, first = d.evaluate(later, {'https://x/a'}, ledger, LATER)
        self.assertFalse(first)
        self.assertEqual([item['url'] for item in new], ['https://x/c'])

    def test_seen_url_not_reported_twice(self):
        ledger = {}
        cand = [{'brand': 'B', 'productId': 'p', 'url': 'https://x/b'}]
        d.evaluate(cand, set(), ledger, NOW)
        new, ledger, _ = d.evaluate(cand, set(), ledger, LATER)
        self.assertEqual(new, [])


class ReportTests(unittest.TestCase):
    def test_report_variants(self):
        self.assertIn('基线', d.build_report([], NOW, True, 5))
        self.assertIn('无新增', d.build_report([], NOW, False, 0))
        report = d.build_report([{'brand': 'Qoder', 'productId': 'qoder', 'url': 'https://q/x'}], NOW, False, 1)
        self.assertIn('候选新页面', report)
        self.assertIn('https://q/x', report)
        self.assertIn('Qoder', report)


class RunDiscoveryTests(unittest.TestCase):
    def _write(self, root, name, value):
        path = Path(root) / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def test_end_to_end_baseline_then_new(self):
        config = {'sitemaps': [{'brand': 'TestBrand', 'productId': 'p', 'url': 'https://brand.test/sitemap.xml',
                                'hosts': ['brand.test'],
                                'include': [r'^https://brand\.test/docs/(benefits|activity)(/|$)'],
                                'exclude': ['/docs/developer/']}]}
        sources = {'sources': [{'url': 'https://brand.test/docs/benefits'}]}
        with tempfile.TemporaryDirectory() as root:
            config_path = self._write(root, 'discovery.json', config)
            sources_path = self._write(root, 'sources.json', sources)
            state_path = str(Path(root) / 'discovery-state.json')
            report_path = str(Path(root) / 'report.md')

            pages = {'https://brand.test/sitemap.xml': INDEX_XML, 'https://brand.test/sitemap-a.xml': URLSET_XML}
            result = d.run_discovery(config_path, sources_path, state_path, report_path,
                                     client_factory=lambda hosts: FakeClient(pages),
                                     now=d.datetime.fromisoformat(NOW))
            self.assertTrue(result['first_run'])
            self.assertEqual(result['new'], [])
            self.assertEqual(result['candidates'], 2)
            ledger = json.loads(Path(state_path).read_text(encoding='utf-8'))
            self.assertIn('https://brand.test/docs/benefits/new', ledger['urls'])
            self.assertNotIn('https://brand.test/docs/benefits', ledger['urls'])
            self.assertIn('基线', Path(report_path).read_text(encoding='utf-8'))

            pages['https://brand.test/sitemap-a.xml'] = URLSET_XML_RUN2
            result = d.run_discovery(config_path, sources_path, state_path, report_path,
                                     client_factory=lambda hosts: FakeClient(pages),
                                     now=d.datetime.fromisoformat(LATER))
            self.assertFalse(result['first_run'])
            self.assertEqual([item['url'] for item in result['new']], ['https://brand.test/docs/activity/launch'])
            self.assertIn('候选新页面', Path(report_path).read_text(encoding='utf-8'))

    def test_fetch_failure_is_skipped_not_fatal(self):
        config = {'sitemaps': [{'brand': 'Down', 'url': 'https://down.test/sitemap.xml', 'hosts': ['down.test'],
                                'include': ['.*'], 'exclude': []}]}

        def failing_client(hosts):
            raise d.AccessRestricted('Robots disallow access')

        with tempfile.TemporaryDirectory() as root:
            config_path = self._write(root, 'discovery.json', config)
            sources_path = self._write(root, 'sources.json', {'sources': []})
            result = d.run_discovery(config_path, sources_path, str(Path(root) / 's.json'), str(Path(root) / 'r.md'),
                                     client_factory=failing_client, now=d.datetime.fromisoformat(NOW))
            self.assertEqual(result['new'], [])
            self.assertTrue(any('Down' in entry for entry in result['skipped']))


if __name__ == '__main__':
    unittest.main()
