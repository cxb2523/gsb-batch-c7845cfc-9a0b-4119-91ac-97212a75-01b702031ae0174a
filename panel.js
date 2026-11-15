/*
 * Webtoon Vault — 章节采集面板
 *
 * 在采集页（index.html 解码重放之后）注入一个采集面板：
 *  - 粘贴系列地址，按章节顺序逐章采集
 *  - 每章结束给出可核对结局（已完成 / 部分完成 / 失败 + 成功/失败页数）
 *  - 单页失败不丢弃已成功页；整章失败跳过并保留原因
 *  - 连续失败退避、成功后回落，重试次数有上限
 *  - 状态持久化到 localStorage，刷新后仍可区分三类状态
 *  - 支持只重试失败/部分完成章节，已成功页不重复计入
 *
 * 依赖 collector.js（window.WebtoonCollector）。
 */
(function () {
  'use strict';

  var STORAGE_PREFIX = 'webtoon-vault:collector:';

  function localStorageAdapter(key) {
    return {
      load: function (k) {
        try { return window.localStorage.getItem(k); } catch (e) { return null; }
      },
      save: function (k, v) {
        try { window.localStorage.setItem(k, v); } catch (e) { /* 隐私模式等场景忽略 */ }
      }
    };
  }

  /* ---------- 默认抓取实现（可被 window.WebtoonVaultHooks 覆盖） ---------- */

  function fetchText(url) {
    return fetch(url, { credentials: 'include' }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status + ' ' + url);
      return resp.text();
    });
  }

  // 从系列页解析章节列表：同源、含数字或 chapter 字样的链接，按出现顺序去重
  function defaultFetchChapterList(seriesUrl) {
    return fetchText(seriesUrl).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var seen = {};
      var list = [];
      var anchors = doc.querySelectorAll('a[href]');
      for (var i = 0; i < anchors.length; i++) {
        var a = anchors[i];
        var href;
        try { href = new URL(a.getAttribute('href'), seriesUrl).href; }
        catch (e) { continue; }
        if (href === seriesUrl || seen[href]) continue;
        var text = (a.textContent || '').trim();
        if (!/(\d+|chapter|话|章)/i.test(href + ' ' + text)) continue;
        seen[href] = true;
        list.push({ id: href, title: text || href, url: href });
      }
      if (!list.length) throw new Error('未能从系列页解析出章节列表');
      return list;
    });
  }

  // 从章节页解析页面图片
  function defaultFetchChapterPages(chapter) {
    return fetchText(chapter.url).then(function (html) {
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var imgs = doc.querySelectorAll('img[src], img[data-src]');
      var pages = [];
      for (var i = 0; i < imgs.length; i++) {
        var src = imgs[i].getAttribute('data-src') || imgs[i].getAttribute('src');
        if (!src) continue;
        try { pages.push(new URL(src, chapter.url).href); } catch (e) { /* 跳过坏地址 */ }
      }
      if (!pages.length) throw new Error('章节页未解析到任何页面图片');
      return pages;
    });
  }

  // 拉取单页（校验可访问即可，实际保存由用户后续处理）
  function defaultFetchPage(chapter, pageUrl) {
    return fetch(pageUrl, { credentials: 'include' }).then(function (resp) {
      if (!resp.ok) throw new Error('HTTP ' + resp.status + ' ' + pageUrl);
      return resp.blob();
    });
  }

  function hooks() {
    return window.WebtoonVaultHooks || {};
  }

  /* ---------- 面板 UI ---------- */

  var STATUS_LABEL = {
    pending: '等待中',
    running: '采集中…',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败'
  };

  var els = {};
  var collector = null;
  var lastSummary = null;

  function h(tag, attrs, text) {
    var el = document.createElement(tag);
    if (attrs) for (var k in attrs) el.setAttribute(k, attrs[k]);
    if (text != null) el.textContent = text;
    return el;
  }

  function buildPanel(root) {
    root.innerHTML = '';
    var box = h('div', { id: 'wv-collector-panel', style:
      'margin:16px auto;max-width:860px;padding:16px;border:1px solid #ccc;border-radius:8px;' +
      'font:14px/1.5 system-ui,sans-serif;background:#fafafa;color:#222;' });

    box.appendChild(h('h2', { style: 'margin:0 0 8px;font-size:18px;' }, '章节采集面板'));
    box.appendChild(h('p', { style: 'margin:0 0 12px;color:#555;' },
      '粘贴系列地址后按章节顺序逐章采集；单页失败保留已成功页，整章失败跳过并记录原因。'));

    var form = h('div', { style: 'display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;' });
    els.urlInput = h('input', { type: 'url', placeholder: 'https://example.com/series/123',
      style: 'flex:1;min-width:260px;padding:6px 8px;' });
    els.startBtn = h('button', { style: 'padding:6px 14px;' }, '开始采集');
    els.retryBtn = h('button', { style: 'padding:6px 14px;', disabled: 'disabled' }, '重试失败章节');
    form.appendChild(els.urlInput);
    form.appendChild(els.startBtn);
    form.appendChild(els.retryBtn);
    box.appendChild(form);

    els.summaryLine = h('div', { style: 'margin-bottom:8px;color:#333;' }, '尚未开始。');
    box.appendChild(els.summaryLine);

    els.table = h('table', { style: 'width:100%;border-collapse:collapse;font-size:13px;' });
    els.table.innerHTML =
      '<thead><tr>' +
      '<th style="text-align:left;border-bottom:1px solid #ddd;padding:4px;">章节</th>' +
      '<th style="text-align:left;border-bottom:1px solid #ddd;padding:4px;">状态</th>' +
      '<th style="text-align:right;border-bottom:1px solid #ddd;padding:4px;">成功页</th>' +
      '<th style="text-align:right;border-bottom:1px solid #ddd;padding:4px;">失败页</th>' +
      '<th style="text-align:left;border-bottom:1px solid #ddd;padding:4px;">说明</th>' +
      '</tr></thead><tbody></tbody>';
    box.appendChild(els.table);
    els.tbody = els.table.querySelector('tbody');

    root.appendChild(box);

    els.startBtn.addEventListener('click', function () { startRun(false); });
    els.retryBtn.addEventListener('click', function () { startRun(true); });
  }

  function render() {
    if (!lastSummary) return;
    els.summaryLine.textContent =
      '共 ' + lastSummary.total + ' 章：已完成 ' + lastSummary.completed +
      '，部分完成 ' + lastSummary.partial + '，失败 ' + lastSummary.failed + '。';
    els.retryBtn.disabled = !(lastSummary.failed > 0 || lastSummary.partial > 0);
    els.tbody.innerHTML = '';
    lastSummary.chapters.forEach(function (c) {
      var tr = h('tr');
      tr.appendChild(h('td', { style: 'padding:4px;border-bottom:1px solid #eee;' }, c.title));
      tr.appendChild(h('td', { style: 'padding:4px;border-bottom:1px solid #eee;' },
        STATUS_LABEL[c.status] || c.status));
      tr.appendChild(h('td', { style: 'padding:4px;border-bottom:1px solid #eee;text-align:right;' },
        String(c.okPages)));
      tr.appendChild(h('td', { style: 'padding:4px;border-bottom:1px solid #eee;text-align:right;' },
        String(c.failedPages.length)));
      tr.appendChild(h('td', { style: 'padding:4px;border-bottom:1px solid #eee;color:#a33;' },
        c.error || ''));
      els.tbody.appendChild(tr);
    });
  }

  function refreshFromCollector() {
    if (!collector) return;
    lastSummary = collector.summary();
    render();
  }

  function makeCollector(seriesUrl) {
    var hk = hooks();
    return window.WebtoonCollector.createCollector({
      fetchChapterList: hk.fetchChapterList || defaultFetchChapterList,
      fetchChapterPages: hk.fetchChapterPages || defaultFetchChapterPages,
      fetchPage: hk.fetchPage || defaultFetchPage,
      storage: localStorageAdapter(),
      storageKey: STORAGE_PREFIX + seriesUrl,
      maxRetries: typeof hk.maxRetries === 'number' ? hk.maxRetries : 3,
      baseDelayMs: typeof hk.baseDelayMs === 'number' ? hk.baseDelayMs : 500,
      backoffFactor: typeof hk.backoffFactor === 'number' ? hk.backoffFactor : 2,
      maxDelayMs: typeof hk.maxDelayMs === 'number' ? hk.maxDelayMs : 30000,
      onEvent: function () { refreshFromCollector(); }
    });
  }

  function startRun(onlyFailed) {
    var seriesUrl = (els.urlInput.value || '').trim();
    if (!seriesUrl) {
      els.summaryLine.textContent = '请先粘贴系列地址。';
      return;
    }
    if (!collector || collector.getState().seriesUrl !== seriesUrl) {
      collector = makeCollector(seriesUrl);
    }
    els.startBtn.disabled = true;
    els.retryBtn.disabled = true;
    collector.run(seriesUrl, { onlyFailed: onlyFailed })
      .then(function (sum) {
        lastSummary = sum;
        render();
      })
      .catch(function (err) {
        els.summaryLine.textContent = '采集中断：' + (err && err.message || err);
        refreshFromCollector();
      })
      .then(function () {
        els.startBtn.disabled = false;
      });
  }

  // 刷新后恢复展示：若 localStorage 里有该系列的状态，直接重建表格
  function restoreIfAny() {
    var seriesUrl = (els.urlInput.value || '').trim();
    if (!seriesUrl) return;
    collector = makeCollector(seriesUrl);
    // 触发一次 summary 渲染（状态在 run 时才合并，这里直接读存储渲染）
    try {
      var raw = window.localStorage.getItem(STORAGE_PREFIX + seriesUrl);
      if (!raw) return;
      var saved = JSON.parse(raw);
      if (!saved || !saved.chapters) return;
      lastSummary = {
        seriesUrl: seriesUrl,
        total: saved.chapters.length,
        completed: saved.chapters.filter(function (c) { return c.status === 'completed'; }).length,
        partial: saved.chapters.filter(function (c) { return c.status === 'partial'; }).length,
        failed: saved.chapters.filter(function (c) { return c.status === 'failed'; }).length,
        chapters: saved.chapters.map(function (c) {
          return {
            id: c.id, title: c.title, status: c.status, totalPages: c.totalPages,
            okPages: (c.donePages || []).length, failedPages: c.failedPages || [], error: c.error
          };
        })
      };
      render();
    } catch (e) { /* 忽略损坏的存档 */ }
  }

  function mount() {
    if (document.getElementById('wv-collector-panel')) return;
    var host = document.createElement('div');
    // 插到解码后正文的最前面，保证用户能看到采集过程
    document.body.insertBefore(host, document.body.firstChild);
    buildPanel(host);
    els.urlInput.addEventListener('change', restoreIfAny);
    els.urlInput.addEventListener('blur', restoreIfAny);
  }

  window.WebtoonVaultPanel = { mount: mount };

  // index.html 的解码脚本在 DOMContentLoaded 里重建 body；
  // 本脚本注册在其后，因此同一事件里 _run 先执行、面板后挂载。
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
