// ── content.test.js — content.js 頁面級驗收（需要 jsdom）──────────────
//
// 執行：cd selekt/tests && npm i jsdom --no-save && node --test
//       或  NODE_PATH=<裝了 jsdom 的 node_modules> node --test selekt/tests/
//
// 在 jsdom 裡載入 market.js + content.js，模擬 Shopee 搜索頁，
// 驗證 hash 市場傳遞、各市場價格解析、缺價標記、無卡片失敗。

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch {
  // 沒裝 jsdom 時跳過整組（避免 CI 無此依賴而失敗）
  test('content.js 頁面測試（跳過：未安裝 jsdom）', { skip: true }, () => {});
  return;
}

const EXT = path.resolve(__dirname, '..', 'shopee-collector');
const marketSrc  = fs.readFileSync(path.join(EXT, 'market.js'), 'utf8');
const contentSrc = fs.readFileSync(path.join(EXT, 'content.js'), 'utf8');

function cardHtml({ name, price, sales, rating, href, withAria = true }) {
  const aria = withAria ? ` role="group" aria-label="Product card: ${name}"` : '';
  return `<li class="shopee-search-item-result__item">
    <a href="${href}">
      <div${aria}>
        <img src="https://cf.shopee.co.id/file/abc_tn" alt="${name}">
        <div class="line-clamp-2">${name}</div>
        ${price ? `<div class="price">${price}</div>` : ''}
        ${sales ? `<div class="sold">${sales}</div>` : ''}
        ${rating ? `<div class="rating">${rating}</div>` : ''}
      </div>
    </a>
  </li>`;
}

// 跑一個完整採集會話，回傳 content.js POST 給後端的 payload
async function runScenario(url, cards) {
  const dom = new JSDOM(
    `<!DOCTYPE html><html><body><ul id="list">${cards.join('')}</ul></body></html>`,
    { url, runScripts: 'outside-only', pretendToBeVisual: true }
  );
  const { window } = dom;
  const posted = [];

  window.chrome = {
    runtime: { onMessage: { addListener() {} }, sendMessage() {} },
  };
  window.fetch = async (u, opts) => {
    posted.push(JSON.parse(opts.body));
    return { ok: true, status: 200, json: async () => ({ saved: 0 }) };
  };
  window.scrollBy = () => {};

  // 把 1.5s 的滚动等待压到 0，测试才不会跑十几秒
  const realSetTimeout = window.setTimeout;
  window.setTimeout = (fn, ms, ...rest) =>
    realSetTimeout(fn, ms >= 1000 ? 0 : ms, ...rest);

  window.eval(marketSrc);
  window.eval(contentSrc);

  for (let i = 0; i < 200 && !posted.length; i++) {
    await new Promise(r => realSetTimeout(r, 10));
  }
  await new Promise(r => realSetTimeout(r, 50));
  dom.window.close();

  assert.strictEqual(posted.length, 1, 'content.js 应回传一次');
  return posted[0];
}

test('印尼站：hash 传入 market=id，Rp 价格与印尼数字惯例', async () => {
  const payload = await runScenario(
    'https://shopee.co.id/search?keyword=sepatu#selekt-task-42&market=id',
    [
      cardHtml({ name: 'Sepatu Kulit Pria', price: 'Rp 125.000', sales: 'Terjual 1,2rb',
                 rating: '4,9 (1,2rb)', href: 'https://shopee.co.id/product/1/2' }),
      cardHtml({ name: 'Tas Ransel', price: 'Rp 89.900', sales: 'Terjual 350',
                 rating: '4,8 (120)', href: 'https://shopee.co.id/product/1/3' }),
      cardHtml({ name: 'Topi', price: 'Rp 25.000', sales: 'Terjual 10',
                 rating: '4,5 (8)', href: 'https://shopee.co.id/product/1/4' }),
      cardHtml({ name: 'Tanpa Harga', price: '', sales: 'Terjual 5', rating: '',
                 href: 'https://shopee.co.id/product/1/5' }),
    ]
  );

  assert.strictEqual(payload.task_id, 42);
  assert.strictEqual(payload.market, 'id');
  assert.strictEqual(payload.error, null);
  assert.strictEqual(payload.products.length, 4);

  const first = payload.products[0];
  assert.strictEqual(first.name, 'Sepatu Kulit Pria');
  assert.strictEqual(first.price, 125000);
  assert.strictEqual(first.sales, 1200);
  assert.strictEqual(first.rating, 4.9);
  assert.ok(first.href.includes('/product/1/2'));

  // 缺价商品：price=null + missing 标记 + 备注，不是 0
  const noPrice = payload.products[3];
  assert.strictEqual(noPrice.price, null);
  assert.ok(noPrice.missing.includes('price'));
  assert.ok(noPrice.notes.some(n => n.includes('價格缺失')));
});

