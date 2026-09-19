export const REGION_NAMES = { international: '国际版', cn: '国内专属', global: '通用', unknown: '范围未说明' };
export const BENEFIT_NAMES = { active: '进行中', ended: '已结束', upcoming: '未开始', unknown: '时效未明确' };
export const SOURCE_NAMES = { ok: '正常', partial: '部分可读', restricted: '访问受限', unconfigured: '待接入', error: '暂时失败' };

export function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function timestamp(value, endOfDay = false) {
  if (!value || typeof value !== 'string') return null;
  const input = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? `${value}T${endOfDay ? '23:59:59.999' : '00:00:00'}+08:00`
    : value;
  const parsed = Date.parse(input);
  return Number.isFinite(parsed) ? parsed : null;
}

export function benefitStatus(item, now = Date.now()) {
  if (!item.benefit) return 'unknown';
  const start = timestamp(item.benefit.startAt);
  const end = timestamp(item.benefit.endAt, true);
  if (start !== null && end !== null && start > end) return 'unknown';
  if (end !== null && now > end) return 'ended';
  if (start !== null && now < start) return 'upcoming';
  if (end !== null || item.benefit.ongoing === true) return 'active';
  return 'unknown';
}

export function itemTime(item) {
  return timestamp(item.publishedAt) ?? timestamp(item.firstSeenAt) ?? 0;
}

export function filterItems(items, filters, now = Date.now()) {
  const query = (filters.query || '').trim().toLocaleLowerCase();
  return items.filter(item => {
    if (filters.productId && filters.productId !== 'all' && item.productId !== filters.productId) return false;
    if (filters.category && filters.category !== 'overview' && !item.categories.includes(filters.category)) return false;
    if ((filters.region || 'focus') === 'focus' && item.productId === 'qoder' && item.region === 'cn') return false;
    if (filters.region === 'international' && (item.productId !== 'qoder' || !['international', 'global'].includes(item.region))) return false;
    if (filters.region === 'cn' && (item.productId !== 'qoder' || item.region !== 'cn')) return false;
    if (filters.days && filters.days !== 'all' && itemTime(item) < now - Number(filters.days) * 86400000) return false;
    if (query && ![item.title, item.originalTitle, ...(item.summary || []), item.productId, item.productName || '', ...(item.tags || [])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query)) return false;
    if (filters.category === 'benefits' && filters.benefitStatus && filters.benefitStatus !== 'all') {
      const status = benefitStatus(item, now);
      if (filters.benefitStatus === 'active' && item.productId === 'qoder' && item.region === 'unknown') return false;
      if (filters.benefitStatus === 'open' ? status === 'ended' : status !== filters.benefitStatus) return false;
    }
    return true;
  }).sort((a, b) => itemTime(b) - itemTime(a) || a.id.localeCompare(b.id));
}

export function activeBenefitCount(items, now = Date.now()) {
  return items.filter(item => item.categories.includes('benefits') && benefitStatus(item, now) === 'active'
    && !(item.productId === 'qoder' && ['cn', 'unknown'].includes(item.region))).length;
}

export function validateFeed(feed) {
  if (!feed || feed.schemaVersion !== 1 || !Array.isArray(feed.items) || !Array.isArray(feed.sources)) throw new Error('Invalid feed');
  const ids = new Set();
  for (const item of feed.items) {
    if (!item || typeof item.id !== 'string' || ids.has(item.id) || typeof item.title !== 'string'
      || !Array.isArray(item.summary) || !item.summary.every(s => typeof s === 'string')
      || !Array.isArray(item.categories) || !item.categories.every(c => typeof c === 'string')
      || typeof item.productId !== 'string' || !safeUrl(item.url)) throw new Error('Invalid item');
    ids.add(item.id);
  }
  return feed;
}
