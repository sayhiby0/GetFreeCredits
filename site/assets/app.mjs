import { REGION_NAMES, BENEFIT_NAMES, SOURCE_NAMES, safeUrl, timestamp, benefitStatus, filterItems, activeBenefitCount, validateFeed } from './logic.mjs';

const $ = id => document.getElementById(id);
const state = { catalog: null, feed: null, category: 'overview', productId: 'all', query: '', days: 'all', region: 'focus', benefitStatus: 'open', limit: 12 };
const categoryTitles = { overview: ['值得关注的，都在这里。', '聚合 AI 产品新动态，把时间留给真正重要的事。'] };
const number = new Intl.NumberFormat('zh-CN');

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

function product(id) { return state.catalog.products.find(p => p.id === id) || { id, name: id, short: '?', color: '#7b8e80' }; }
function category(id) { return state.catalog.categories.find(c => c.id === id); }
function source(id) { return state.feed.sources.find(s => s.id === id); }

function avatar(p) {
  const element = node('span', 'product-avatar', p.short);
  if (/^#[\da-f]{6}$/i.test(p.color)) element.style.setProperty('--product-color', p.color);
  element.setAttribute('aria-hidden', 'true');
  return element;
}

function externalLink(url, text, className = 'original-link') {
  const href = safeUrl(url);
  if (!href) return node('span', className, '原文链接暂不可用');
  const element = node('a', className, text);
  element.href = href;
  element.target = '_blank';
  element.rel = 'noopener noreferrer';
  element.append(icon('external'));
  return element;
}

function formatDate(value, withTime = false) {
  const time = timestamp(value);
  if (time === null) return '尚未成功读取';
  const year = new Date(time).getUTCFullYear() !== new Date().getUTCFullYear() ? { year: 'numeric' } : {};
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', ...year, month: 'numeric', day: 'numeric', ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}) }).format(time);
}

function emptyState(title, description, symbol = 'search') {
  const element = node('div', 'empty-state');
  element.append(icon(symbol), node('h3', '', title), node('p', '', description));
  return element;
}

function renderNavigation() {
  const entries = [{ id: 'overview', name: '资讯总览', icon: 'grid' }, ...state.catalog.categories];
  $('category-nav').replaceChildren(...entries.map(c => {
    const link = node('a', `nav-link${state.category === c.id ? ' active' : ''}`);
    link.href = `#${c.id}`;
    if (state.category === c.id) link.setAttribute('aria-current', 'page');
    link.append(icon(c.icon), node('span', '', c.name));
    if (c.id === 'benefits' && state.feed) link.append(node('span', 'nav-count', state.feed.items.filter(i => i.categories.includes('benefits') && benefitStatus(i) !== 'ended' && !(i.productId === 'qoder' && i.region === 'cn')).length));
    return link;
  }));
  $('sidebar-products').replaceChildren(...state.catalog.products.map(p => {
    const button = node('button', `side-product${state.productId === p.id ? ' active' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.productId === p.id));
    button.append(avatar(p), node('span', '', p.name));
    if (p.id === 'qoder') button.append(node('small', '', 'GLOBAL'));
    button.addEventListener('click', () => { state.productId = state.productId === p.id ? 'all' : p.id; state.limit = 12; render(); closeNavigation(); });
    return button;
  }));
}

function renderProductFilters() {
  const products = [{ id: 'all', name: '全部产品' }, ...state.catalog.products];
  $('product-filters').replaceChildren(...products.map(p => {
    const button = node('button', `product-chip${state.productId === p.id ? ' active' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(state.productId === p.id));
    if (p.color) {
      const dot = node('span', 'chip-dot');
      dot.style.setProperty('--product-color', p.color);
      button.append(dot);
    }
    button.append(document.createTextNode(p.name));
    button.addEventListener('click', () => { state.productId = p.id; state.limit = 12; render(); });
    return button;
  }));
}

function benefitBadge(item) {
  const status = benefitStatus(item);
  return node('span', `benefit-status ${status}`, BENEFIT_NAMES[status]);
}

function pendingFlag(item) {
  if (!item.pending) return null;
  const flag = node('span', 'pending-flag', '待确认');
  flag.title = '这条内容来自官方页面的自动收录，暂时没有读到明确的发布日期，请以原文为准。';
  return flag;
}

function deadline(item) {
  if (item.benefit?.endAt) return `截至 ${formatDate(item.benefit.endAt, item.benefit.endPrecision !== 'date')}${item.benefit.endPrecision !== 'date' ? '（北京时间）' : ''}`;
  if (item.benefit?.ongoing) return '原文说明长期有效';
  return '截止时间未明确';
}

function benefitCard(item) {
  const card = node('article', 'benefit-card');
  const top = node('div', 'benefit-card-top');
  const label = node('div', 'product-label');
  const p = product(item.productId);
  label.append(avatar(p), node('span', '', p.name));
  top.append(label, benefitBadge(item));
  const cardFlag = pendingFlag(item);
  if (cardFlag) top.append(cardFlag);
  const heading = node('h3', '', item.title);
  const summary = node('p', 'benefit-card-summary', item.summary.join(' '));
  const scope = node('span', 'benefit-scope', REGION_NAMES[item.region] || REGION_NAMES.unknown);
  const bottom = node('div', 'benefit-card-bottom');
  const date = node('span', 'benefit-deadline', deadline(item));
  date.title = item.benefit?.deadlineText || '原文未说明';
  bottom.append(date, externalLink(item.url, '查看原文', ''));
  const provenance = node('p', 'benefit-provenance', `${source(item.sourceId)?.name || '已收录来源'} · ${item.summaryMethod === 'ai' ? 'AI 摘要' : '原文摘录'}`);
  card.append(top, scope, heading, summary, provenance, bottom);
  return card;
}

function newsCard(item) {
  const article = node('article', 'news-item');
  const meta = node('div', 'news-meta');
  const p = product(item.productId);
  meta.append(avatar(p), node('span', 'news-product-name', p.name));
  for (const id of item.categories) {
    const c = category(id);
    if (c) meta.append(node('span', `category-label ${id}`, c.name));
  }
  if (item.productId === 'qoder' || item.region === 'unknown') meta.append(node('span', 'region-label', REGION_NAMES[item.region] || REGION_NAMES.unknown));
  const flag = pendingFlag(item);
  if (flag) meta.append(flag);
  const date = node('time', 'news-date', item.publishedAt ? formatDate(item.publishedAt) : `首次发现 ${formatDate(item.firstSeenAt)}`);
  const timeValue = item.publishedAt || item.firstSeenAt;
  if (timeValue) date.dateTime = timeValue;
  date.title = item.publishedAt ? `原文发布时间：${formatDate(item.publishedAt, true)}` : '原文未提供发布时间，此处是首次收录时间';
  meta.append(date);
  const heading = node('h3');
  const link = externalLink(item.url, item.title, 'news-title-link');
  const linkIcon = link.querySelector('svg');
  if (linkIcon) linkIcon.remove();
  heading.append(link);
  const summary = node('ul', 'news-summary');
  summary.append(...item.summary.slice(0, 4).map(text => node('li', '', text)));
  article.append(meta, heading, summary);
  if (item.categories.includes('benefits')) {
    const details = node('div', 'benefit-detail');
    details.append(benefitBadge(item), node('span', '', deadline(item)));
    if (item.benefit?.limited) details.append(node('span', '', '限量权益，余量以官方为准'));
    const terms = node('details', 'benefit-terms');
    terms.append(node('summary', '', '参与条件与时间依据'));
    terms.append(node('p', '', `适用人群：${item.benefit?.eligibility || '原文未说明'}`));
    terms.append(node('p', '', `参与方式：${item.benefit?.howToClaim || '查看官方原文'}`));
    terms.append(node('p', '', `时间依据：${item.benefit?.deadlineText || '原文未说明'}`));
    article.append(details, terms);
  }
  const footer = node('div', 'news-footer');
  const sourceLabel = node('div', 'source-label');
  const sourceName = source(item.sourceId)?.name || '官方来源';
  sourceLabel.append(icon('globe'), node('span', '', `${sourceName} · ${item.summaryMethod === 'ai' ? 'AI 摘要' : '原文摘录'}`));
  footer.append(sourceLabel, externalLink(item.url, '阅读原文'));
  article.append(footer);
  return article;
}

function renderSources() {
  const sources = state.feed.sources;
  const okay = sources.filter(s => s.status === 'ok').length;
  const limited = sources.length - okay;
  $('source-ok').textContent = okay;
  $('source-limited').textContent = limited;
  $('source-health').textContent = sources.length ? `${okay} / ${sources.length} 正常` : '等待首次采集';
  const okBar = node('span', 'meter-ok');
  const limitedBar = node('span', 'meter-limited');
  okBar.style.width = `${sources.length ? okay / sources.length * 100 : 0}%`;
  limitedBar.style.width = `${sources.length ? limited / sources.length * 100 : 100}%`;
  $('source-meter').replaceChildren(okBar, limitedBar);
  const preview = state.catalog.products.map(p => sources.find(s => s.productId === p.id && s.status === 'ok') || sources.find(s => s.productId === p.id)).filter(Boolean);
  $('source-preview').replaceChildren(...preview.map(s => {
    const row = node('div', 'source-preview-row');
    row.append(avatar(product(s.productId)), node('span', 'source-preview-name', product(s.productId).name), node('span', `source-state ${s.status}`, SOURCE_NAMES[s.status] || '未确认'));
    return row;
  }));
  $('sources-list').replaceChildren(...sources.map(s => {
    const row = node('article', 'source-row');
    const content = node('div', 'source-row-content');
    const heading = node('div', 'source-row-heading');
    heading.append(node('h3', '', s.name), node('span', `source-state ${s.status}`, SOURCE_NAMES[s.status] || '未确认'));
    const footer = node('div', 'source-row-footer');
    if (safeUrl(s.url)) footer.append(externalLink(s.url, '访问来源', ''));
    const time = node('time', '', s.lastSuccessAt ? `最近成功：${formatDate(s.lastSuccessAt, true)}` : '尚无成功采集记录');
    footer.append(time);
    content.append(heading, node('p', '', s.message || '尚未执行采集'), footer);
    row.append(avatar(product(s.productId)), content);
    return row;
  }));
  if (!sources.length) $('sources-list').append(emptyState('等待首次采集', '来源状态将在采集完成后出现。', 'globe'));
}

function renderBudget() {
  const budget = state.feed.run?.budget || {};
  const spent = Number.isFinite(budget.spentCny) ? budget.spentCny : 0;
  $('budget-spent').textContent = `¥${spent.toFixed(2)}`;
  $('budget-progress').style.width = `${Math.max(0, Math.min(100, spent / 30 * 100))}%`;
  $('summary-mode').textContent = budget.mode === 'ai' ? 'AI 摘要 · 启用预算保护' : '原文摘录模式';
  $('budget-note').textContent = budget.mode === 'ai' ? '展示本项目调用成本估算；接近保护阈值后暂停付费调用，实际以服务商账单为准。' : spent > 0 ? '付费摘要已暂停，当前使用原文摘录；金额为本月已记录的成本估算。' : '未启用付费 API，不产生模型调用费用。';
}

function render() {
  if (!state.catalog || !state.feed) return;
  renderNavigation();
  renderProductFilters();
  const current = category(state.category);
  $('breadcrumb-current').textContent = current?.name || '资讯总览';
  $('page-title').textContent = current ? current.name : categoryTitles.overview[0];
  $('page-description').textContent = current?.description || categoryTitles.overview[1];
  document.title = `${current?.name || '资讯总览'} · Agent Daily`;
  $('metric-products').textContent = state.catalog.products.length;
  $('product-total').textContent = state.catalog.products.length;
  const focused = filterItems(state.feed.items, { region: 'focus' });
  $('metric-items').textContent = number.format(focused.length);
  $('metric-benefits').textContent = number.format(activeBenefitCount(focused));
  const newCount = state.feed.run?.newCount || 0;
  $('metric-items-note').textContent = newCount ? `最近一次采集新增 ${newCount} 条` : '可追溯，每一条都有出处';
  const last = state.feed.lastSuccessfulCollectionAt;
  $('update-label').textContent = last ? `${formatDate(last, true)} 成功采集${Date.now() - timestamp(last) > 36 * 3600000 ? ' · 可能延迟' : ''}` : '尚未完成首次采集';
  $('featured-section').hidden = state.category !== 'overview';
  const items = state.feed.items.map(i => ({ ...i, productName: `${product(i.productId).name} ${product(i.productId).description || ''}` }));
  const featured = filterItems(items, { ...state, category: 'benefits', benefitStatus: 'open' })
    .sort((a, b) => (benefitStatus(a) === 'active' ? 0 : 1) - (benefitStatus(b) === 'active' ? 0 : 1)).slice(0, 3);
  $('featured-benefits').replaceChildren(...featured.map(benefitCard));
  if (!featured.length) $('featured-benefits').append(emptyState('暂未收录匹配的福利', '我们只展示有出处的权益，不把未核实的信息当成优惠。', 'gift'));
  $('benefit-filter-label').hidden = state.category !== 'benefits';
  $('reset-filters').hidden = state.productId === 'all' && !state.query && state.days === 'all' && state.region === 'focus' && state.benefitStatus === 'open';
  const visible = filterItems(items, state);
  const title = state.category === 'overview' ? '最新动态' : `${current?.name || '资讯'}列表`;
  $('news-title').replaceChildren(document.createTextNode(title + ' '), node('span', 'count-pill', number.format(visible.length)));
  $('news-list').replaceChildren(...visible.slice(0, state.limit).map(newsCard));
  $('news-list').setAttribute('aria-busy', 'false');
  if (!visible.length) {
    const hasData = state.feed.items.length > 0;
    $('news-list').append(emptyState(hasData ? '没有找到匹配的资讯' : '还没有收录内容', hasData ? '试试更换关键词、产品或时间范围，也可以重置筛选。' : '尚无可发布的内容。你可以查看数据源状态，了解哪些渠道可用。'));
  }
  $('load-more').hidden = visible.length <= state.limit;
  renderSources();
  renderBudget();
}

function closeNavigation() {
  $('sidebar').classList.remove('open');
  $('nav-backdrop').hidden = true;
  $('menu-button').setAttribute('aria-expanded', 'false');
}

function setCategoryFromHash() {
  if (!state.catalog) return;
  const hash = location.hash.slice(1);
  state.category = state.catalog.categories.some(c => c.id === hash) ? hash : 'overview';
  state.limit = 12;
  render();
  closeNavigation();
}

async function load() {
  $('load-error').hidden = true;
  $('news-list').setAttribute('aria-busy', 'true');
  $('retry-button').disabled = true;
  try {
    const responses = await Promise.all(['catalog.json', 'feed.json'].map(path => fetch(new URL(`../data/${path}`, import.meta.url), { cache: 'no-cache' })));
    if (responses.some(r => !r.ok)) throw new Error('Failed to load data');
    const [catalog, feed] = await Promise.all(responses.map(r => r.json()));
    if (!Array.isArray(catalog.products) || !Array.isArray(catalog.categories)) throw new Error('Invalid catalog');
    state.catalog = catalog;
    state.feed = validateFeed(feed);
    setCategoryFromHash();
  } catch {
    $('load-error').hidden = false;
    $('news-list').replaceChildren(emptyState('数据暂不可用', '请稍后重试，已有来源和历史数据不会因此被判定为空。', 'info'));
    $('news-list').setAttribute('aria-busy', 'false');
    $('featured-section').hidden = true;
    $('update-label').textContent = '数据加载失败';
    $('source-health').textContent = '暂不可用';
  } finally { $('retry-button').disabled = false; }
}

$('today-date').textContent = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date());
let searchTimer;
$('search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.query = event.target.value; state.limit = 12; render(); }, 160); });
for (const [id, field] of [['time-filter', 'days'], ['region-filter', 'region'], ['benefit-filter', 'benefitStatus']]) {
  $(id).addEventListener('change', event => { state[field] = event.target.value; state.limit = 12; render(); });
}
$('reset-filters').addEventListener('click', () => {
  clearTimeout(searchTimer);
  Object.assign(state, { productId: 'all', query: '', days: 'all', region: 'focus', benefitStatus: 'open', limit: 12 });
  $('search').value = '';
  $('time-filter').value = 'all';
  $('region-filter').value = 'focus';
  $('benefit-filter').value = 'open';
  render();
});
$('load-more').addEventListener('click', () => { state.limit += 12; render(); });
for (const id of ['source-button', 'sidebar-source-button', 'view-all-sources']) $(id).addEventListener('click', () => $('sources-dialog').showModal());
$('close-sources').addEventListener('click', () => $('sources-dialog').close());
$('sources-dialog').addEventListener('click', event => { if (event.target === $('sources-dialog')) { const box = event.target.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) event.target.close(); } });
$('menu-button').addEventListener('click', () => { const open = !$('sidebar').classList.contains('open'); $('sidebar').classList.toggle('open', open); $('nav-backdrop').hidden = !open; $('menu-button').setAttribute('aria-expanded', String(open)); });
$('nav-backdrop').addEventListener('click', closeNavigation);
$('retry-button').addEventListener('click', load);
window.addEventListener('hashchange', setCategoryFromHash);
window.addEventListener('keydown', event => {
  if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) && !$('sources-dialog').open) { event.preventDefault(); $('search').focus(); }
  if (event.key === 'Escape') closeNavigation();
});
document.addEventListener('visibilitychange', () => { if (!document.hidden) render(); });
setInterval(() => { if (!document.hidden) render(); }, 60000);
load();
