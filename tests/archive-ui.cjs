const { test } = require('node:test');
const assert = require('node:assert/strict');
const { visibleArticles, updateVisibleSelection, selectionStatus, jobSummary, friendlyError, filterAccounts, loadFavoriteAccounts, saveFavoriteAccounts, toggleFavoriteAccount } = require('../app/static/archive-ui.js');
const articles = [
  { original_id: 'a', title: '经营的本质', summary: '成长', date: '2026-10-01' },
  { original_id: 'b', title: '明天的机会', summary: '经营', date: '2026-10-02' },
  { original_id: '', title: '无标识文章', date: '' },
];
test('title and inclusive date filters use real dates without matching missing dates', () => {
  assert.deepEqual(visibleArticles(articles, '经营', '2026-10-01', '2026-10-01'), [articles[0]]);
  assert.deepEqual(visibleArticles(articles, '', '2026-10-02', '2026-10-01'), []);
  assert.equal(visibleArticles(articles, '', '2026-10-01', '').length, 2);
});
test('select all affects only visible selectable items and preserves hidden selections', () => {
  const selected = new Set(['b']);
  updateVisibleSelection(selected, [articles[0], articles[2]], true);
  assert.deepEqual([...selected], ['b', 'a']);
  assert.equal(selectionStatus(selected, articles).checked, true);
  updateVisibleSelection(selected, [articles[0]], false);
  assert.deepEqual([...selected], ['b']);
  assert.equal(selectionStatus(selected, articles).indeterminate, true);
});
test('empty filtered results never show a checked select-all', () => {
  assert.deepEqual(selectionStatus(new Set(['b']), []), {checked:false, indeterminate:false, disabled:true});
});
test('job statistics use actual outcomes rather than estimating success from percent', () => {
  assert.deepEqual(jobSummary({status:'running', progress:70, completed:1, processed:2, total:3, items:[{status:'failed'}], warnings:[]}), {success:1, failed:1, processed:2, total:3, terminal:false, imageWarnings:0});
  assert.equal(jobSummary({status:'failed', completed:2, processed:3, total:3, failures:[{}], warnings:[{article_id:'a'},{article_id:'a'},{article_id:'b'}]}).imageWarnings, 2);
});
test('ordinary error messages provide a next step and hide upstream internals', () => {
  assert.match(friendlyError({kind:'captcha'}), /微信读书/);
  assert.match(friendlyError({kind:'expired'}), /重新连接/);
  assert.equal(friendlyError(new Error('secret upstream trace'), '暂时无法读取，请稍后重试。'), '暂时无法读取，请稍后重试。');
});

test('favorite accounts can be loaded, toggled, and filtered independently of article state', () => {
  const storage = {
    value: JSON.stringify(['MP_WXS_2', 'invalid', 'MP_WXS_2']),
    getItem() { return this.value; },
    setItem(_key, value) { this.value = value; },
  };
  const favorites = loadFavoriteAccounts(storage);
  assert.deepEqual([...favorites], ['MP_WXS_2']);
  const next = toggleFavoriteAccount(favorites, 'MP_WXS_1');
  assert.deepEqual([...next].sort(), ['MP_WXS_1', 'MP_WXS_2']);
  saveFavoriteAccounts(storage, next);
  assert.deepEqual(JSON.parse(storage.value), ['MP_WXS_1', 'MP_WXS_2']);
  assert.deepEqual(filterAccounts([
    {book_id: 'MP_WXS_1', title: '刘润'},
    {book_id: 'MP_WXS_2', title: '人间罗盘'},
  ], '', true, next).map((account) => account.book_id), ['MP_WXS_1', 'MP_WXS_2']);
  assert.deepEqual(filterAccounts([
    {book_id: 'MP_WXS_1', title: '刘润'},
    {book_id: 'MP_WXS_2', title: '人间罗盘'},
  ], '罗盘', false, next).map((account) => account.book_id), ['MP_WXS_2']);
});
