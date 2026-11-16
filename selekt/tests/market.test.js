// ── market.test.js — 市場／幣種／派發 URL 驗收（零依賴，node --test）──────
//
// 執行：node --test selekt/tests/
//
// 覆蓋：
//   1. 8 個市場的本地幣種符號解析（Rp / ฿ / ₫ / RM / ₱ / $ / R$ / NT$ / S$）
//   2. 各市場千位/小數慣例（id/th/vn/br 用「.」分千位、「,」分小數）
//   3. 價格解析不到時回傳 null（不是 0）
//   4. 市場優先級：hash 傳入 → 域名推斷 → 台灣站
//   5. background.js 派發的 URL 同時帶上正確域名與 market

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const EXT = path.resolve(__dirname, '..', 'shopee-collector');
const M = require(path.join(EXT, 'market.js'));

test('價格：各市場本地幣種符號', () => {
  const cases = [
    ['id', 'Rp 12.345', 12345],
    ['id', 'Rp12.345', 12345],
    ['id', 'Rp 1.234.567', 1234567],
    ['id', 'Rp12.345,00', 12345],
    ['th', '฿1,250', 1250],
    ['th', '1,250 ฿', 1250],
    ['vn', '120.000 ₫', 120000],
    ['vn', '₫120.000', 120000],
    ['my', 'RM12.90', 12.9],
    ['ph', '₱1,299', 1299],
    ['tw', 'NT$1,200', 1200],
    ['tw', '$99', 99],
    ['sg', 'S$12.50', 12.5],
    ['br', 'R$ 19,90', 19.9],
    ['br', 'R$ 1.299,90', 1299.9],
  ];
  for (const [market, text, want] of cases) {
    assert.strictEqual(M.parsePriceForMarket(text, market), want, `${market} "${text}"`);
  }
});

test('價格：不會把後面的銷量數字併進價格', () => {
  // 卡片文字常見「RM45.90 1.5k sold」，貪婪匹配會得到 45.901
  assert.strictEqual(M.parsePriceForMarket('RM45.90 1.5k sold', 'my'), 45.9);
  assert.strictEqual(M.parsePriceForMarket('Rp 25.000 1,2rb Terjual', 'id'), 25000);
  assert.strictEqual(M.parsePriceForMarket('₱1,299 2.3k sold', 'ph'), 1299);
  assert.strictEqual(M.parsePriceForMarket('120.000 ₫ Đã bán 2,1k', 'vn'), 120000);
  assert.strictEqual(M.parsePriceForMarket('฿890 1.2k ขายแล้ว', 'th'), 890);
});

test('價格：解析不到時回傳 null，不回傳 0', () => {
  for (const [market, text] of [
    ['id', 'Produk 已售 5'],
    ['tw', '無價格資訊'],
    ['th', '฿'],
    ['my', 'RM'],
    ['vn', '₫'],
    ['ph', ''],
  ]) {
    assert.strictEqual(M.parsePriceForMarket(text, market), null, `${market} "${text}"`);
  }
});

test('價格：市場不認的幣種符號不誤讀（id 市場的 NT$）', () => {
  assert.strictEqual(M.parsePriceForMarket('NT$120', 'id'), null);
});

test('銷量：多語言標記與單位', () => {
  const cases = [
    ['id', 'Terjual 1,2rb', 1200],
    ['id', '10 rb terjual', 10000],
    ['id', 'Terjual 2 jt', 2000000],
    ['th', 'ขายแล้ว 1.2k', 1200],
    ['vn', 'Đã bán 1,2k', 1200],
    ['tw', '已售出 1.2萬', 12000],
    ['my', '1.5k sold', 1500],
    ['ph', '2.3k sold', 2300],
  ];
  for (const [market, text, want] of cases) {
    assert.strictEqual(M.parseSalesForMarket(text, market), want, `${market} "${text}"`);
  }
  assert.strictEqual(M.parseSalesForMarket('沒有任何銷量字樣', 'tw'), null);
});

test('評分與評論數：各市場小數符號', () => {
  assert.deepStrictEqual(
    M.parseRatingForMarket('4.8 (1.2k)', 'tw'),
    { rating: 4.8, ratingCount: 1200, matched: '4.8 (1.2k)' });
  assert.deepStrictEqual(
    M.parseRatingForMarket('4,9 (1,2rb)', 'id'),
    { rating: 4.9, ratingCount: 1200, matched: '4,9 (1,2rb)' });
  assert.deepStrictEqual(
    M.parseRatingForMarket('4.7 (500)', 'th'),
    { rating: 4.7, ratingCount: 500, matched: '4.7 (500)' });
});

