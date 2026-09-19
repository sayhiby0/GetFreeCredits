"""Deterministic discovery of new official pages via brand sitemaps.

Fetches each configured sitemap (following sitemap indexes), filters the listed
URLs by include/exclude patterns, and reports URLs that are neither already
tracked in config/sources.json nor seen on a previous run. It only lists
candidates; it never judges whether a page is worth adding and never fetches
candidate pages. Access is anonymous and robots-respecting (reuses collect.PublicClient).
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

MAX_CHILD_SITEMAPS = 30
MAX_DEPTH = 3
MAX_LOCS = 5000
REPORT_CAP = 80
FETCH_ERRORS = (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, AccessRestricted, OSError)


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
    runs only surface genuinely new URLs. Returns (new_items, ledger, first_run).
    """
    urls = ledger.setdefault('urls', {})
    first_run = not ledger.get('baselineAt')
    new_items = []
    for candidate in candidates:
        key = candidate['url']
        if key in tracked or key in urls:
            continue
        if not first_run:
            new_items.append(candidate)
        urls[key] = {'brand': candidate['brand'], 'seenAt': now_iso}
    if first_run:
        ledger['baselineAt'] = now_iso
    ledger['lastRunAt'] = now_iso
    return new_items, ledger, first_run


def build_report(new_items, now_iso, first_run, candidate_count):
    date = now_iso[:10]
    if first_run:
        return (f'## 发现基线已建立（{date}）\n\n'
                f'首次运行，已记录 {candidate_count} 个候选页面作为基线，本次不产生待审阅项。'
                f'之后每周只会报告相对基线新增的页面。\n')
    if not new_items:
        return f'## 本周无新增候选页面（{date}）\n\n各品牌官方 sitemap 相对上次未发现新页面。\n'
    by_brand = {}
    for item in new_items:
        by_brand.setdefault(item['brand'], []).append(item['url'])
    lines = [f'## 发现 {len(new_items)} 个候选新页面（{date}）',
             '',
             '> 由 GitHub Actions 每周检索各品牌官方 sitemap 自动得到，仅为线索，未判断是否值得接入。',
             '> 人工核对后，若页面匿名可访问、robots 允许、服务端渲染且有日期正文，再手动加入 `config/sources.json`。']
    for brand in sorted(by_brand):
        urls = by_brand[brand]
        lines.append(f'\n### {brand}（{len(urls)}）')
        lines.extend(f'- {url}' for url in urls[:REPORT_CAP])
        if len(urls) > REPORT_CAP:
            lines.append(f'- …另有 {len(urls) - REPORT_CAP} 个，详见 `.state/discovery.json`')
    return '\n'.join(lines) + '\n'


def write_github_outputs(count, first_run, report):
    output_path = os.environ.get('GITHUB_OUTPUT')
    if output_path:
        with open(output_path, 'a', encoding='utf-8') as handle:
            handle.write(f'has_new={"true" if count else "false"}\n')
            handle.write(f'new_count={count}\n')
            handle.write(f'first_run={"true" if first_run else "false"}\n')
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        with open(summary_path, 'a', encoding='utf-8') as handle:
            handle.write(report + '\n')


def run_discovery(config_path='config/discovery.json', sources_path='config/sources.json',
                  state_path='.state/discovery.json', report_path='.state/discovery-report.md',
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
            candidates.append({'brand': brand, 'productId': entry.get('productId'), 'url': candidate_url})
            matched += 1
        print(f'{brand} {url}: {len(locs)} listed, {matched} matched filters')

    new_items, ledger, first_run = evaluate(candidates, tracked, ledger, now_iso)
    atomic_json(state_path, ledger)
    report = build_report(new_items, now_iso, first_run, len(candidates))
    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(report, encoding='utf-8')
    write_github_outputs(len(new_items), first_run, report)
    return {'new': new_items, 'first_run': first_run, 'candidates': len(candidates), 'skipped': skipped}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='List new official pages from brand sitemaps without scraping or judging them.')
    parser.add_argument('--config', default='config/discovery.json')
    parser.add_argument('--sources', default='config/sources.json')
    parser.add_argument('--state', default='.state/discovery.json')
    parser.add_argument('--report', default='.state/discovery-report.md')
    args = parser.parse_args()
    result = run_discovery(args.config, args.sources, args.state, args.report)
    status = 'baseline established' if result['first_run'] else f"{len(result['new'])} new candidate page(s)"
    print(f'Discovery: {status}; {result["candidates"]} candidate(s) considered; skipped={result["skipped"] or "none"}')
