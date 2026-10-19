const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'app/templates/index.html'), 'utf8');
const script = fs.readFileSync(path.join(root, 'app/static/app.js'), 'utf8');
const response = (status, data) => ({ ok: status < 400, status, json: async () => data });
const flush = () => new Promise((resolve) => setImmediate(resolve));

async function setup(otherRequest, browserStorage = new Map()) {
  const elements = new Map();
  const makeElement = () => ({
    style: {}, children: [], hidden: false, disabled: false, value: '', textContent: '', listeners: new Map(),
    addEventListener(event, callback) { this.listeners.set(event, callback); },
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute() {}, removeAttribute() {}, focus() {},
    close() { this.open = false; }, showModal() { this.open = true; },
  });
  for (const tag of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
    elements.set(tag[1], {
      ...makeElement(),
      hidden: /\shidden(?:\s|>)/.test(tag[0]),
      listeners: new Map(),
      addEventListener(event, callback) { this.listeners.set(event, callback); },
    });
  }
  const requests = [];
  const localStorage = {
    getItem(key) { return browserStorage.get(key) || null; },
    setItem(key, value) { browserStorage.set(key, value); },
  };
  const timeouts = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    document: { getElementById: (id) => elements.get(id), createElement: makeElement },
    window: { ArchiveUI: require('../app/static/archive-ui.js'), localStorage },
    fetch: async (url, options) => {
      requests.push(url);
      if (url === '/api/session') return response(200, { authenticated: true, connected: false, account_count: 0 });
      if (otherRequest) return otherRequest(url, options);
      throw new Error(`Unexpected request: ${url}`);
    },
    AbortController, URLSearchParams, Error, TypeError,
    setTimeout(callback) { const id = ++nextTimer; timeouts.set(id, callback); return id; },
    clearTimeout(id) { timeouts.delete(id); },
  });
  vm.runInContext(script, context);
  await flush();
  return {
    elements, requests, timeouts, storage: browserStorage,
    fire: async (id, event = 'click', value = {}) => elements.get(id).listeners.get(event)({ preventDefault() {}, ...value }),
  };
}

test('anonymous session starts the real QR flow on page load', async () => {
  const ui = await setup((url) => {
    if (url === '/api/weread/qr/start') return response(200, {
      login_id: 'qr-test', state: 'waiting', message: '请使用微信扫描二维码。',
      expires_at: 1890000000, image_url: '/api/weread/qr/qr-test/image', status_url: '/api/weread/qr/qr-test/status',
    });
    if (url === '/api/weread/qr/qr-test/status') return response(200, {
      login_id: 'qr-test', state: 'success', message: '微信读书已连接。', connected: true,
      accounts: [{ book_id: 'MP_WXS_QR', title: '扫码公众号' }],
    });
    throw new Error(`Unexpected request: ${url}`);
  });
  await flush();
  assert.ok(ui.requests.includes('/api/session'));
  assert.ok(ui.requests.includes('/api/weread/qr/start'));
  assert.match(ui.elements.get('qr-status-text').textContent, /连接成功/);
  assert.equal(ui.elements.get('selection-panel').hidden, false);
});

test('scan login still pauses for the phone OTP and submits four digits', async () => {
  let verified = false;
  let statusPolls = 0;
  const ui = await setup((url, options) => {
    if (url === '/api/weread/qr/start') return response(200, {
      login_id: 'qr-otp', state: 'waiting', message: '请使用微信扫描二维码。',
      expires_at: 1890000000, image_url: '/api/weread/qr/qr-otp/image', status_url: '/api/weread/qr/qr-otp/status',
    });
    if (url === '/api/weread/qr/qr-otp/status') {
      statusPolls++;
      return response(200, verified ? {
        login_id: 'qr-otp', state: 'success', message: '微信读书已连接。', connected: true,
        accounts: [{ book_id: 'MP_WXS_OTP', title: '验证码公众号' }],
      } : statusPolls === 1 ? {
        login_id: 'qr-otp', state: 'waiting', message: '请使用微信扫描二维码。', connected: false,
      } : { login_id: 'qr-otp', state: 'scanned', message: '已扫码，正在等待微信读书确认。', connected: false });
    }
    if (url === '/api/weread/qr/qr-otp/verify') {
      assert.equal(JSON.parse(options.body).code, '1234');
      verified = true;
      return response(200, { login_id: 'qr-otp', state: 'scanned', message: '正在验证手机验证码…', connected: false });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  await flush();
  assert.equal(ui.elements.get('otp-form').hidden, true);
  const poll = [...ui.timeouts.values()][0];
  assert.ok(poll);
  poll();
  await flush();
  await flush();
  assert.equal(ui.elements.get('otp-form').hidden, false);
  assert.match(ui.elements.get('qr-status-text').textContent, /4 位验证码/);
  ui.elements.get('otp-code').value = '1234';
  await ui.fire('otp-form', 'submit');
  await flush();
  assert.ok(ui.requests.includes('/api/weread/qr/qr-otp/verify'));
  assert.equal(ui.elements.get('selection-panel').hidden, false);
  assert.equal(ui.elements.get('otp-form').hidden, true);
});

test('QR start retries a transient upstream failure before showing an error', async () => {
  let starts = 0;
  const ui = await setup((url) => {
    if (url === '/api/weread/qr/start') {
      starts++;
      if (starts === 1) return response(503, { detail: { kind: 'qr_unavailable', message: '暂时不可用' } });
      return response(200, {
        login_id: 'qr-retry', state: 'waiting', message: '请使用微信扫描二维码。',
        expires_at: 1890000000, image_url: '/api/weread/qr/qr-retry/image', status_url: '/api/weread/qr/qr-retry/status',
      });
    }
    if (url === '/api/weread/qr/qr-retry/status') return response(200, { login_id: 'qr-retry', state: 'waiting', connected: false });
    throw new Error(`Unexpected request: ${url}`);
  });
  const retry = [...ui.timeouts.values()][0];
  assert.ok(retry);
  retry();
  await flush();
  await flush();
  assert.equal(starts, 2);
  assert.match(ui.elements.get('qr-status-text').textContent, /等待|扫码/);
});