test('評分片段可被摘除，避免銷量把評分尾數吃進數字', () => {
  // textContent 會把相鄰區塊直接拼接："Terjual 10" + "4,5 (8)"
  const raw = 'TopiRp 25.000Terjual 104,5 (8)';
  const info = M.parseRatingForMarket(raw, 'id');
  assert.strictEqual(info.rating, 4.5);
  assert.strictEqual(info.ratingCount, 8);
  const rest = raw.replace(info.matched, ' ');
  assert.strictEqual(M.parseSalesForMarket(rest, 'id'), 10);
  assert.strictEqual(M.parsePriceForMarket(rest, 'id'), 25000);
});

test('市場優先級：hash 傳入 → 域名 → 台灣站', () => {
  // 傳入市場優先，即使域名不同（Shopee 跳轉/別名域名）
  assert.strictEqual(M.resolveMarket('#selekt-task-7&market=id', 'shopee.tw'), 'id');
  assert.strictEqual(M.resolveMarket('#selekt-task-7&market=id', 'shopee.co.id'), 'id');
  // hash 無 market（舊任務）→ 域名推斷
  assert.strictEqual(M.resolveMarket('#selekt-task-7', 'shopee.co.th'), 'th');
  assert.strictEqual(M.resolveMarket('', 'shopee.vn'), 'vn');
  // 未知 hash 值 → 退回域名
  assert.strictEqual(M.resolveMarket('#selekt-task-7&market=xx', 'shopee.ph'), 'ph');
  // 都無法判定 → 台灣站
  assert.strictEqual(M.resolveMarket('', 'example.com'), 'tw');
  assert.strictEqual(M.resolveMarket('#selekt-task-7', ''), 'tw');
});

test('域名推斷：8 個市場與子域名', () => {
  const cases = [
    ['shopee.tw', 'tw'], ['shopee.sg', 'sg'], ['shopee.com.my', 'my'],
    ['shopee.co.id', 'id'], ['shopee.co.th', 'th'], ['shopee.ph', 'ph'],
    ['shopee.vn', 'vn'], ['shopee.com.br', 'br'],
    ['www.shopee.co.id', 'id'],
    ['shopee.com', null], ['evil-shopee.tw.attacker.com', null],
  ];
  for (const [host, want] of cases) {
    assert.strictEqual(M.detectMarketFromHostname(host), want, host);
  }
});

// ── background.js 派發 URL ─────────────────────────────────────────
async function dispatchUrl(task) {
  const src = fs.readFileSync(path.join(EXT, 'background.js'), 'utf8');
  const created = [];
  const sandbox = {
    console: { log() {}, error() {} },
    setInterval: () => 0,
    clearInterval: () => {},
    chrome: {
      runtime: { onInstalled: { addListener() {} }, onStartup: { addListener() {} },
                 onAlarm: { addListener() {} }, onConnect: { addListener() {} },
                 onMessage: { addListener() {} } },
      alarms: { create() {}, onAlarm: { addListener() {} } },
      tabs: { create: async (o) => { created.push(o.url); return { id: 1 }; },
              remove: async () => {} },
    },
    fetch: async (url) => {
      if (url.includes('/collect/pending-tasks')) {
        return { ok: true, json: async () => ({ tasks: [task] }) };
      }
      return { ok: true, json: async () => ({ ok: true }) };
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);
  await sandbox.pollAndCollect();
  return created;
}

test('派發：URL 同時帶正確域名與 market 代碼', async () => {
  const cases = [
    [{ id: 5, keyword: 'sepatu', market: 'id' },
     'https://shopee.co.id/search?keyword=sepatu#selekt-task-5&market=id'],
    [{ id: 7, keyword: 'a b', market: 'vn' },
     'https://shopee.vn/search?keyword=a%20b#selekt-task-7&market=vn'],
    [{ id: 8, keyword: 'x' },
     'https://shopee.tw/search?keyword=x#selekt-task-8&market=tw'],
    [{ id: 9, keyword: 'x', market: 'bogus' },
     'https://shopee.tw/search?keyword=x#selekt-task-9&market=tw'],
  ];
  for (const [task, want] of cases) {
    const urls = await dispatchUrl(task);
    assert.deepStrictEqual(urls, [want], JSON.stringify(task));
  }
});

test('派發：URL 能被頁面端解析回同一市場', async () => {
  for (const market of ['tw', 'sg', 'my', 'id', 'th', 'ph', 'vn', 'br']) {
    const [url] = await dispatchUrl({ id: 1, keyword: 'k', market });
    const u = new URL(url);
    assert.strictEqual(M.resolveMarket(u.hash, u.hostname), market);
  }
});
