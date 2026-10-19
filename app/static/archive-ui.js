/* Shared view logic; no network, credentials, or browser storage. */
(function (root) {
  const FAVORITES_KEY = 'weread-mp.favorite-accounts';
  const visibleArticles = (articles, term = '', from = '', to = '') => {
    const query = term.trim().toLocaleLowerCase();
    return articles.filter((article) => (!query || article.title.toLocaleLowerCase().includes(query))
      && (!from || (article.date && article.date >= from))
      && (!to || (article.date && article.date <= to)));
  };
  const updateVisibleSelection = (selected, articles, checked) => articles.forEach((article) => {
    if (article.original_id) checked ? selected.add(article.original_id) : selected.delete(article.original_id);
  });
  const selectionStatus = (selected, articles) => {
    const available = articles.filter((article) => article.original_id);
    const count = available.filter((article) => selected.has(article.original_id)).length;
    return { checked: available.length > 0 && count === available.length, indeterminate: count > 0 && count < available.length, disabled: available.length === 0 };
  };
  const jobSummary = (job) => ({
    success: job.completed || 0,
    failed: job.items ? job.items.filter((item) => item.status === 'failed').length : (job.failures || []).length,
    processed: job.processed || 0,
    total: job.total || 0,
    terminal: ['completed', 'failed'].includes(job.status),
    imageWarnings: new Set((job.warnings || []).map((item) => item.article_id)).size,
  });
  const friendlyError = (error, fallback = '暂时无法完成，请稍后重试。') => {
    if (error.kind === 'expired' || error.status === 409) return '连接已过期，请重新连接微信读书。';
    if (error.kind === 'captcha') return '请先在微信读书完成安全验证，再回到这里重新连接。';
    if (error.kind === 'rate_limited') return '读取较频繁，请稍等片刻再试。';
    if (error.kind === 'invalid_cookie') return '连接信息不完整，请检查后重新填写。';
    if (error.name === 'AbortError') return '等待时间较长，请稍后重试。';
    if (error instanceof TypeError || error.kind === 'network') return '暂时无法连接，请检查网络后重试。';
    return fallback;
  };
  const filterAccounts = (accounts, term = '', favoritesOnly = false, favorites = new Set()) => {
    const query = term.trim().toLocaleLowerCase();
    return accounts.filter((account) => (!query || account.title.toLocaleLowerCase().includes(query))
      && (!favoritesOnly || favorites.has(account.book_id)));
  };
  const loadFavoriteAccounts = (storage, key = FAVORITES_KEY) => {
    try {
      const parsed = JSON.parse(storage?.getItem(key) || '[]');
      return new Set(Array.isArray(parsed) ? parsed.filter((value) => typeof value === 'string' && /^MP_WXS_[A-Za-z0-9_-]{1,100}$/.test(value)) : []);
    } catch (_error) {
      return new Set();
    }
  };
  const saveFavoriteAccounts = (storage, favorites, key = FAVORITES_KEY) => {
    try { storage?.setItem(key, JSON.stringify([...favorites].sort())); } catch (_error) { /* private mode can reject storage */ }
  };
  const toggleFavoriteAccount = (favorites, bookId) => {
    const next = new Set(favorites);
    if (next.has(bookId)) next.delete(bookId); else next.add(bookId);
    return next;
  };
  const exports = { visibleArticles, updateVisibleSelection, selectionStatus, jobSummary, friendlyError, filterAccounts, loadFavoriteAccounts, saveFavoriteAccounts, toggleFavoriteAccount };
  if (typeof module !== 'undefined' && module.exports) module.exports = exports;
  else root.ArchiveUI = exports;
})(typeof window !== 'undefined' ? window : globalThis);
