(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const UI = window.ArchiveUI;
  const favoriteStorage = (() => { try { return window.localStorage; } catch (_error) { return null; } })();
  const state = {
    accounts: [], articles: [], selected: new Set(), bookId: '', cache: new Map(),
    favorites: UI.loadFavoriteAccounts(favoriteStorage), favoritesOnly: false, articleQuery: { fromDate: '', toDate: '' },
    connectionVersion: 0, connected: false, authenticated: false, loading: false, listVersion: 0,
    jobId: '', job: null, jobBookId: '', timer: null, pollVersion: 0, polling: false,
    qrLoginId: '', qrStatusUrl: '', qrTimer: null, qrVersion: 0, qrPolling: false, qrScanned: false,
    qrStatusFailures: 0, qrImageFailures: 0,
    submitting: false, downloading: false, expiredAuth: false, listError: false, packages: new Map(), needsJobRefresh: false, expiredJobs: new Set(),
  };
  const api = async (path, options = {}) => {
    const { timeoutMs = 30000, ...requestOptions } = options;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, { ...requestOptions, signal: controller.signal,
        credentials: 'same-origin', cache: 'no-store',
        headers: { 'Content-Type': 'application/json', ...(requestOptions.headers || {}) } });
      let data = null;
      try { data = await response.json(); } catch (error) { if (error.name === 'AbortError') throw error; }
      if (!response.ok) {
        const error = new Error(typeof data?.detail === 'string' ? data.detail : data?.detail?.message || '无法完成操作');
        error.kind = data?.detail?.kind; error.status = response.status; throw error;
      }
      return data;
    } finally { clearTimeout(timeout); }
  };
  const message = (id, value, error = false) => {
    $(id).textContent = value || '';
    $(id).className = `message${error ? ' error' : ''}`;
    $(id).hidden = !value;
  };
  const setMessage = (value, error = false) => message('message', value, error);
  const step = (current) => {
    for (let index = 1; index <= 3; index++) {
      const item = $(`step-${index}`);
      item.className = `step${index === current ? ' on' : index < current ? ' done' : ''}`;
      if (index === current) item.setAttribute('aria-current', 'step'); else item.removeAttribute('aria-current');
    }
  };
  const connected = (value) => {
    state.connected = value;
    $('connection-state').textContent = value ? '已连接' : '未连接';
    $('connection-chip').className = `conn-chip${value ? ' on' : ''}`;
  };
  const stopQrPolling = () => { clearTimeout(state.qrTimer); state.qrTimer = null; state.qrVersion++; state.qrPolling = false; };
  const showConnection = () => {
    stopQrPolling(); state.qrLoginId = ''; state.qrStatusUrl = ''; state.qrScanned = false;
    $('public-info').hidden = false;
    $('operation-guide').hidden = false;
    $('connect-panel').hidden = false; $('scan-panel').hidden = true;
    $('selection-panel').hidden = true; $('dock').hidden = true; step(1);
  };
  const showScan = () => {
    $('public-info').hidden = true;
    $('operation-guide').hidden = false;
    $('connect-panel').hidden = true; $('scan-panel').hidden = false;
    $('selection-panel').hidden = true; $('dock').hidden = true; step(1);
  };
  const showApp = () => {
    state.authenticated = true;
    $('app-panel').hidden = false;
    showConnection();
  };
  const showSelection = () => {
    $('public-info').hidden = true;
    $('operation-guide').hidden = true;
    $('connect-panel').hidden = true; $('scan-panel').hidden = true;
    $('selection-panel').hidden = false; $('dock').hidden = false; step(2);
  };
  const saveCurrent = () => {
    if (state.bookId) state.cache.set(state.bookId, { articles: state.articles, selected: state.selected, query: state.articleQuery });
  };
  const selectionTotal = () => {
    saveCurrent(); return [...state.cache.values()].reduce((sum, value) => sum + value.selected.size, 0);
  };
  const openDialog = (id) => { if (!$(id).open) $(id).showModal(); };
  const stopPolling = () => { clearTimeout(state.timer); state.timer = null; state.pollVersion++; state.polling = false; };
  const expired = (auth = false) => {
    state.connectionVersion++;
    state.expiredAuth = auth;
    if (auth) { state.authenticated = false; state.job = null; state.jobId = ''; state.packages.clear(); }
    connected(false); state.listVersion++; state.loading = false; stopPolling();
    $('article-loading').hidden = true; $('download-dialog').close();
    $('expired-title').textContent = '连接已过期';
    $('expired-description').textContent = '微信读书登录状态已失效，请重新扫码。当前已选文章会为你保留。';
    $('retained-count').textContent = `已保留 ${selectionTotal()} 篇已选文章`;
    openDialog('expired-dialog'); updateSelection();
  };
  const checkJobExpiry = (job) => {
    if (!job.connection_expired || state.expiredJobs.has(job.job_id)) return false;
    state.expiredJobs.add(job.job_id); expired(); return true;
  };
  const handleError = (error, target, fallback) => {
    if (error.status === 401) { expired(); return; }
    if (error.kind === 'expired' || error.status === 409) { expired(); return; }
    message(target, UI.friendlyError(error, fallback), true);
  };
  const visible = () => UI.visibleArticles(state.articles, $('search').value, $('from-date').value, $('to-date').value);
  const updateSelection = () => {
    const rows = visible(); const status = UI.selectionStatus(state.selected, rows);
    $('select-visible').checked = status.checked;
    $('select-visible').indeterminate = status.indeterminate;
    $('select-visible').disabled = status.disabled || state.loading;
    $('selected-count').textContent = state.selected.size;
    $('list-count').textContent = `当前结果 ${rows.length} 篇 · 已选 ${state.selected.size} 篇`;
    $('clear-selection').hidden = state.selected.size === 0;
    $('view-job').hidden = !state.job || !UI.jobSummary(state.job).terminal;
    const active = state.submitting || (state.job && !UI.jobSummary(state.job).terminal);
    $('export').disabled = !state.connected || state.loading || (!active && state.selected.size === 0);
    $('export').textContent = active ? '查看下载进度' : '下载文章';
    $('dock-hint').textContent = state.selected.size ? '将选中文章保存为 Markdown + ZIP' : '勾选要导出的文章';
    saveCurrent();
  };
  const renderAccounts = () => {
    const query = $('account-search').value.trim().toLowerCase();
    const list = $('accounts'); list.replaceChildren();
    const accounts = UI.filterAccounts(state.accounts, query, state.favoritesOnly, state.favorites);
    const favoriteCount = state.accounts.filter((account) => state.favorites.has(account.book_id)).length;
    $('account-total').textContent = state.accounts.length;
    $('favorite-total').textContent = favoriteCount;
    $('accounts-all').setAttribute('aria-pressed', String(!state.favoritesOnly));
    $('accounts-favorites').setAttribute('aria-pressed', String(state.favoritesOnly));
    for (const account of accounts) {
      const row = document.createElement('div'); row.className = 'account-row';
      const button = document.createElement('button'); button.type = 'button';
      button.className = `account-item${account.book_id === state.bookId ? ' on' : ''}`;
      button.setAttribute('aria-pressed', String(account.book_id === state.bookId));
      const avatar = document.createElement('span'); avatar.className = 'ava';
      // Initials are the supplied design's text avatar; no synthetic cover images.
      avatar.textContent = account.title.slice(0, 1);
      const meta = document.createElement('span'); meta.className = 'meta';
      const title = document.createElement('b'); title.textContent = account.title;
      const sub = document.createElement('small');
      sub.textContent = state.cache.has(account.book_id) ? `${state.cache.get(account.book_id).articles.length} 篇已读取` : '点击读取文章';
      meta.append(title, sub); button.append(avatar, meta);
      button.addEventListener('click', () => chooseAccount(account.book_id));
      const favorite = state.favorites.has(account.book_id);
      const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = `favorite-toggle${favorite ? ' on' : ''}`;
      toggle.textContent = favorite ? '★' : '☆'; toggle.setAttribute('aria-pressed', String(favorite));
      toggle.setAttribute('aria-label', `${favorite ? '取消收藏' : '收藏'}公众号：${account.title}`);
      toggle.title = favorite ? '取消收藏' : '收藏公众号';
      toggle.addEventListener('click', () => {
        state.favorites = UI.toggleFavoriteAccount(state.favorites, account.book_id);
        UI.saveFavoriteAccounts(favoriteStorage, state.favorites);
        renderAccounts();
      });
      row.append(button, toggle); list.append(row);
    }
    $('accounts-empty').hidden = accounts.length > 0;
    if (!state.accounts.length) $('accounts-empty').textContent = '还没有已关注的公众号。请先在微信读书关注公众号，再重新连接。';
    else if (state.favoritesOnly && !favoriteCount) $('accounts-empty').textContent = '还没有收藏的公众号。';
    else $('accounts-empty').textContent = '没有找到公众号，请调整关键词。新关注的公众号可在重新连接后读取。';
  };
  const renderArticles = () => {
    const rows = visible(); const list = $('articles'); list.replaceChildren();
    $('filter-message').hidden = !($('from-date').value && $('to-date').value && $('from-date').value > $('to-date').value);
    for (const article of rows) {
      const row = document.createElement('label'); row.className = 'article-row';
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = state.selected.has(article.original_id);
      input.disabled = !article.original_id; input.setAttribute('aria-label', `选择文章：${article.title}`);
      const thumb = document.createElement('span'); thumb.className = 'thumb';
      const icon = document.createElement('img'); icon.src = '/static/icon-file-earmark-text.svg'; icon.alt = ''; icon.className = 'icon'; thumb.append(icon);
      const copy = document.createElement('span'); copy.className = 't';
      const title = document.createElement('b'); title.textContent = article.title;
      const summary = document.createElement('small'); summary.textContent = article.summary || '暂无摘要';
      if (!article.original_id) { summary.textContent = '暂时无法导出这篇文章'; summary.className = 'unavailable'; }
      copy.append(title, summary);
      const date = document.createElement('time'); date.className = 'article-date'; date.textContent = article.date || '日期未知';
      if (article.date) date.dateTime = article.date;
      input.addEventListener('change', () => {
        input.checked ? state.selected.add(article.original_id) : state.selected.delete(article.original_id); updateSelection();
      });
      row.append(input, thumb, copy, date); list.append(row);
    }
    $('articles-empty').hidden = rows.length > 0 || state.listError;
    const noContent = state.articles.length === 0;
    $('empty-title').textContent = noContent ? '该公众号暂无可获取内容' : '没有找到匹配的文章';
    $('empty-description').textContent = noContent ? '这个公众号暂时没有可读取的文章。你可以换一个公众号再试试。' : '换一个标题关键词或调整日期范围再试试。';
    $('empty-action').textContent = noContent ? '更换公众号' : '清空筛选';
    updateSelection();
  };
  const articleListPath = (bookId, query = {}) => {
    const params = [];
    if (query.fromDate) params.push(`from_date=${encodeURIComponent(query.fromDate)}`);
    if (query.toDate) params.push(`to_date=${encodeURIComponent(query.toDate)}`);
    const suffix = params.join('&');
    return `/api/accounts/${encodeURIComponent(bookId)}/articles${suffix ? `?${suffix}` : ''}`;
  };
  const resetFilters = async () => {
    ['search', 'from-date', 'to-date'].forEach((id) => { $(id).value = ''; });
    $('filter-message').hidden = true;
    if (state.bookId) await loadArticles({ fromDate: '', toDate: '' });
    else renderArticles();
  };
  const chooseAccount = async (bookId) => {
    if (state.bookId === bookId) return;
    saveCurrent(); state.bookId = bookId; ++state.listVersion; state.loading = false;
    const cached = state.cache.get(bookId); state.articles = cached?.articles || []; state.selected = cached?.selected || new Set();
    state.articleQuery = cached?.query || { fromDate: '', toDate: '' };
    $('from-date').value = state.articleQuery.fromDate; $('to-date').value = state.articleQuery.toDate; $('search').value = '';
    $('coverage').hidden = true; $('picker-placeholder').hidden = true;
    $('articles-panel').hidden = false; $('article-loading').hidden = true;
    $('account-title').textContent = state.accounts.find((account) => account.book_id === bookId)?.title || '公众号文章';
    renderAccounts(); renderArticles(); await loadArticles(state.articleQuery);
  };
  const loadArticles = async (query = state.articleQuery) => {
    if (!state.bookId || !state.connected || state.loading) return;
    const bookId = state.bookId; const version = ++state.listVersion; state.loading = true; state.listError = false;
    state.articleQuery = { fromDate: query?.fromDate || '', toDate: query?.toDate || '' };
    $('picker-placeholder').hidden = true; $('articles-panel').hidden = true; $('article-loading').hidden = false;
    $('load-articles').disabled = true; $('search-date').disabled = true; setMessage(''); updateSelection();
    try {
      const data = await api(articleListPath(bookId, state.articleQuery), {timeoutMs:180000});
      if (version !== state.listVersion) return;
      state.articles = data.articles;
      // Keep IDs through reconnection. The backend validates exports against its fresh list.
      state.selected = new Set([...state.selected].filter((id) => state.articles.some((article) => article.original_id === id)));
      $('account-sub').textContent = state.articleQuery.fromDate || state.articleQuery.toDate
        ? `所选日期范围内读取 ${state.articles.length} 篇文章`
        : `已读取最近 1 年内 ${state.articles.length} 篇文章`;
      $('coverage').hidden = !(data.earliest_date && data.latest_date);
      $('range-text').textContent = `${data.earliest_date || ''} ~ ${data.latest_date || ''}`;
      const stopMessages = {
        article_limit: '检索达到单次文章数量上限，请缩小日期范围。',
        page_limit: '检索达到分页安全上限，以下为本次已读取的内容。',
        we_read_terminal: '微信读书接口提示历史列表已结束。',
        repeated_page: '微信读书重复返回同一页，检索已停止。',
        non_advancing_cursor: '微信读书分页游标未前进，检索已停止。',
        invalid_cursor: '微信读书未返回可用分页游标，检索已停止。',
      };
      if (stopMessages[data.stop_reason]) setMessage(stopMessages[data.stop_reason]);
      renderArticles(); renderAccounts();
    } catch (error) {
      if (version !== state.listVersion) return;
      state.listError = true;
      $('account-sub').textContent = state.articles.length ? `保留上次读取的 ${state.articles.length} 篇文章` : '文章读取未完成';
      handleError(error, 'message', '暂时无法读取这个公众号的文章，请稍后点击“重新读取”。');
      renderArticles();
    } finally {
      if (version === state.listVersion) {
        state.loading = false; $('articles-panel').hidden = false; $('article-loading').hidden = true;
        $('load-articles').disabled = false; $('search-date').disabled = false; updateSelection();
      }
    }
  };
  const applyConnection = async (accounts) => {
    state.accounts = accounts;
    const accountIds = new Set(accounts.map((account) => account.book_id));
    state.favorites = new Set([...state.favorites].filter((bookId) => accountIds.has(bookId)));
    UI.saveFavoriteAccounts(favoriteStorage, state.favorites);
    state.needsJobRefresh = Boolean(state.jobId); connected(true); $('connect-message').textContent = '';
    renderAccounts(); showSelection();
    if (state.bookId && accounts.some((account) => account.book_id === state.bookId)) await loadArticles(state.articleQuery);
    else {
      state.bookId = ''; state.articles = []; state.selected = new Set();
      $('articles-panel').hidden = true; $('picker-placeholder').hidden = false;
    }
    if (state.jobId && state.job && !UI.jobSummary(state.job).terminal) startPolling();
    updateSelection();
  };
  const restoreConnection = async () => {
    const data = await api('/api/accounts'); await applyConnection(data.accounts);
  };
  const setQrStatus = (qrState, text) => {
    const observed = ['waiting', 'scanned', 'otp_required', 'success', 'expired', 'failed'].includes(qrState) ? qrState : 'waiting';
    if (observed === 'scanned' || observed === 'otp_required') state.qrScanned = true;
    if (['success', 'expired', 'failed'].includes(observed)) state.qrScanned = false;
    const normalized = observed === 'waiting' && state.qrScanned ? 'scanned' : observed;
    $('qr-status').className = `status-line ${normalized}`;
    $('qr-status-text').textContent = normalized === 'success' ? '连接成功' : (normalized === 'otp_required' ? '请输入手机显示的 4 位验证码' : (normalized === 'scanned' ? (text && text.includes('手机验证码') ? text : '已扫码；如手机提示双重验证，请输入下方 4 位验证码。') : (text || ({ waiting:'等待微信扫码…', scanned:'已扫码，请在手机上确认登录…', success:'连接成功', expired:'二维码已过期', failed:'扫码连接失败' }[normalized]))));
    $('qr-message').textContent = ['failed', 'expired', 'otp_required'].includes(normalized) ? (text || '') : '';
    $('otp-form').hidden = !state.qrScanned || !['scanned', 'otp_required'].includes(normalized);
    if (!['scanned', 'otp_required'].includes(normalized)) { $('otp-message').textContent = ''; $('otp-message').hidden = true; }
    $('qr-refresh').hidden = !['expired', 'failed'].includes(normalized);
    $('qr-overlay').hidden = normalized === 'waiting' || normalized === 'success';
    if (normalized === 'success') $('qr-overlay-text').textContent = '连接成功';
    else if (normalized === 'expired') $('qr-overlay-text').textContent = '二维码已过期';
    else if (normalized === 'failed') $('qr-overlay-text').textContent = '扫码连接失败';
    else if (normalized === 'otp_required') $('qr-overlay').hidden = true;
    else $('qr-overlay-text').textContent = normalized === 'scanned' ? '请在手机上确认' : '正在生成二维码…';
  };
  const pollQrLogin = async (version) => {
    if (version !== state.qrVersion || !state.qrLoginId || state.qrPolling) return;
    state.qrPolling = true;
    try {
      const data = await api(state.qrStatusUrl || `/api/weread/qr/${encodeURIComponent(state.qrLoginId)}/status`, { timeoutMs: 70000 });
      if (version !== state.qrVersion) return;
      state.qrStatusFailures = 0;
      setQrStatus(data.state, data.message);
      if (data.state === 'success' && data.connected) {
        stopQrPolling(); state.qrLoginId = ''; state.qrStatusUrl = ''; state.connectionVersion++;
        await applyConnection(data.accounts || []);
      } else if (['waiting', 'scanned'].includes(data.state)) {
        state.qrTimer = setTimeout(() => pollQrLogin(version), 900);
      }
    } catch (error) {
      if (version !== state.qrVersion) return;
      state.qrStatusFailures += 1;
      const retryable = state.qrStatusFailures <= 2 && (!error.status || error.status === 404 || error.status >= 500);
      if (retryable) {
        setQrStatus('waiting', '扫码状态暂时不可用，正在重新生成二维码…');
        state.qrTimer = setTimeout(() => {
          if (version === state.qrVersion) startQrLogin();
        }, 700);
        return;
      }
      setQrStatus('failed', UI.friendlyError(error, '暂时无法获取扫码状态，请重新生成二维码。'));
    } finally { if (version === state.qrVersion) state.qrPolling = false; }
  };
  const startQrLogin = async (attempt = 0) => {
    stopQrPolling(); showScan();
    const version = state.qrVersion;
    state.qrScanned = false;
    state.qrStatusFailures = 0;
    $('qr-refresh').disabled = true; $('qr-refresh').hidden = true;
    $('qr-image').removeAttribute('src'); setQrStatus('waiting', '正在生成二维码…');
    $('otp-code').value = ''; $('otp-message').textContent = ''; $('otp-message').hidden = true;
    try {
      const data = await api('/api/weread/qr/start', { method: 'POST', timeoutMs: 30000 });
      if (version !== state.qrVersion) return;
      state.qrLoginId = data.login_id; state.qrStatusUrl = data.status_url;
      $('qr-image').src = `${data.image_url}?v=${encodeURIComponent(data.login_id)}`;
      $('qr-refresh').disabled = false; setQrStatus(data.state, data.message);
      await pollQrLogin(version);
    } catch (error) {
      if (version !== state.qrVersion) return;
      const retryable = attempt < 2 && (!error.status || error.status >= 500 || error.kind === 'qr_unavailable');
      if (retryable) {
        setQrStatus('waiting', `二维码生成较慢，正在重试（${attempt + 1}/2）…`);
        state.qrTimer = setTimeout(() => {
          if (version === state.qrVersion) startQrLogin(attempt + 1);
        }, 700);
        return;
      }
      $('qr-refresh').disabled = false; setQrStatus('failed', UI.friendlyError(error, '暂时无法生成微信读书二维码，请稍后重试。'));
    }
  };
  const cancelQrLogin = async () => {
    const loginId = state.qrLoginId; stopQrPolling(); state.qrLoginId = ''; state.qrStatusUrl = ''; state.qrScanned = false;
    if (!loginId) return;
    try { await api(`/api/weread/qr/${encodeURIComponent(loginId)}/cancel`, { method: 'POST' }); }
    catch (error) { /* The local view can still close if the task already expired. */ }
  };
  const renderJob = (job) => {
    state.job = job; const info = UI.jobSummary(job);
    if (info.terminal && job.download_ready && info.success && !state.packages.has(job.job_id)) state.packages.set(job.job_id, {count:info.success, downloaded:false});
    $('download-confirm').hidden = true; $('job-panel').hidden = false;
    $('download-title').textContent = info.terminal ? '导出完成' : '正在导出文章';
    $('download-sub').textContent = info.terminal ? `${info.success} 篇成功 · ${info.failed} 篇未成功` : '正在生成 Markdown 与 ZIP，可以关闭窗口继续选择文章。';
    $('job-total').textContent = info.total; $('job-success').textContent = info.success; $('job-failed').textContent = info.failed;
    $('progress').style.width = `${Math.min(100, Math.max(0, job.progress || 0))}%`;
    $('progress-track').setAttribute('aria-valuenow', String(job.progress || 0));
    $('job-status').textContent = info.terminal ? `已处理 ${info.processed} / ${info.total} 篇` : `正在导出 ${Math.min(info.processed + 1, info.total)} / ${info.total} 篇`;
    const list = $('job-items'); list.replaceChildren();
    for (const item of job.items || []) {
      const row = document.createElement('li'); row.className = `job-item ${item.status}`;
      const copy = document.createElement('span'); copy.className = 'item-copy'; copy.textContent = item.title;
      if (item.status === 'failed') { const reason = document.createElement('small'); reason.textContent = '暂时无法读取这篇文章，请重试。'; copy.append(reason); }
      const status = document.createElement('span'); status.className = 'item-status'; status.textContent = { queued:'等待中', running:'导出中', completed:'已完成', failed:'未成功' }[item.status] || '等待中';
      row.append(copy,status); list.append(row);
    }
    $('image-warning').hidden = !info.imageWarnings;
    $('image-warning').textContent = `图片说明：有 ${info.imageWarnings} 篇文章的图片未能下载，已保留原图链接，Markdown 中仍可查看原图。`;
    const previous = $('previous-downloads'); previous.replaceChildren();
    for (const [id, pack] of state.packages) {
      if (id === job.job_id || pack.downloaded) continue;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'button button-secondary button-small';
      button.textContent = `下载此前成功的 ${pack.count} 篇文章`;
      button.addEventListener('click', () => downloadPackage(id, button)); previous.append(button);
    }
    $('retry').hidden = !info.terminal || !job.failures?.length;
    $('download').hidden = !info.terminal || !job.download_ready || !info.success || state.packages.get(job.job_id)?.downloaded;
    updateSelection();
  };
  const pollJob = async (version) => {
    if (version !== state.pollVersion || !state.jobId || state.polling) return;
    state.polling = true;
    try {
      const job = await api(`/api/jobs/${state.jobId}`);
      if (version !== state.pollVersion) return;
      renderJob(job);
      if (checkJobExpiry(job)) return;
      message('job-message', ''); $('resume-poll').hidden = true;
      if (!UI.jobSummary(job).terminal) state.timer = setTimeout(() => pollJob(version), 900);
    } catch (error) {
      if (version !== state.pollVersion) return;
      handleError(error, 'job-message', '暂时无法查看下载进度，请点击“重新查看进度”。');
      $('resume-poll').hidden = false;
    } finally { if (version === state.pollVersion) state.polling = false; }
  };
  const startPolling = () => { stopPolling(); void pollJob(state.pollVersion); };
  const closeDownload = () => { $('download-dialog').close(); step(state.connected ? 2 : 1); };
  const startJob = async (retry = false) => {
    if (state.submitting) return;
    const connectionVersion = state.connectionVersion;
    const bookId = state.bookId;
    const articleIds = [...state.selected];
    const includeImages = $('include-images').checked;
    state.submitting = true; $('start-download').disabled = true; $('retry').disabled = true;
    $('start-download').textContent = '正在准备…'; message('job-message', '');
    try {
      const job = retry
        ? await api(`/api/jobs/${state.jobId}/retry`, { method:'POST' })
        : await api('/api/jobs', { method:'POST', body:JSON.stringify({book_id:bookId, article_ids:articleIds, include_images:includeImages}) });
      if (connectionVersion !== state.connectionVersion) return;
      if (!retry) state.jobBookId = bookId;
      if (retry) message('job-message','本次重试仅导出失败项，此前成功的文章可单独下载。');
      state.jobId = job.job_id; renderJob(job);
      if (!checkJobExpiry(job)) startPolling();
    } catch (error) {
      if (connectionVersion !== state.connectionVersion) return;
      $('download-confirm').hidden = true; $('job-panel').hidden = false;
      handleError(error, 'job-message', '暂时无法开始下载，请关闭窗口后重试。');
    } finally {
      state.submitting = false; $('start-download').disabled = false; $('retry').disabled = false;
      $('start-download').textContent = '开始下载'; updateSelection();
    }
  };
  $('scan-connect').addEventListener('click', async () => { await startQrLogin(); $('scan-cancel').focus(); });
  $('qr-refresh').addEventListener('click', startQrLogin);
  $('scan-cancel').addEventListener('click', async () => { await cancelQrLogin(); showConnection(); $('scan-connect').focus(); });
  $('otp-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const code = $('otp-code').value.trim();
    if (!/^\d{4}$/.test(code)) {
      $('otp-message').textContent = '请输入手机显示的 4 位数字验证码。'; $('otp-message').className = 'message error'; $('otp-message').hidden = false; $('otp-code').focus(); return;
    }
    const button = $('otp-submit'); if (button.disabled || !state.qrLoginId) return;
    button.disabled = true; button.textContent = '验证中…'; $('otp-message').textContent = ''; $('otp-message').hidden = true;
    try {
      const data = await api(`/api/weread/qr/${encodeURIComponent(state.qrLoginId)}/verify`, { method:'POST', body:JSON.stringify({code}) });
      $('otp-code').value = '';
      setQrStatus(data.state, data.message);
      if (['waiting', 'scanned'].includes(data.state) && !data.message?.includes('手机验证码')) {
        $('otp-message').textContent = '验证码已暂存，登录确认后会自动验证。'; $('otp-message').className = 'message'; $('otp-message').hidden = false;
      }
      if (data.state === 'success' && data.connected) {
        stopQrPolling(); state.qrLoginId = ''; state.qrStatusUrl = ''; state.connectionVersion++;
        await applyConnection(data.accounts || []);
      } else if (['waiting', 'scanned'].includes(data.state)) {
        await pollQrLogin(state.qrVersion);
      }
    } catch (error) {
      const text = UI.friendlyError(error, '验证码验证失败，请重新输入。');
      $('otp-message').textContent = text; $('otp-message').className = 'message error'; $('otp-message').hidden = false;
      if (error.kind === 'otp_expired' || error.kind === 'otp_not_required') setQrStatus(error.kind === 'otp_expired' ? 'expired' : 'failed', text);
      $('otp-code').focus();
    } finally { button.disabled = false; button.textContent = '验证'; }
  });
  $('connect-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const button = $('connect-submit'); if (button.disabled) return;
    button.disabled = true; button.textContent = '正在验证…'; message('connect-message', '正在连接微信读书，请稍候…');
    try {
      const data = await api('/api/weread/connect', { method:'POST', body:JSON.stringify({cookie:$('cookie').value}) });
      $('cookie').value = ''; await applyConnection(data.accounts);
    } catch (error) { handleError(error,'connect-message','暂时无法连接微信读书，请稍后重试。'); }
    finally { button.disabled = false; button.textContent = '验证并连接'; }
  });
  $('disconnect').addEventListener('click', async () => {
    $('disconnect').disabled = true;
    try {
      await api('/api/weread/connect',{method:'DELETE'}); state.connectionVersion++; stopPolling(); ++state.listVersion;
      connected(false); state.bookId = ''; state.cache.clear(); state.articles = []; state.selected.clear();
      state.job = null; state.jobId = ''; state.packages.clear(); state.expiredJobs.clear(); state.loading = false; $('cookie').value = '';
      $('advanced-connect').open = false; showConnection();
    } catch(error) { handleError(error,'message','暂时无法断开连接，请重试。'); }
    finally { $('disconnect').disabled = false; }
  });
  $('refresh-accounts').addEventListener('click', async () => {
    $('refresh-accounts').disabled = true;
    try {
      const data = await api('/api/accounts');
      state.accounts = data.accounts;
      const accountIds = new Set(state.accounts.map((account) => account.book_id));
      state.favorites = new Set([...state.favorites].filter((bookId) => accountIds.has(bookId)));
      UI.saveFavoriteAccounts(favoriteStorage, state.favorites);
      renderAccounts(); setMessage('公众号列表已更新。新关注的公众号需要重新连接后读取。');
    }
    catch(error) { handleError(error,'message','暂时无法更新公众号列表，请稍后重试。'); }
    finally { $('refresh-accounts').disabled = false; }
  });
  $('account-search').addEventListener('input', renderAccounts);
  $('accounts-all').addEventListener('click', () => { state.favoritesOnly = false; renderAccounts(); });
  $('accounts-favorites').addEventListener('click', () => { state.favoritesOnly = true; renderAccounts(); });
  $('load-articles').addEventListener('click', () => loadArticles(state.articleQuery));
  $('search-date').addEventListener('click', () => {
    const fromDate = $('from-date').value; const toDate = $('to-date').value;
    if (!fromDate && !toDate) {
      $('filter-message').textContent = '请先选择开始日期或结束日期。'; $('filter-message').hidden = false; $('from-date').focus(); return;
    }
    if (fromDate && toDate && fromDate > toDate) {
      $('filter-message').textContent = '结束日期不能早于开始日期，请调整日期范围。'; $('filter-message').hidden = false; $('to-date').focus(); return;
    }
    $('filter-message').hidden = true;
    loadArticles({ fromDate, toDate });
  });
  ['search','from-date','to-date'].forEach((id) => $(id).addEventListener('input',renderArticles));
  $('reset-filters').addEventListener('click',resetFilters);
  $('select-visible').addEventListener('change',(event) => { UI.updateVisibleSelection(state.selected, visible(), event.target.checked); renderArticles(); });
  $('clear-selection').addEventListener('click',() => { state.selected.clear(); renderArticles(); });
  $('empty-action').addEventListener('click',() => { if (state.articles.length) resetFilters(); else $('account-search').focus(); });
  $('export').addEventListener('click',() => {
    if (state.job && !UI.jobSummary(state.job).terminal) { renderJob(state.job); }
    else {
      $('download-title').textContent = `下载 ${state.selected.size} 篇文章`;
      $('download-sub').textContent = '文章将保存为 Markdown，并打包为 ZIP。';
      $('download-confirm').hidden = false; $('job-panel').hidden = true;
    }
    step(3); openDialog('download-dialog');
  });
  $('view-job').addEventListener('click', () => { if (state.job) { renderJob(state.job); step(3); openDialog('download-dialog'); } });
  $('cancel-download').addEventListener('click',closeDownload); $('close-download').addEventListener('click',closeDownload);
  $('download-dialog').addEventListener('cancel',() => step(state.connected ? 2 : 1));
  $('expired-dialog').addEventListener('cancel',(event) => event.preventDefault());
  $('start-download').addEventListener('click',() => startJob());
  $('retry').addEventListener('click',async () => {
    // A renewed connection clears the backend list: re-read the original account before retrying.
    if (!state.connected) { expired(); return; }
    if (state.needsJobRefresh && state.jobBookId) {
      const connectionVersion = state.connectionVersion;
      try {
        const query = state.cache.get(state.jobBookId)?.query || { fromDate: '', toDate: '' };
        await api(articleListPath(state.jobBookId, query), {timeoutMs:180000});
        if (connectionVersion !== state.connectionVersion) return;
        state.needsJobRefresh = false;
      }
      catch(error) { handleError(error,'job-message','暂时无法恢复原文章列表，请稍后重试。'); return; }
    }
    await startJob(true);
  });
  $('resume-poll').addEventListener('click',startPolling);
  const downloadPackage = async (jobId, button) => {
    if (state.downloading) return;
    state.downloading = true; button.disabled = true; const label = button.textContent; button.textContent = '正在下载…';
    try {
      const response = await fetch(`/api/jobs/${jobId}/download`, {credentials:'same-origin',cache:'no-store'});
      if (!response.ok) { const error = new Error(); error.status = response.status; throw error; }
      const url = URL.createObjectURL(await response.blob()); const link = document.createElement('a');
      link.href = url; link.download = 'weread-articles.zip'; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url),60000);
      if (state.packages.has(jobId)) state.packages.get(jobId).downloaded = true;
      message('job-message','下载已开始，文件可在浏览器下载记录中查看。'); button.hidden = true;
    } catch(error) { handleError(error,'job-message','下载文件可能已过期或已下载，请关闭窗口后重新导出。'); }
    finally { state.downloading = false; button.disabled = false; button.textContent = label; }
  };
  $('download').addEventListener('click', () => downloadPackage(state.jobId, $('download')));
  $('reconnect').addEventListener('click',() => {
    $('expired-dialog').close();
    state.expiredAuth = false; showConnection(); $('advanced-connect').open = false; startQrLogin();
  });
  api('/api/session').then(async (session) => {
    showApp(); if (session.connected) {
      try { await restoreConnection(); } catch(error) { handleError(error,'connect-message','暂时无法恢复连接，请重新连接。'); $('advanced-connect').open = true; }
    } else await startQrLogin();
  }).catch((error) => {
    message('connect-message',UI.friendlyError(error,'暂时无法连接服务，请刷新页面后重试。'),true);
  });
})();
