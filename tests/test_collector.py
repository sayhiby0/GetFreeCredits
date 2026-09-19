import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

SPEC = importlib.util.spec_from_file_location('collector', Path(__file__).parents[1] / 'scripts' / 'collect.py')
c = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(c)
NOW = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
SOURCE = {'id': 'test', 'productId': 'qoder', 'productName': 'Qoder', 'name': '测试来源', 'url': 'https://example.com/log', 'kind': 'website', 'parser': 'changelog', 'enabled': True, 'region': 'international', 'categories': ['updates'], 'timezone': 'Asia/Shanghai'}


class ParsingTests(unittest.TestCase):
    def test_date_precision(self):
        self.assertEqual(c.date_from_text('2026 年 9 月 16 日'), '2026-09-16')
        self.assertIsNone(c.date_from_text('2026年2月30日'))
        self.assertIsNone(c.date_from_text('今天更新'))

    def test_tracking_only_removed(self):
        url = c.canonical_url('https://example.com/a?code=CS1&utm_source=x&xsec_token=temp#release-one')
        self.assertEqual(url, 'https://example.com/a?code=CS1#release-one')
        self.assertNotEqual(c.canonical_url('https://example.com/a#v1'), c.canonical_url('https://example.com/a#v2'))

    def test_unsafe_urls(self):
        for url in ['http://example.com/', 'https://localhost/', 'https://127.0.0.1/', 'https://[::1]/', 'https://10.1.1.1/', 'https://example.com:444/', 'https://user:pass@example.com/', 'file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                c.validate_url(url, resolve=False)
        with self.assertRaises(ValueError):
            c.validate_url('https://evil.example/', ['example.com'], resolve=False)
        self.assertEqual(c.validate_url('https://example.com/', ['example.com'], resolve=False).hostname, 'example.com')

    def test_dns_private_destination(self):
        with patch.object(c.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('192.168.1.2', 443))]):
            with self.assertRaises(ValueError):
                c.validate_url('https://example.com')

    def test_excerpt_strips_markup_navigation(self):
        soup = BeautifulSoup('<article><nav>导航不应该被收录在正文中</nav><script>alert(1)</script><p>新增多人协作功能，支持自动整理项目文件。</p><p>修复任务恢复时重复显示消息的问题。</p></article>', 'html.parser')
        lines = c.html_lines(soup)
        self.assertEqual(len(lines), 2)
        self.assertNotIn('alert', ''.join(lines))
        self.assertNotIn('导航', ''.join(lines))

    def test_english_not_fake_chinese_summary(self):
        self.assertEqual(c.excerpt(['New workspace integration is now available.'], ['updates']), [])

    def test_cross_category(self):
        categories = c.classify('新功能正式上线', ['登录即送30天免费会员，领取条件请查看官方介绍。'], ['updates'])
        self.assertEqual(categories, ['benefits', 'updates'])

    def test_fixing_coupon_ui_is_not_promotion(self):
        result = c.classify('千问办公 1.0.5', ['个人资料新增兑换码入口，模型限免标签样式修复。'], ['updates'], 'changelog')
        self.assertEqual(result, ['updates'])

    def test_tutorial_category(self):
        self.assertEqual(c.classify('如何搭建个人网站', ['这里是完整的使用说明与实际操作教程。'], ['practice']), ['practice'])

    def test_membership_duration_not_deadline(self):
        value = c.extract_benefit(['下载豆包工作，任意账号登录即送30天豆包订阅权益。'], SOURCE)
        self.assertIsNone(value['startAt'])
        self.assertIsNone(value['endAt'])
        self.assertFalse(value['ongoing'])
        self.assertFalse(c.is_active(value, NOW))

    def test_explicit_benefit_range(self):
        value = c.extract_benefit(['活动时间：2026 年 9 月 1 日 10:00 至 2026 年 9 月 30 日 24:00（SGT）。'], SOURCE)
        self.assertEqual(value['startAt'], '2026-09-01T10:00:00+08:00')
        self.assertTrue(value['endAt'].startswith('2026-09-30T23:59:59'))
        self.assertTrue(c.is_active(value, NOW))
        self.assertFalse(c.is_active(value, NOW + timedelta(days=20)))

    def test_no_year_or_timezone_not_guessed(self):
        unknown_source = {**SOURCE, 'timezone': None}
        value = c.extract_benefit(['活动时间：2026年9月1日至2026年9月30日。'], unknown_source)
        self.assertIsNone(value['endAt'])
        value = c.extract_benefit(['活动时间：9月1日至9月30日。'], SOURCE)
        self.assertIsNone(value['endAt'])

    def test_partial_range_inherits_only_explicit_year(self):
        value = c.extract_benefit(['活动时间：2026年9月15日12:00 至 9月20日12:00。'], SOURCE)
        self.assertEqual(value['endAt'], '2026-09-20T12:00:00+08:00')
        self.assertTrue(c.is_active(value, NOW))
        ambiguous = c.extract_benefit(['活动时间：2026年12月30日至1月5日。'], SOURCE)
        self.assertIsNone(ambiguous['endAt'])

    def test_single_deadline_not_overwritten_by_disclaimer(self):
        value = c.extract_benefit(['结束时间：2026年9月3日23:59（UTC+8），领取在此时截止。',
                                 '活动期限：本次限时优惠没有明确截止时间。'], SOURCE)
        self.assertEqual(value['endAt'], '2026-09-03T23:59:00+08:00')
        self.assertIn('领取在此时截止', value['deadlineText'])
        self.assertFalse(c.is_active(value, NOW))
        self.assertEqual(value['endPrecision'], 'minute')

    def test_explicit_timezone_overrides_source_default(self):
        value = c.extract_benefit(['截止时间：2026年9月18日12:00 UTC-04:00。'], SOURCE)
        self.assertEqual(value['endAt'], '2026-09-18T12:00:00-04:00')

    def test_end_only_date_precision_and_start_only(self):
        end = c.extract_benefit(['截止日期：2026年9月18日。'], SOURCE)
        self.assertTrue(end['endAt'].startswith('2026-09-18T23:59:59'))
        self.assertEqual(end['endPrecision'], 'date')
        start = c.extract_benefit(['开始时间：2026年9月3日14:00。'], SOURCE)
        self.assertIsNotNone(start['startAt'])
        self.assertFalse(c.is_active(start, NOW))

    def test_expired_or_inverted_overrides_ongoing(self):
        for value in [{'endAt': '2026-09-01', 'ongoing': True},
                      {'startAt': '2026-10-01', 'endAt': '2026-09-20', 'ongoing': True}]:
            self.assertFalse(c.is_active(value, NOW))
        value = c.extract_benefit(['活动并非长期有效，可能随时结束。'], SOURCE)
        self.assertFalse(value['ongoing'])

    def test_real_offer_in_changelog_and_all_offer_types(self):
        for title in ['会员订阅八折', '邀请好友奖励积分', '联名福利抽奖', '新用户免费试用']:
            self.assertIn('benefits', c.classify(title, [], []))
        result = c.classify('1.2版本', ['本周登录即送30天会员，请在客户端内领取。'], ['updates'], 'changelog')
        self.assertEqual(result, ['benefits', 'updates'])

    def test_mintlify_changelog(self):
        soup = BeautifulSoup('<article><div class="update-container" id="2026-09-16"><div data-component-part="update-label">2026年09月16日</div><div data-component-part="update-description">千问办公 1.0.6</div><div data-component-part="update-content"><li>新增项目空态引导与任务批量归类。</li></div></div></article>', 'html.parser')
        result = c.parse_changelog(soup, SOURCE, SOURCE['url'])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['publishedAt'], '2026-09-16')
        self.assertTrue(result[0]['url'].endswith('#2026-09-16'))

    def test_vitepress_changelog_is_split(self):
        soup = BeautifulSoup('<main><h2 id="v2">2.0 版本发布（2026-09-16）</h2><ul><li>新增多人协作功能，支持项目文件管理。</li></ul><h2 id="v1">1.0 版本发布（2026-09-01）</h2><p>首次发布全新的项目协作与文档功能。</p></main>', 'html.parser')
        result = c.parse_changelog(soup, SOURCE, SOURCE['url'])
        self.assertEqual(len(result), 2)
        self.assertNotIn('首次发布', ''.join(result[0]['lines']))
        self.assertEqual(result[0]['title'], 'Qoder 2.0 版本发布')

    def test_hera_data_is_parsed_not_executed(self):
        data = {'articleTitle': '豆包工作正式上线', 'richtext': {'text': '支持完成文档、表格、PPT与应用开发等工作。\n可以指定工作目标和交付要求，执行结果仍需要核对。'}, '_createTime': 1780000000}
        soup = BeautifulSoup('<script>window._templateValue = ' + json.dumps(data) + '; throw Error("never execute")</script>', 'html.parser')
        result = c.parse_hera(soup, SOURCE, SOURCE['url'])
        self.assertEqual(result[0]['title'], '豆包工作正式上线')
        self.assertIsNone(result[0]['publishedAt'])


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_guard_and_persistence(self):
        budget = c.Budget(self.path, NOW)
        self.assertTrue(budget.reserve(24.9))
        self.assertFalse(budget.reserve(0.2))
        self.assertEqual(c.Budget(self.path, NOW).spent, 24.9)
        self.assertFalse(budget.reserve(float('nan')))
        self.assertFalse(budget.reserve(-1))

    def test_new_month_retains_history(self):
        budget = c.Budget(self.path, NOW)
        budget.reserve(2)
        new = c.Budget(self.path, NOW + timedelta(days=40))
        self.assertEqual(new.spent, 0)
        self.assertEqual(new.state['months']['2026-09']['spentCny'], 2)

    def test_no_paid_api_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            summarizer = c.Summarizer(c.Budget(self.path, NOW))
            self.assertIsNone(summarizer.summarize('新闻标题', ['这里是一条真实的官方中文消息内容。']))
            self.assertEqual(summarizer.budget.spent, 0)

    def test_qwen_non_thinking_json_request(self):
        env = {'AI_ENABLED': 'true', 'AI_API_KEY': 'test-only-not-a-secret', 'AI_MODEL': 'qwen3.7-flash', 'AI_BASE_URL': 'https://example.com/v1', 'AI_INPUT_CNY_PER_MILLION': '0.2', 'AI_OUTPUT_CNY_PER_MILLION': '0.8'}
        summary = ['新增文档管理能力，支持项目协作。']
        envelope = {'choices': [{'message': {'content': json.dumps({'summary': summary})}}]}
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'validate_url'), patch.object(c, 'build_opener') as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(envelope).encode()
            summarizer = c.Summarizer(c.Budget(self.path, NOW))
            self.assertEqual(summarizer.summarize('更新', summary), summary)
            request = opener.return_value.open.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload['model'], 'qwen3.7-flash')
            self.assertIs(payload['enable_thinking'], False)
            self.assertEqual(payload['max_tokens'], 800)
            self.assertEqual(payload['response_format'], {'type': 'json_object'})
            self.assertNotIn(env['AI_API_KEY'], request.data.decode())
            expected = (len(request.data) * 0.2 + 800 * 0.8) / 1_000_000
            self.assertAlmostEqual(c.Budget(self.path, NOW).spent, expected, places=8)

    def test_qwen_budget_guard_prevents_request(self):
        env = {'AI_ENABLED': 'true', 'AI_API_KEY': 'test-only-not-a-secret', 'AI_MODEL': 'qwen3.7-flash', 'AI_BASE_URL': 'https://example.com/v1', 'AI_INPUT_CNY_PER_MILLION': '0.2', 'AI_OUTPUT_CNY_PER_MILLION': '0.8'}
        budget = c.Budget(self.path, NOW)
        budget.reserve(25)
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'validate_url'), patch.object(c, 'build_opener') as opener:
            self.assertIsNone(c.Summarizer(budget).summarize('更新', ['新增文档管理能力，支持项目协作。']))
            opener.assert_not_called()
            self.assertEqual(c.Budget(self.path, NOW).spent, 25)

    def test_failed_call_keeps_reservation(self):
        env = {'AI_ENABLED': 'true', 'AI_API_KEY': 'test-only-not-a-secret', 'AI_MODEL': 'test-model', 'AI_BASE_URL': 'https://example.com/v1', 'AI_INPUT_CNY_PER_MILLION': '1', 'AI_OUTPUT_CNY_PER_MILLION': '1'}
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'validate_url'), patch.object(c, 'build_opener') as opener:
            opener.return_value.open.side_effect = TimeoutError()
            summarizer = c.Summarizer(c.Budget(self.path, NOW))
            self.assertIsNone(summarizer.summarize('更新', ['新增文件管理能力，支持跨项目协作。']))
            self.assertGreater(c.Budget(self.path, NOW).spent, 0)

    def test_http_failure_logs_only_status(self):
        env = {'AI_ENABLED': 'true', 'AI_API_KEY': 'test-only-not-a-secret', 'AI_MODEL': 'qwen3.7-flash', 'AI_BASE_URL': 'https://example.com/v1', 'AI_INPUT_CNY_PER_MILLION': '0.2', 'AI_OUTPUT_CNY_PER_MILLION': '0.8'}
        with patch.dict(os.environ, env, clear=True), patch.object(c, 'validate_url'), patch.object(c, 'build_opener') as opener, patch('builtins.print') as output:
            opener.return_value.open.side_effect = c.HTTPError('https://example.com/v1/chat/completions', 401, env['AI_API_KEY'], {}, None)
            summarizer = c.Summarizer(c.Budget(self.path, NOW))
            self.assertIsNone(summarizer.summarize('更新', ['新增文档管理能力，支持项目协作。']))
            output.assert_called_once_with('AI request failed: HTTP 401; using source excerpts.')
            self.assertGreater(c.Budget(self.path, NOW).spent, 0)

    def test_ai_check_disabled_does_not_write_or_collect(self):
        feed = self.path.parent / 'feed.json'
        result = subprocess.run([sys.executable, str(Path(c.__file__)), '--check-ai', '--state', str(self.path), '--output', str(feed)],
                                env={**os.environ, 'AI_ENABLED': 'false'}, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn('AI verification requires', result.stderr)
        self.assertFalse(self.path.exists())
        self.assertFalse(feed.exists())


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'sources.json'
        self.output = self.root / 'feed.json'
        self.state = self.root / 'state.json'
        self.no_api = patch.dict(os.environ, {'AI_ENABLED': 'false'})
        self.no_api.start()

    def tearDown(self):
        self.no_api.stop()
        self.temp.cleanup()

    def run_collection(self, candidates, sources=None, now=NOW):
        c.atomic_json(self.config, {'sources': sources or [SOURCE]})
        with patch.object(c, 'discover', return_value=(candidates, False)):
            return c.collect(self.config, self.output, self.state, now=now)

    def candidate(self, **changes):
        return {'title': 'Qoder 版本更新', 'url': 'https://example.com/log#v1', 'publishedAt': '2026-09-16', 'lines': ['新增文档管理与项目分类能力，支持更高效地组织任务。', '修复网络恢复后部分消息无法正确展示的问题。'], **changes}

    def test_repeat_is_idempotent(self):
        first = self.run_collection([self.candidate()])
        second = self.run_collection([self.candidate()], now=NOW + timedelta(hours=1))
        self.assertEqual(len(second['items']), 1)
        self.assertEqual(second['run']['newCount'], 0)
        self.assertEqual(first['items'][0]['updatedAt'], second['items'][0]['updatedAt'])
        self.assertEqual(first['items'][0]['firstSeenAt'], second['items'][0]['firstSeenAt'])

    def test_update_retains_first_seen(self):
        first = self.run_collection([self.candidate()])
        second = self.run_collection([self.candidate(lines=['新增任务分析能力，支持更完整的文档处理与协作。'])], now=NOW + timedelta(hours=1))
        self.assertEqual(second['run']['updatedCount'], 1)
        self.assertEqual(first['items'][0]['firstSeenAt'], second['items'][0]['firstSeenAt'])

    def test_old_news_excluded_new_discovery_not_faked(self):
        result = self.run_collection([self.candidate(publishedAt='2026-01-01'), self.candidate(publishedAt=None, url='https://example.com/guide')])
        self.assertEqual(len(result['items']), 1)
        self.assertIsNone(result['items'][0]['publishedAt'])
        self.assertEqual(result['items'][0]['firstSeenAt'], NOW.isoformat(timespec='seconds'))

    def test_old_active_benefit_included(self):
        candidate = self.candidate(title='限时加赠积分', publishedAt='2026-01-01', lines=['活动时间：2026年1月1日至2026年12月31日。', '个人订阅用户续费成功后可以领取积分，详细规则请阅读原文。'])
        result = self.run_collection([candidate])
        self.assertEqual(len(result['items']), 1)
        self.assertIn('benefits', result['items'][0]['categories'])

    def test_failed_source_preserves_old_data_and_timestamp(self):
        first = self.run_collection([self.candidate()])
        with patch.object(c, 'discover', side_effect=TimeoutError()):
            second = c.collect(self.config, self.output, self.state, now=NOW + timedelta(days=1))
        self.assertEqual(first['items'], second['items'])
        self.assertEqual(first['lastSuccessfulCollectionAt'], second['lastSuccessfulCollectionAt'])
        self.assertEqual(second['sources'][0]['status'], 'error')

    def test_one_failure_does_not_block_other(self):
        c.atomic_json(self.config, {'sources': [SOURCE, {**SOURCE, 'id': 'second'}]})
        with patch.object(c, 'discover', side_effect=[TimeoutError(), ([self.candidate()], False)]):
            feed = c.collect(self.config, self.output, self.state, now=NOW)
        self.assertEqual([s['status'] for s in feed['sources']], ['error', 'ok'])
        self.assertEqual(len(feed['items']), 1)

    def test_short_complete_update_is_published(self):
        result = self.run_collection([self.candidate(lines=['新增任务分析能力，支持更完整的文档处理与协作。'])])
        self.assertEqual(len(result['items']), 1)

    def test_invalid_candidate_does_not_block_following_article(self):
        result = self.run_collection([self.candidate(url='https://127.0.0.1/private'), self.candidate()])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['sources'][0]['status'], 'partial')

    def test_failed_attempts_are_bounded_and_resume(self):
        candidates = [self.candidate(url='https://example.com/log#english', lines=['An English announcement without a usable Chinese summary.']),
                      self.candidate(url='https://example.com/log#valid')]
        c.atomic_json(self.config, {'sources': [SOURCE]})
        with patch.object(c, 'discover', return_value=(candidates, False)):
            first = c.collect(self.config, self.output, self.state, max_items=1, now=NOW)
            second = c.collect(self.config, self.output, self.state, max_items=1, now=NOW + timedelta(hours=1))
        self.assertEqual(len(first['items']), 0)
        self.assertEqual(len(second['items']), 1)
        self.assertTrue(second['items'][0]['url'].endswith('#valid'))

    def test_batches_rotate_between_sources(self):
        other = {**SOURCE, 'id': 'other'}
        c.atomic_json(self.config, {'sources': [SOURCE, other]})
        def discover(source, client):
            english = self.candidate(lines=['English-only news cannot become a Chinese excerpt automatically.'])
            return ([english if source['id'] == 'test' else self.candidate(url='https://example.com/log#other')], False)
        with patch.object(c, 'discover', side_effect=discover):
            c.collect(self.config, self.output, self.state, max_items=1, now=NOW)
            result = c.collect(self.config, self.output, self.state, max_items=1, now=NOW + timedelta(hours=1))
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['sourceId'], 'other')
        self.assertEqual([s['id'] for s in result['sources']], ['test', 'other'])

    def test_known_content_does_not_call_summarizer(self):
        self.run_collection([self.candidate()])
        with patch.object(c.Summarizer, 'summarize') as summarize:
            self.run_collection([self.candidate()], now=NOW + timedelta(hours=1))
            summarize.assert_not_called()

    def test_disabled_sources_never_requested(self):
        source = {**SOURCE, 'enabled': False, 'kind': 'xiaohongshu', 'disabledReason': '需要登录，跳过'}
        c.atomic_json(self.config, {'sources': [source]})
        with patch.object(c, 'discover') as fetch:
            result = c.collect(self.config, self.output, self.state, now=NOW)
            fetch.assert_not_called()
        self.assertEqual(result['sources'][0]['status'], 'restricted')
        self.assertIsNone(result['lastSuccessfulCollectionAt'])


if __name__ == '__main__':
    unittest.main()
