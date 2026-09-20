"""Weekly auto-publish discovery of new official pages via brand sitemaps.

Fetches each configured sitemap (following sitemap indexes), filters the listed
URLs by include/exclude patterns, and for every URL that is neither already
tracked in config/sources.json nor seen on a previous run, fetches the page,
classifies it, builds a feed item (reusing collect.parse_article/prepare_item
with extractive summaries so nothing costs money) and merges it straight into
site/data/feed.json. There is no human-review step: items go live automatically.
When a page carries no publish date and no benefit date, the item is flagged
``pending`` so the site can show a "待确认" label. Access is anonymous and
robots-respecting (reuses collect.PublicClient).
"""
import argparse
import importlib.util
import os
import re
import warnings
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

_spec = importlib.util.spec_from_file_location('collect', Path(__file__).resolve().parent / 'collect.py')
collect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect)

from bs4 import BeautifulSoup

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings('ignore', category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

PublicClient = collect.PublicClient
AccessRestricted = collect.AccessRestricted
canonical_url = collect.canonical_url
read_json = collect.read_json
atomic_json = collect.atomic_json
parse_time = collect.parse_time

MAX_CHILD_SITEMAPS = 30
MAX_DEPTH = 3
MAX_LOCS = 5000
MAX_NEW_PER_RUN = 25
FETCH_ERRORS = (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, AccessRestricted, OSError)


class ExtractiveSummarizer:
    """Discovery never pays for AI summaries; excerpts keep the budget intact."""
    enabled = False
    used = False
    attempts = 0

    def summarize(self, title, lines):
        return None


def locs_from_soup(soup):
    return [tag.get_text(strip=True) for tag in soup.find_all('loc') if tag.get_text(strip=True)]


def soup_is_index(soup):
    return soup.find('sitemapindex') is not None or soup.find('sitemap') is not None


def parse_sitemap(text):
    soup = BeautifulSoup(text, 'html.parser')
    return ('index' if soup_is_index(soup) else 'urlset'), locs_from_soup(soup)


def fetch_sitemap_urls(client, url, depth=0, visited=None):
    visited = set() if visited is None else visited
    if depth > MAX_DEPTH or url in visited or len(visited) >= MAX_CHILD_SITEMAPS:
        return []
    visited.add(url)
    soup = client.get(url)
    locs = locs_from_soup(soup)
    if not soup_is_index(soup):
        return locs[:MAX_LOCS]
    pages = []
    for child in locs:
        pages.extend(fetch_sitemap_urls(client, child, depth + 1, visited))
        if len(pages) >= MAX_LOCS:
            break
    return pages[:MAX_LOCS]


def url_matches(url, include, exclude):
    if include and not any(re.search(pattern, url, re.IGNORECASE) for pattern in include):
        return False
    return not any(re.search(pattern, url, re.IGNORECASE) for pattern in exclude)


def evaluate(candidates, tracked, ledger, now_iso):
    """Diff candidates against tracked sources and the seen ledger.

    On the first run (no baseline) every candidate is recorded silently so later
    runs only surface genuinely new URLs; nothing is published on the baseline
    run. On later runs, new candidates are returned without being marked seen —
    the caller marks each one seen only after it attempts to publish it, so URLs
    beyond the per-run cap are picked up on a following run.
    Returns (new_items, ledger, first_run).
    """
    urls = ledger.setdefault('urls', {})
    first_run = not ledger.get('baselineAt')
    if first_run:
        for candidate in candidates:
            urls[candidate['url']] = {'brand': candidate['brand'], 'seenAt': now_iso, 'status': 'baseline'}
        ledger['baselineAt'] = now_iso
        ledger['lastRunAt'] = now_iso
        return [], ledger, True
    new_items = [c for c in candidates if c['url'] not in tracked and c['url'] not in urls]
    ledger['lastRunAt'] = now_iso
    return new_items, ledger, False


def mark_seen(ledger, candidate, now_iso, status):
    ledger.setdefault('urls', {})[candidate['url']] = {'brand': candidate['brand'], 'seenAt': now_iso, 'status': status}


def build_source(candidate):
    product = candidate.get('productId') or 'unknown'
    return {
        'id': f'discover-{product}',
        'productId': product,
        'name': candidate.get('brand') or product,
        'url': candidate['url'],
        'parser': 'article',
        'contentSelector': candidate.get('contentSelector', 'article,main'),
        'titleSelector': candidate.get('titleSelector', 'h1'),
        'region': candidate.get('region', 'unknown'),
        'timezone': candidate.get('timezone'),
        'categories': [],
        'allowedHosts': candidate.get('hosts') or [urlsplit(candidate['url']).hostname],
    }


def is_pending(item):
    """Flag items we cannot date: no publish date and no benefit start/end/ongoing."""
    if item.get('publishedAt'):
        return False
    benefit = item.get('benefit')
    if benefit and (benefit.get('startAt') or benefit.get('endAt') or benefit.get('ongoing')):
        return False
    return True


def publish_new(new_items, ledger, feed_path, client_factory, now, now_iso, cap=MAX_NEW_PER_RUN):
    """Fetch, classify and merge up to ``cap`` new pages into the feed. No review."""
    summarizer = ExtractiveSummarizer()
    feed = read_json(feed_path, {'schemaVersion': 1, 'items': [], 'sources': [], 'lastSuccessfulCollectionAt': None})
    items = {item['id']: item for item in feed.get('items', [])}
    published, attempted, errors = [], 0, []
    for candidate in new_items:
        if attempted >= cap:
            break
        attempted += 1
        source = build_source(candidate)
        try:
            articles = collect.parse_article(client_factory(source['allowedHosts']).get(candidate['url']), source, candidate['url'])
        except FETCH_ERRORS as error:
            mark_seen(ledger, candidate, now_iso, f'fetch-failed:{type(error).__name__}')
            errors.append(f"{candidate['url']}: {type(error).__name__}")
            continue
        if not articles:
            mark_seen(ledger, candidate, now_iso, 'no-content')
            continue
        added = 0
        for article in articles:
            item = collect.prepare_item(article, source, None, now, summarizer)
            if not item:
                continue
            item['pending'] = is_pending(item)
            items[item['id']] = item
            published.append(item)
            added += 1
        mark_seen(ledger, candidate, now_iso, 'published' if added else 'rejected')
    if published:
        feed['items'] = sorted(items.values(),
                               key=lambda it: parse_time(it.get('publishedAt')) or parse_time(it['firstSeenAt']),
                               reverse=True)
        atomic_json(feed_path, feed)
    return published, attempted, errors


def build_summary(published, first_run, candidate_count, attempted, errors):
    date = datetime.now(timezone.utc).date().isoformat()
    if first_run:
        return (f'## 发现基线已建立（{date}）\n\n'
                f'首次运行，已记录 {candidate_count} 个官方页面作为基线，本次不发布内容。'
                f'之后每周会自动把相对基线新增的页面分类并发布到网站。\n')
    if not published:
        note = f'，{len(errors)} 个页面读取失败已跳过' if errors else ''
        return (f'## 本周无新增可发布页面（{date}）\n\n'
                f'各品牌官方 sitemap 相对上次未发现可发布的新页面（尝试 {attempted} 个{note}）。\n')
    pending = sum(1 for item in published if item.get('pending'))
    lines = [f'## 已自动发布 {len(published)} 条新内容（{date}）', '',
             '> 由 GitHub Actions 每周检索各品牌官方 sitemap 得到，已自动分类并合并进网站，无需人工审核。',
             f'> 其中 {pending} 条缺少明确日期，已在网站标注「待确认」。', '']
    for item in published:
        flag = '（待确认）' if item.get('pending') else ''
        lines.append(f"- {item['productId']} · {item['title']}{flag} — {item['url']}")
    return '\n'.join(lines) + '\n'


def write_github_outputs(published_count, first_run, summary):
    output_path = os.environ.get('GITHUB_OUTPUT')
    if output_path:
        with open(output_path, 'a', encoding='utf-8') as handle:
            handle.write(f'published_count={published_count}\n')
            handle.write(f'changed={"true" if published_count else "false"}\n')
            handle.write(f'has_new={"true" if published_count else "false"}\n')
            handle.write(f'first_run={"true" if first_run else "false"}\n')
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        with open(summary_path, 'a', encoding='utf-8') as handle:
            handle.write(summary + '\n')


def run_discovery(config_path='config/discovery.json', sources_path='config/sources.json',
                  state_path='.state/discovery.json', feed_path='site/data/feed.json',
                  client_factory=PublicClient, now=None):
    now = now or datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_iso = now.isoformat(timespec='seconds')

    sitemaps = read_json(config_path, {}).get('sitemaps', [])
    sources = read_json(sources_path, {}).get('sources', [])
    tracked = {canonical_url(source['url']) for source in sources if source.get('url')}
    ledger = read_json(state_path, {})

    candidates, seen_local, skipped = [], set(), []
    for entry in sitemaps:
        url = entry.get('url')
        if not url:
            continue
        brand = entry.get('brand') or urlsplit(url).hostname
        hosts = entry.get('hosts') or [urlsplit(url).hostname]
        include, exclude = entry.get('include') or [], entry.get('exclude') or []
        try:
            locs = fetch_sitemap_urls(client_factory(hosts), url)
        except FETCH_ERRORS as error:
            skipped.append(f'{brand}: {type(error).__name__}')
            continue
        matched = 0
        for raw in locs:
            try:
                candidate_url = canonical_url(raw)
            except ValueError:
                continue
            if candidate_url in seen_local or not url_matches(candidate_url, include, exclude):
                continue
            seen_local.add(candidate_url)
            candidates.append({'brand': brand, 'productId': entry.get('productId'), 'url': candidate_url,
                               'region': entry.get('region', 'unknown'), 'timezone': entry.get('timezone'),
                               'hosts': hosts, 'contentSelector': entry.get('contentSelector', 'article,main'),
                               'titleSelector': entry.get('titleSelector', 'h1')})
            matched += 1
        print(f'{brand} {url}: {len(locs)} listed, {matched} matched filters')

    new_items, ledger, first_run = evaluate(candidates, tracked, ledger, now_iso)
    published, attempted, errors = publish_new(new_items, ledger, feed_path, client_factory, now, now_iso)
    atomic_json(state_path, ledger)
    summary = build_summary(published, first_run, len(candidates), attempted, errors)
    write_github_outputs(len(published), first_run, summary)
    return {'published': published, 'attempted': attempted, 'errors': errors, 'first_run': first_run,
            'candidates': len(candidates), 'new': len(new_items), 'skipped': skipped}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Auto-classify and publish new official pages found in brand sitemaps.')
    parser.add_argument('--config', default='config/discovery.json')
    parser.add_argument('--sources', default='config/sources.json')
    parser.add_argument('--state', default='.state/discovery.json')
    parser.add_argument('--feed', default='site/data/feed.json')
    args = parser.parse_args()
    result = run_discovery(args.config, args.sources, args.state, args.feed)
    if result['first_run']:
        status = 'baseline established'
    else:
        status = f"published {len(result['published'])} item(s) from {result['attempted']} new page(s)"
    print(f'Discovery: {status}; {result["candidates"]} candidate(s) considered; '
          f'errors={result["errors"] or "none"}; skipped={result["skipped"] or "none"}')