test('相邻区块拼接时，销量不吞掉评分尾数', async () => {
  // textContent 无分隔符会把 "Terjual 10" 与 "4,5 (8)" 拼成 "Terjual 104,5 (8)"
  const payload = await runScenario(
    'https://shopee.co.id/search?keyword=topi#selekt-task-43&market=id',
    [
      cardHtml({ name: 'Topi', price: 'Rp 25.000', sales: 'Terjual 10',
                 rating: '4,5 (8)', href: 'https://shopee.co.id/product/1/4' }),
      cardHtml({ name: 'Kaos', price: 'Rp 55.000', sales: 'Terjual 200',
                 rating: '4,8 (60)', href: 'https://shopee.co.id/product/1/5' }),
      cardHtml({ name: 'Jaket', price: 'Rp 199.000', sales: 'Terjual 1,5rb',
                 rating: '4,9 (1,2rb)', href: 'https://shopee.co.id/product/1/6' }),
    ]
  );
  assert.strictEqual(payload.products[0].sales, 10);
  assert.strictEqual(payload.products[0].rating, 4.5);
  assert.strictEqual(payload.products[0].ratingCount, 8);
  assert.strictEqual(payload.products[2].sales, 1500);
  assert.strictEqual(payload.products[2].price, 199000);
});

test('市场以传入值为准：hash=id 落在 shopee.tw 域名', async () => {
  const payload = await runScenario(
    'https://shopee.tw/search?keyword=x#selekt-task-7&market=id',
    [
      cardHtml({ name: 'A', price: 'NT$120', sales: '已售出 5', rating: '4.8 (10)', href: 'https://shopee.tw/product/1/2' }),
      cardHtml({ name: 'B', price: 'NT$130', sales: '已售出 6', rating: '4.8 (10)', href: 'https://shopee.tw/product/1/3' }),
      cardHtml({ name: 'C', price: 'NT$140', sales: '已售出 7', rating: '4.8 (10)', href: 'https://shopee.tw/product/1/4' }),
    ]
  );
  assert.strictEqual(payload.market, 'id');
  // id 市场不认 NT$ → 不得臆测成数字
  assert.strictEqual(payload.products[0].price, null);
});

test('旧任务（hash 无 market）：按域名推断为泰国站', async () => {
  const payload = await runScenario(
    'https://shopee.co.th/search?keyword=bag#selekt-task-9',
    [
      cardHtml({ name: 'กระเป๋า', price: '฿1,250', sales: 'ขายแล้ว 1.2k',
                 rating: '4.7 (500)', href: 'https://shopee.co.th/product/1/2' }),
      cardHtml({ name: 'รองเท้า', price: '฿890', sales: 'ขายแล้ว 300',
                 rating: '4.6 (90)', href: 'https://shopee.co.th/product/1/3' }),
      cardHtml({ name: 'หมวก', price: '฿250', sales: 'ขายแล้ว 12',
                 rating: '4.5 (8)', href: 'https://shopee.co.th/product/1/4' }),
    ]
  );
  assert.strictEqual(payload.market, 'th');
  assert.strictEqual(payload.products[0].price, 1250);
  assert.strictEqual(payload.products[0].sales, 1200);
  assert.strictEqual(payload.products[0].name, 'กระเป๋า');
});

test('越南站：₫ 后缀价格', async () => {
  const payload = await runScenario(
    'https://shopee.vn/search?keyword=giay#selekt-task-11&market=vn',
    [
      cardHtml({ name: 'Giày thể thao', price: '120.000 ₫', sales: 'Đã bán 2,1k',
                 rating: '4.8 (1,2k)', href: 'https://shopee.vn/product/1/2' }),
      cardHtml({ name: 'Túi xách', price: '250.000 ₫', sales: 'Đã bán 400',
                 rating: '4.7 (60)', href: 'https://shopee.vn/product/1/3' }),
      cardHtml({ name: 'Mũ', price: '50.000 ₫', sales: 'Đã bán 30',
                 rating: '4.5 (10)', href: 'https://shopee.vn/product/1/4' }),
    ]
  );
  assert.strictEqual(payload.market, 'vn');
  assert.strictEqual(payload.products[0].price, 120000);
  assert.strictEqual(payload.products[0].sales, 2100);
});

test('无商品卡片：回传 error，供后端结算失败', async () => {
  const payload = await runScenario(
    'https://shopee.tw/search?keyword=zzz#selekt-task-13&market=tw',
    []
  );
  assert.strictEqual(payload.task_id, 13);
  assert.strictEqual(payload.error, 'no_cards_found');
  assert.strictEqual(payload.products.length, 0);
  assert.strictEqual(payload.market, 'tw');
});

test('DOM 兜底：无 aria-label 时仍取到商品名与链接', async () => {
  const payload = await runScenario(
    'https://shopee.com.my/search?keyword=bag#selekt-task-15&market=my',
    [
      cardHtml({ name: 'Beg Tangan Kulit', price: 'RM45.90', sales: '1.5k sold',
                 rating: '4.8 (1.2k)', href: 'https://shopee.com.my/product/1/2', withAria: false }),
      cardHtml({ name: 'Beg Sandang', price: 'RM32.00', sales: '200 sold',
                 rating: '4.7 (40)', href: 'https://shopee.com.my/product/1/3', withAria: false }),
      cardHtml({ name: 'Topi', price: 'RM15.50', sales: '25 sold',
                 rating: '4.5 (9)', href: 'https://shopee.com.my/product/1/4', withAria: false }),
    ]
  );
  assert.strictEqual(payload.market, 'my');
  assert.strictEqual(payload.products.length, 3);
  assert.strictEqual(payload.products[0].name, 'Beg Tangan Kulit');
  assert.ok(payload.products[0].href.includes('/product/1/2'));
  assert.strictEqual(payload.products[0].price, 45.9);
});
