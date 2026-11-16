// ── market.js — 市場識別與本地幣種價格解析（content.js / popup 共用）────
//
// 職責：
//   1. 市場代碼正規化、域名 → 市場推斷、hash → 市場讀取
//   2. 市場優先級：派發端傳入（hash） → 當前域名 → 台灣站
//   3. 依市場幣種符號（Rp / ฿ / ₫ / RM / ₱ / $ / R$ / NT$）與
//      當地數字慣例（千位/小數分隔符）解析價格、銷量、評分
//
// 解析不到的欄位一律回傳 null，由上層標記為「字段缺失」，
// 不得回退成 0 —— 0 元 / 0 銷量是合法數據值，和「沒解析到」不是一回事。
//
// 加載方式：manifest.json 的 content_scripts 先載入本檔，再載入 content.js。
// Node 測試：require('./market.js')。
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.SelektMarket = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const DEFAULT_MARKET = 'tw';

  const MARKET_DOMAINS = {
    tw: 'shopee.tw',     sg: 'shopee.sg',
    my: 'shopee.com.my', id: 'shopee.co.id',
    th: 'shopee.co.th',  ph: 'shopee.ph',
    vn: 'shopee.vn',     br: 'shopee.com.br',
  };

  // symbols 為正則片段（不是顯示字串）：
  //   prefixSymbols 出現在數字前（Rp12.345 / RM12.90 / NT$1,200）
  //   suffixSymbols 出現在數字後（120.000 ₫ / 1,250 ฿）
  // decimal / thousand 為該市場數字慣例：
  //   印尼、越南、巴西用「.」分千位、「,」分小數；其餘市場相反。
  // priceTiers 與後端 markets.py 的 [超低, 低, 中, 高] 對齊。
  // display 僅用於 UI 顯示。
  const MARKET_PROFILES = {
    tw: {
      currency: 'TWD', display: 'NT$', decimal: '.', thousand: ',',
      priceTiers: [99, 150, 499, 999],
      prefixSymbols: ['NT\\$', '\\$'], suffixSymbols: [],
    },
    sg: {
      currency: 'SGD', display: 'S$', decimal: '.', thousand: ',',
      priceTiers: [3, 8, 30, 60],
      prefixSymbols: ['S\\$', '\\$'], suffixSymbols: [],
    },
    my: {
      currency: 'MYR', display: 'RM', decimal: '.', thousand: ',',
      priceTiers: [5, 15, 60, 120],
      prefixSymbols: ['RM'], suffixSymbols: [],
    },
    id: {
      currency: 'IDR', display: 'Rp', decimal: ',', thousand: '.',
      priceTiers: [5000, 20000, 100000, 300000],
      prefixSymbols: ['Rp\\.?'], suffixSymbols: [],
    },
    th: {
      currency: 'THB', display: '฿', decimal: '.', thousand: ',',
      priceTiers: [50, 100, 400, 900],
      prefixSymbols: ['฿'], suffixSymbols: ['฿'],
    },
    ph: {
      currency: 'PHP', display: '₱', decimal: '.', thousand: ',',
      priceTiers: [99, 200, 800, 1500],
      prefixSymbols: ['₱'], suffixSymbols: [],
    },
    vn: {
      currency: 'VND', display: '₫', decimal: ',', thousand: '.',
      priceTiers: [30000, 80000, 400000, 900000],
      prefixSymbols: ['₫', 'đ'], suffixSymbols: ['₫', 'đ'],
    },
    br: {
      currency: 'BRL', display: 'R$', decimal: ',', thousand: '.',
      priceTiers: [5, 15, 60, 120],
      prefixSymbols: ['R\\$'], suffixSymbols: [],
    },
  };

  // 銷量關鍵字：中文 / 英文 / 印尼 / 泰 / 越 / 葡
  const SALES_MARKERS = '已售出|已售|售出|sold|terjual|ขายแล้ว|đã\\s*bán|vendidos?|mua\\s*được';
  // 銷量單位：印尼 ribu/rb、jt/juta；越南 tr；巴西 mil；通用 k；中文 萬
  // 順序：長字串在前，避免 ribu 被 rb、juta 被 jt 前綴截斷
  const COUNT_UNITS = '(ribu|rb|juta|jt|tr|mil|k|萬|万)';

  function normalizeMarketCode(code) {
    const c = String(code == null ? '' : code).trim().toLowerCase();
    return Object.prototype.hasOwnProperty.call(MARKET_DOMAINS, c) ? c : null;
  }

  function getMarketProfile(market) {
    return MARKET_PROFILES[normalizeMarketCode(market) || DEFAULT_MARKET];
  }

  function getMarketSymbol(market) {
    return getMarketProfile(market).display;
  }

  function getMarketDomain(market) {
    return MARKET_DOMAINS[normalizeMarketCode(market) || DEFAULT_MARKET];
  }

  // 由當前域名推斷市場（hash 未帶 market 的舊任務 / 手動採集）
  function detectMarketFromHostname(hostname) {
    const h = String(hostname || '').trim().toLowerCase().replace(/\.$/, '');
    if (!h) return null;
    for (const code of Object.keys(MARKET_DOMAINS)) {
      const domain = MARKET_DOMAINS[code];
      if (h === domain || h.endsWith('.' + domain)) return code;
    }
    return null;
  }

  // 從 hash 讀取派發端傳入的市場代碼，例如 #selekt-task-12&market=id
  function readMarketFromHash(hash) {
    const m = String(hash || '').match(/[#&?]market=([A-Za-z]{2,3})(?:[&#]|$)/);
    return m ? normalizeMarketCode(m[1]) : null;
  }

  // 優先級：派發端傳入市場 → 當前域名推斷 → 台灣站
  function resolveMarket(hash, hostname) {
    return readMarketFromHash(hash)
        || detectMarketFromHostname(hostname)
        || DEFAULT_MARKET;
  }

  // ── 數字正規化：處理各市場千位/小數分隔符 ───────────────────────
  // opts.abbrev = true 表示數字帶 k/rb/萬 等單位，此時「1,2」「1.2」
  // 一律視為小數（1.2k），而不是千位分隔。
  function parseLocalizedNumber(raw, market, opts) {
    if (raw == null) return null;
    const profile = getMarketProfile(market);
    let s = String(raw).replace(/[\s  ]/g, '');
    s = s.replace(/^[.,]+/, '').replace(/[.,]+$/, '');
    if (!s || !/[0-9]/.test(s)) return null;

    if (opts && opts.abbrev) {
      if (/^[0-9]+[.,][0-9]{1,2}$/.test(s)) s = s.replace(',', '.');
      else s = s.replace(/[.,]/g, '');
    } else if (profile.decimal === ',') {
      s = s.replace(/\./g, '').replace(/,/g, '.');
    } else {
      s = s.replace(/,/g, '');
    }

    const n = parseFloat(s);
    return Number.isFinite(n) ? n : null;
  }

  // ── 價格：依市場識別本地幣種符號 ─────────────────────────────────
  // 數字樣式：1–3 位整數 + 任意組「分隔符 + 3 位」（千位）+ 可選「分隔符 + 1–2 位」（小數）。
  // 不能簡單用 [0-9.,\s]* 吃掉所有字元 —— 卡片文字常是「RM45.90 1.5k sold」，
  // 貪婪匹配會把後面的銷量數字併進價格（45.901）。
  const NUM_PATTERN = '[0-9]{1,3}(?:[.,\\s\\u00a0\\u202f][0-9]{3})*(?:[.,][0-9]{1,2})?'
                    + '|[0-9]+';
  const NUM_GUARD = '(?![0-9])';

  // 回傳 number；頁面完全沒有可識別價格時回傳 null。
  function parsePriceForMarket(text, market) {
    if (!text) return null;
    const profile = getMarketProfile(market);
    const src = String(text);

    if (profile.prefixSymbols.length) {
      const m = src.match(new RegExp(
        '(?:' + profile.prefixSymbols.join('|') + ')\\s*(' + NUM_PATTERN + ')' + NUM_GUARD, 'i'
      ));
      if (m) {
        const price = parseLocalizedNumber(m[1], market, { abbrev: false });
        if (price != null) return price;
      }
    }
    if (profile.suffixSymbols.length) {
      const m = src.match(new RegExp(
        '(' + NUM_PATTERN + ')' + NUM_GUARD + '\\s*(?:' + profile.suffixSymbols.join('|') + ')', 'i'
      ));
      if (m) {
        const price = parseLocalizedNumber(m[1], market, { abbrev: false });
        if (price != null) return price;
      }
    }
    return null;
  }

  function countUnitMultiplier(unit) {
    const u = String(unit || '').toLowerCase();
    if (u === 'k' || u === 'rb' || u === 'ribu' || u === 'mil') return 1000;
    if (u === 'jt' || u === 'juta' || u === 'tr') return 1000000;
    if (u === '萬' || u === '万') return 10000;
    return 1;
  }

  // ── 銷量：支援 已售/已售出、sold、terjual、ขายแล้ว、đã bán、vendidos ──
  // 回傳 number；頁面沒有銷量字樣時回傳 null。
  function parseSalesForMarket(text, market) {
    if (!text) return null;
    const src = String(text);
    const marker = '(?:' + SALES_MARKERS + ')';

    let m = src.match(
      new RegExp(marker + '\\s*([0-9][0-9.,]*)\\s*' + COUNT_UNITS + '?', 'i')
    );
    let raw = m && m[1];
    let unit = m && m[2];
    if (raw == null) {
      m = src.match(
        new RegExp('([0-9][0-9.,]*)\\s*' + COUNT_UNITS + '?\\s*' + marker, 'i')
      );
      raw = m && m[1];
      unit = m && m[2];
    }
    if (raw == null) return null;

    const n = parseLocalizedNumber(raw, market, { abbrev: !!unit });
    if (n == null) return null;
    return Math.round(n * countUnitMultiplier(unit));
  }

  // ── 評分 / 評論數：常見 "4.8 (1.2k)"、"4,9 (1,2rb)" ─────────────
  // matched 為匹配到的原始片段（供呼叫端從整段文字中摘除，避免它被
  // 價格／銷量正則重複吃進數字裡）。
  function parseRatingForMarket(text, market) {
    if (!text) return { rating: 0, ratingCount: 0, matched: null };
    const m = String(text).match(
      new RegExp('([0-5][.,][0-9])\\s*\\(\\s*([0-9][0-9.,]*)\\s*' + COUNT_UNITS + '?\\s*\\)', 'i')
    );
    if (!m) return { rating: 0, ratingCount: 0, matched: null };

    const rating = parseFloat(m[1].replace(',', '.')) || 0;
    const n = parseLocalizedNumber(m[2], market, { abbrev: !!m[3] });
    const ratingCount = n == null ? 0 : Math.round(n * countUnitMultiplier(m[3]));
    return { rating, ratingCount, matched: m[0] };
  }

  return {
    DEFAULT_MARKET,
    MARKET_DOMAINS,
    MARKET_PROFILES,
    normalizeMarketCode,
    getMarketProfile,
    getMarketSymbol,
    getMarketDomain,
    detectMarketFromHostname,
    readMarketFromHash,
    resolveMarket,
    parseLocalizedNumber,
    parsePriceForMarket,
    parseSalesForMarket,
    parseRatingForMarket,
    countUnitMultiplier,
  };
});
