// Shopee 選品採集工具 - Content Script (v2 自主採集版)
// 在用戶已開啟 / BG 自動開啟的 Shopee 搜索頁中讀取商品 DOM 數據

(function () {
  'use strict';

  if (window.__shopeeCollectorLoaded) return;
  window.__shopeeCollectorLoaded = true;

  const _BACKEND = 'http://localhost:8000/api';

  // ════════════════════════════════════════════════════════════════
  // ██  市場解析（共用 market.js：域名 / 幣種符號 / 數字慣例 / 價格分段）
  // ════════════════════════════════════════════════════════════════
  // market.js 由 manifest.json 先行載入，掛在 window.SelektMarket。
  const M = window.SelektMarket;
  if (!M) {
    console.error('[SELEKT] market.js 未載入，無法識別市場與價格，中止採集');
    return;
  }
  const {
    DEFAULT_MARKET,
    normalizeMarketCode,
    getMarketProfile,
    resolveMarket,
    parsePriceForMarket,
    parseSalesForMarket,
    parseRatingForMarket,
  } = M;


  // ── 商品卡片選擇器（waitForCards / 滾動累積共用）───────────────
  const CARD_SELECTORS = [
    'li.shopee-search-item-result__item',
    'div[data-sqe="item"]',
    'div.col-xs-2-4.shopee-search-item-result__item',
    'li[class*="col-xs"]',
  ];

  function findCardElements() {
    let best = null;
    for (const sel of CARD_SELECTORS) {
      const els = document.querySelectorAll(sel);
      if (els.length > 2) return els;
      if (els.length && (!best || els.length > best.length)) best = els;
    }
    if (best) return best;
    const fallback = document.querySelectorAll('a[href*="/product/"]');
    return fallback.length ? fallback : null;
  }

  // ── 等待商品卡片（CSR 頁面非同步渲染）────────────────────────────
  // 3 張以上視為渲染完成；只有 1–2 張時給 800ms 寬限，
  // 寬限後仍只有少量卡片就照實回傳，避免小結果集被誤判為「無卡片」。
  function waitForCards(timeout = 12000) {
    return new Promise((resolve) => {
      let done = false;
      let observer = null;
      let graceTimer = null;

      function finish(cards) {
        if (done) return;
        done = true;
        if (observer) observer.disconnect();
        if (graceTimer) clearTimeout(graceTimer);
        resolve(cards && cards.length ? cards : null);
      }

      function armGrace() {
        if (graceTimer || done) return;
        graceTimer = setTimeout(() => finish(findCardElements()), 800);
      }

      const existing = findCardElements();
      if (existing && existing.length > 2) { resolve(existing); return; }
      if (existing) armGrace();

      const deadline = Date.now() + timeout;
      observer = new MutationObserver(() => {
        const cards = findCardElements();
        if (cards && cards.length > 2) finish(cards);
        else if (cards) armGrace();
        if (Date.now() > deadline) finish(cards);
      });
      if (document.body) observer.observe(document.body, { childList: true, subtree: true });
      setTimeout(() => finish(findCardElements()), timeout);
    });
  }

  // ── 選品打分 ─────────────────────────────────────────────────────
  // 價格分段依市場讀取（與後端 markets.py 的 price_tiers 對齊）；
  // 缺價 / 缺銷量時不計該維度分數並標註，而不是當成 0 元、0 銷量商品打分。
  function scoreProduct(product, market) {
    let score = 0;
    const notes = [];

    const salesMissing = product.sales == null;
    const sales = salesMissing ? 0 : product.sales;
    if (salesMissing)      {              notes.push('⚠ 銷量缺失，未計入銷量評分'); }
    else if (sales >= 1000) { score += 15; }
    else if (sales >= 500) { score += 12; notes.push('銷量中等'); }
    else if (sales >= 100) { score += 8;  notes.push('銷量偏低'); }
    else if (sales >= 10)  { score += 4;  notes.push('銷量很低'); }
    else                   {              notes.push('銷量極少，風險高'); }

    const rating = product.rating || 0;
    const ratingCount = product.ratingCount || 0;
    if (rating >= 4.7 && ratingCount >= 50)      { score += 15; }
    else if (rating >= 4.5 && ratingCount >= 20) { score += 12; }
    else if (rating >= 4.0)                       { score += 8;  notes.push('評分一般'); }
    else if (rating >= 3.0)                       { score += 4;  notes.push('評分偏低'); }
    else                                          {              notes.push('評分差或無評分'); }

    const name = (product.name || '').toLowerCase();
    const hasVariant = /組合|套裝|多入|多色|多尺寸|pack|set|combo/.test(name);
    score += hasVariant ? 10 : 5;
    if (!hasVariant) notes.push('建議增加組合規格');

    const [low, midLow, mid, high] = getMarketProfile(market).priceTiers;
    const price = product.price;
    if (price == null)                          { notes.push('⚠ 價格缺失，未計入價格評分'); }
    else if (price >= midLow && price <= mid)   { score += 15; }
    else if (price >= low && price < midLow)    { score += 10; notes.push('低價區間競爭較激烈'); }
    else if (price > mid && price <= high)      { score += 12; }
    else if (price < low)                       { score += 3;  notes.push('超低價紅海，利潤風險高'); }
    else                                        { score += 8;  notes.push('高價品需強視覺支撐'); }

    score += 10;
    notes.push('圖片品質請人工確認');

    const isRisky = /玻璃|電器|電子|插頭|食品|飲料|刀|剪/.test(name);
    score += isRisky ? 2 : 9;
    if (isRisky) notes.push('⚠ 品類有風險，謹慎評估');

    score += 8;
    const hasLegalRisk = /認證|3C|安規|接觸食品|嬰兒|兒童/.test(name);
    score += hasLegalRisk ? 2 : 8;
    if (hasLegalRisk) notes.push('⚠ 可能需認證，確認法規');

    let verdict = '直接放棄';
    if (score >= 80)      verdict = '強烈推薦';
    else if (score >= 70) verdict = '可以測款';
    else if (score >= 60) verdict = '謹慎考慮';

    return { score, verdict, notes };
  }

  // ── 取「第一個有內容」的元素：依優先順序逐一嘗試選擇器（名稱抓不到 aria-label 時的備援）──
  function queryFirst(card, selectors) {
    for (const sel of selectors) {
      const el = card.querySelector(sel);
      if (el && el.textContent.trim()) return el;
    }
    return null;
  }

  let _debugLogged = false;

  // ── DOM 解析 ─────────────────────────────────────────────────────
  // Shopee PC 商城用 Tailwind 動態 class，無法靠 class 名稱穩定定位欄位，
  // 改用：① 卡片 [role="group"][aria-label="Product card: 商品名"] 取真實商品名
  //       ② 依市場識別幣種符號與數字慣例，正則掃描卡片全文字
  // 解析不到的欄位保留 null 並列入 missing，不以 0 冒充正常數據。
  function parseCards(cards, market) {
    const results = [];
    const marketCode = normalizeMarketCode(market) || DEFAULT_MARKET;

    cards.forEach((card, idx) => {
      try {
        // ① 商品名：優先用 aria-label（穩定，不會抓到優惠角標）
        let name = '';
        const group = card.querySelector('[role="group"][aria-label]');
        if (group) {
          name = (group.getAttribute('aria-label') || '')
            .replace(/^Product card:\s*/i, '')
            .trim();
        }
        if (!name) {
          const nameEl = queryFirst(card, [
            '[data-sqe="name"]', '.Cve6sh', '._1NoI8_',
            'div[class*="name"]', 'span[class*="name"]', 'div[class*="ellipsis"]',
          ]);
          if (nameEl) name = nameEl.textContent.trim();
        }
        if (!name) {
          // 再退一層：任意帶 aria-label / title 的元素、圖片 alt
          const labelled = card.querySelector('[aria-label]:not([aria-label=""])');
          if (labelled) {
            name = (labelled.getAttribute('aria-label') || '')
              .replace(/^Product card:\s*/i, '')
              .trim();
          }
          if (!name) {
            const imgEl = card.querySelector('img[alt]');
            if (imgEl) name = (imgEl.getAttribute('alt') || '').trim();
          }
          if (!name) {
            const titled = card.querySelector('[title]:not([title=""])');
            if (titled) name = (titled.getAttribute('title') || '').trim();
          }
        }

        // ② 卡片全文字（壓縮多餘空白），依市場抓價格/銷量/評分
        //    DOM 的 textContent 會把相鄰區塊直接拼在一起
        //    （"Terjual 10" + "4,5 (8)" → "Terjual 104,5 (8)"），
        //    所以先摘出評分片段，再用剩下的文字解析價格與銷量。
        const text = (card.textContent || '').replace(/\s+/g, ' ').trim();
        const ratingInfo = parseRatingForMarket(text, marketCode);
        const rest = ratingInfo.matched ? text.replace(ratingInfo.matched, ' ') : text;

        // 商品名最後兜底：取卡片內第一個「像商品名」的葉節點文字
        // （只看無子元素的節點，避免把「名稱 + 價格 + 銷量」的整塊容器當成商品名；
        //   再排除純價格 / 純銷量 / 純評分 / 純數字）
        if (!name) {
          const candidates = card.querySelectorAll('div, span, p, h3, h4, a, img');
          for (const el of candidates) {
            if (el.children && el.children.length) continue;   // 只取葉節點
            let t = '';
            if (el.tagName === 'IMG') {
              t = (el.getAttribute('alt') || '').trim();
            } else {
              t = (el.textContent || '').replace(/\s+/g, ' ').trim();
            }
            if (t.length < 3 || t.length > 200) continue;
            if (!/[A-Za-zÀ-ɏ一-鿿฀-๿Ā-ỹ]/.test(t)) continue;
            if (/^[\d.,\s%+-]+$/.test(t)) continue;
            if (/^[0-5][.,]\d$/.test(t)) continue;                      // 純評分
            if (parsePriceForMarket(t, marketCode) != null) continue;   // 純價格
            if (parseSalesForMarket(t, marketCode) != null) continue;   // 純銷量
            name = t;
            break;
          }
        }
        if (!name) return;

        const price = parsePriceForMarket(rest, marketCode);          // null = 解析不出
        const sales = parseSalesForMarket(rest, marketCode);          // null = 解析不出
        const { rating, ratingCount } = ratingInfo;

        const linkEl = card.closest('a[href*="/product/"]')
                    || card.closest('a[href*="shopee."]')
                    || card.querySelector('a[href*="shopee."]')
                    || card.querySelector('a[href]')
                    || card.querySelector('a');
        const imgEl  = card.querySelector('img');
        const href  = linkEl ? (linkEl.href || linkEl.getAttribute('href') || '') : '';
        const image = imgEl  ? (imgEl.src || imgEl.dataset.src || '') : '';

        const missing = [];
        if (price == null) missing.push('price');
        if (sales == null) missing.push('sales');

        if (!_debugLogged && idx < 3) {
          console.log('[SELEKT][DEBUG]', JSON.stringify({
            market: marketCode, name, price, sales, rating, ratingCount, missing,
            text: text.slice(0, 200),
          }));
          if (idx === 2) _debugLogged = true;
        }

        const product = {
          idx: idx + 1, name, price, sales, rating, ratingCount,
          href, image, market: marketCode, missing,
        };
        results.push({ ...product, ...scoreProduct(product, marketCode) });
      } catch (_) {}
    });

    return results;
  }

  // ════════════════════════════════════════════════════════════════
  // ██  後端採集模式（自主執行，不依賴 BG 的長時 await）
  // ════════════════════════════════════════════════════════════════
  // BG 開啟此 Tab 時會在 URL hash 加入 #selekt-task-{id}&market={market}
  // content.js 偵測到後，自行完成：等待 → 解析 → POST 後端 → 通知 BG 關閉
  const _taskMatch = location.hash.match(/#selekt-task-(\d+)/);
  if (_taskMatch) {
    const _taskId  = parseInt(_taskMatch[1]);
    const _keyword = new URLSearchParams(location.search).get('keyword') || '';
    // 市場優先級：派發端傳入 → 域名推斷 → 台灣站（與手動模式共用 resolveMarket）
    const _market = resolveMarket(location.hash, location.hostname);

    console.log(`[SELEKT] 自主採集模式 Task#${_taskId} 關鍵字: ${_keyword} market=${_market}`);

    (async () => {
      let products = [];
      let error    = null;

      try {
        // 等待商品卡片（最多 12 秒，因為 Shopee React 有時很慢）
        const cards = await waitForCards(12000);

        if (!cards || cards.length === 0) {
          // 沒有任何商品卡片 → 回傳 error，讓任務結算為 failed（而非成功 0 件）
          error = 'no_cards_found';
          console.warn(`[SELEKT] Task#${_taskId} 未找到商品卡片，任務將標記為失敗`);
        } else {
          // 滾動觸發 Shopee 無限滾動懶加載 + 每一步都立刻解析並累積
          // （Shopee 列表為虛擬滾動，滾走的卡片會被卸載，
          //  若只在最後查一次 DOM，最上面那批商品早就被卸載、抓不到）
          console.log(`[SELEKT] Task#${_taskId} 開始分段滾動加載更多商品...`);

          const _seen = new Map(); // key: href || name+price，跨多次滾動去重
          function collectCurrentBatch() {
            const els = findCardElements() || cards;
            for (const p of parseCards(els, _market)) {
              const key = p.href || `${p.name}__${p.price == null ? 'na' : p.price}`;
              if (!_seen.has(key)) _seen.set(key, p);
            }
          }

          // 初始批次（滾動前先抓一次，確保第一畫面的商品不漏）
          collectCurrentBatch();

          const SCROLL_TIMES = 8;
          const SCROLL_DELAY = 1500; // ms，等 Shopee XHR 返回並渲染
          for (let _i = 0; _i < SCROLL_TIMES; _i++) {
            // 每次只往下滾一個畫面高度，而非直接跳到頁尾，
            // 讓懶加載逐批渲染，避免一次性虛擬卸載掉上方卡片
            window.scrollBy({ top: window.innerHeight * 0.85, behavior: 'smooth' });
            await new Promise(r => setTimeout(r, SCROLL_DELAY));
            collectCurrentBatch();
            console.log(`[SELEKT] 滾動 ${_i + 1}/${SCROLL_TIMES}，累積 ${_seen.size} 件`);
          }

          products = Array.from(_seen.values());
          console.log(`[SELEKT] Task#${_taskId} 滾動後解析完成 ${products.length} 件`);

          if (products.length === 0) {
            // 卡片存在但全部解析失敗（例如卡片裡連商品名都取不到）
            error = 'no_products_parsed';
          }
        }

        // 直接 POST 後端（content script 有 localhost:8000 權限）
        const resp = await fetch(`${_BACKEND}/collect/from-extension`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ task_id: _taskId, keyword: _keyword, products, error, market: _market })
        });

        if (!resp.ok) {
          console.error(`[SELEKT] Task#${_taskId} 回傳後端失敗 HTTP ${resp.status}`);
        } else {
          console.log(`[SELEKT] Task#${_taskId} ✅ 完成，共 ${products.length} 件 market=${_market}`);
        }
      } catch (e) {
        console.error(`[SELEKT] Task#${_taskId} 例外錯誤`, e);
        // 嘗試把錯誤回傳後端
        try {
          await fetch(`${_BACKEND}/collect/from-extension`, {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ task_id: _taskId, keyword: _keyword, products: [], error: String(e), market: _market })
          });
        } catch {}
      } finally {
        // 通知 BG 關閉此 Tab
        try {
          chrome.runtime.sendMessage({ action: 'CLOSE_TAB' });
        } catch {}
      }
    })();
  }

  // ════════════════════════════════════════════════════════════════
  // ██  手動採集模式（用戶在 Shopee 手動點擊擴充按鈕時）
  // ════════════════════════════════════════════════════════════════
  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.action === 'PING') {
      sendResponse({ ok: true });
      return false;
    }
    if (msg.action === 'COLLECT') {
      (async () => {
        try {
          const market = resolveMarket(location.hash, location.hostname);
          const cards = await waitForCards(8000);
          if (!cards || cards.length === 0) {
            sendResponse({ ok: false, products: [], count: 0, market,
              error: '找不到商品卡片，請確認已在 Shopee 搜索結果頁' });
            return;
          }
          const products = parseCards(cards, market);
          sendResponse({ ok: true, products, market, url: location.href, count: products.length });
        } catch (e) {
          sendResponse({ ok: false, products: [], count: 0, error: String(e) });
        }
      })();
      return true; // 保持 channel 開放等 async
    }
  });

})();
