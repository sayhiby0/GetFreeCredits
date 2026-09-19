import test from 'node:test';
import assert from 'node:assert/strict';
import { benefitStatus, filterItems, activeBenefitCount, safeUrl, validateFeed, timestamp } from '../site/assets/logic.mjs';

const now = Date.parse('2026-09-18T04:00:00Z');
const base = { id: '1', productId: 'qoder', productName: 'Qoder', categories: ['benefits', 'updates'], region: 'international', title: 'Pro 首月 Credits 翻倍', summary: ['限时加赠 Credits'], url: 'https://example.com/post', publishedAt: '2026-09-01', firstSeenAt: '2026-09-17T04:00:00Z', benefit: { startAt: '2026-09-01T10:00:00+08:00', endAt: '2026-09-30T23:59:59+08:00' } };

test('benefit statuses follow current time', () => {
  assert.equal(benefitStatus(base, now), 'active');
  assert.equal(benefitStatus(base, Date.parse('2026-10-01')), 'ended');
  assert.equal(benefitStatus(base, Date.parse('2026-08-31')), 'upcoming');
  assert.equal(benefitStatus({ ...base, benefit: {} }, now), 'unknown');
});
test('membership duration does not imply offer deadline', () => {
  assert.equal(benefitStatus({ ...base, benefit: { deadlineText: '赠送30天会员' } }, now), 'unknown');
});
test('invalid or inverted range remains unknown', () => {
  assert.equal(benefitStatus({ ...base, benefit: { startAt: '2026-10-01', endAt: '2026-09-01' } }, now), 'unknown');
  assert.equal(benefitStatus({ ...base, benefit: { endAt: 'not-a-date' } }, now), 'unknown');
});
test('date-only end includes end of Shanghai day', () => {
  const offer = { ...base, benefit: { endAt: '2026-09-18' } };
  assert.equal(benefitStatus(offer, Date.parse('2026-09-18T15:59:59Z')), 'active');
  assert.equal(benefitStatus(offer, Date.parse('2026-09-18T16:00:00Z')), 'ended');
});
test('only explicit perpetual offer stays active', () => {
  assert.equal(benefitStatus({ ...base, benefit: { ongoing: true } }, now), 'active');
  assert.equal(benefitStatus({ ...base, benefit: { startAt: '2026-09-01' } }, now), 'unknown');
});
test('default hides Qoder CN but preserves other Chinese products', () => {
  const items = [base, { ...base, id: '2', region: 'cn' }, { ...base, id: '3', productId: 'workbuddy', region: 'cn' }];
  assert.deepEqual(filterItems(items, {}, now).map(i => i.id), ['1', '3']);
  assert.equal(filterItems(items, { region: 'all' }, now).length, 3);
});
test('unknown-region Qoder offers not counted as confirmed', () => {
  assert.equal(activeBenefitCount([base, { ...base, id: '2', region: 'unknown' }, { ...base, id: '3', region: 'cn' }], now), 1);
});
test('cross-category record appears in both without duplicating', () => {
  assert.equal(filterItems([base], { category: 'benefits' }, now).length, 1);
  assert.equal(filterItems([base], { category: 'updates' }, now).length, 1);
  assert.equal(filterItems([base], { category: 'practice' }, now).length, 0);
});
test('search matches Chinese product aliases and is case insensitive', () => {
  const item = { ...base, productId: 'qwenwork', productName: '千问办公' };
  assert.equal(filterItems([item], { query: '千问' }, now).length, 1);
  assert.equal(filterItems([item], { query: '  CREDITS  ' }, now).length, 1);
  assert.equal(filterItems([item], { query: '不存在的消息' }, now).length, 0);
});
test('combined filters and unknown publish date use first-seen time', () => {
  const unknown = { ...base, id: '2', publishedAt: null };
  assert.deepEqual(filterItems([base, unknown], { days: '7', category: 'benefits', productId: 'qoder', benefitStatus: 'active' }, now).map(i => i.id), ['2']);
});
test('ended offers hidden in default benefit view and available in archive', () => {
  const ended = { ...base, benefit: { endAt: '2026-09-01' } };
  assert.equal(filterItems([ended], { category: 'benefits', benefitStatus: 'open' }, now).length, 0);
  assert.equal(filterItems([ended], { category: 'benefits', benefitStatus: 'ended' }, now).length, 1);
});
test('URL protocol and embedded credentials rejected', () => {
  for (const url of ['javascript:alert(1)', 'data:text/html,test', 'http://example.com', 'https://user:pass@example.com', '/relative']) assert.equal(safeUrl(url), null);
  assert.equal(safeUrl('https://example.com'), 'https://example.com/');
});
test('feed schema fails closed for malformed and duplicate items', () => {
  assert.throws(() => validateFeed({ items: [] }));
  assert.throws(() => validateFeed({ schemaVersion: 1, items: [base, base], sources: [] }));
  assert.throws(() => validateFeed({ schemaVersion: 1, items: [{ ...base, url: 'javascript:alert(1)' }], sources: [] }));
  assert.equal(validateFeed({ schemaVersion: 1, items: [base], sources: [] }).items.length, 1);
});
test('no timestamp manufactured from missing data', () => {
  assert.equal(timestamp(null), null);
  assert.equal(timestamp('not a date'), null);
});
