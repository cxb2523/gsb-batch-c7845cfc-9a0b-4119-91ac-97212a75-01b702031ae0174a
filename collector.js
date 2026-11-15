/*
 * Webtoon Vault — 章节采集核心逻辑
 *
 * 与 UI 解耦：浏览器里通过 window.WebtoonCollector 使用，
 * Node 里通过 require('./collector.js') 使用，便于本地测试。
 *
 * 用法：
 *   const collector = WebtoonCollector.createCollector({
 *     fetchChapterList: async (seriesUrl) => [{id, title, url}, ...],
 *     fetchChapterPages: async (chapter) => [pageUrl, ...],   // 抛错 = 整章失败
 *     fetchPage: async (chapter, pageUrl, pageIndex) => {...},// 抛错 = 单页失败
 *     delay: (ms) => new Promise(r => setTimeout(r, ms)),     // 可注入假实现便于测试
 *     storage: { load(key), save(key, value) },               // 可选，持久化面板状态
 *     storageKey: 'my-series',                                // 可选
 *     maxRetries: 3,        // 每次请求最多重试次数（首次尝试不计）
 *     baseDelayMs: 500,     // 基础请求间隔
 *     backoffFactor: 2,     // 连续失败时的退避倍数
 *     maxDelayMs: 30000,    // 请求间隔上限
 *     onEvent: (evt) => {}  // 可选，进度回调
 *   });
 *   const summary = await collector.run(seriesUrl);            // 全量/续跑
 *   const summary2 = await collector.run(seriesUrl, { onlyFailed: true }); // 只重试失败章节
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.WebtoonCollector = api;
  }
})(typeof self !== 'undefined' ? self : globalThis, function () {
  'use strict';

  var STATUS = {
    PENDING: 'pending',
    RUNNING: 'running',
    COMPLETED: 'completed',
    PARTIAL: 'partial',
    FAILED: 'failed'
  };

  function defaultDelay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function createCollector(opts) {
    opts = opts || {};
    var fetchChapterList = opts.fetchChapterList;
    var fetchChapterPages = opts.fetchChapterPages;
    var fetchPage = opts.fetchPage;
    if (typeof fetchChapterList !== 'function' ||
        typeof fetchChapterPages !== 'function' ||
        typeof fetchPage !== 'function') {
      throw new Error('createCollector 需要 fetchChapterList / fetchChapterPages / fetchPage 三个函数');
    }

    var delay = typeof opts.delay === 'function' ? opts.delay : defaultDelay;
    var storage = opts.storage || null;
    var storageKey = opts.storageKey || 'webtoon-vault:collector:default';
    var maxRetries = typeof opts.maxRetries === 'number' ? opts.maxRetries : 3;
    var baseDelayMs = typeof opts.baseDelayMs === 'number' ? opts.baseDelayMs : 500;
    var backoffFactor = typeof opts.backoffFactor === 'number' ? opts.backoffFactor : 2;
    var maxDelayMs = typeof opts.maxDelayMs === 'number' ? opts.maxDelayMs : 30000;
    var onEvent = typeof opts.onEvent === 'function' ? opts.onEvent : function () {};

    // 当前请求间隔：连续失败时按 backoffFactor 拉长，成功后回落到 baseDelayMs
    var currentDelayMs = baseDelayMs;

    var state = {
      seriesUrl: null,
      chapters: [] // [{id,title,url,status,totalPages,donePages:[],failedPages:[],error}]
    };

    function emit(type, payload) {
      var evt = { type: type };
      if (payload) { for (var k in payload) evt[k] = payload[k]; }
      try { onEvent(evt); } catch (e) { /* 回调异常不影响采集 */ }
    }

    function persist() {
      if (!storage) return Promise.resolve();
      return Promise.resolve().then(function () {
        return storage.save(storageKey, JSON.stringify(state));
      }).catch(function () { /* 持久化失败不阻断采集 */ });
    }

    function restore() {
      if (!storage) return Promise.resolve(null);
      return Promise.resolve().then(function () {
        return storage.load(storageKey);
      }).then(function (raw) {
        if (!raw) return null;
        try { return JSON.parse(raw); } catch (e) { return null; }
      }).catch(function () { return null; });
    }

    function findChapter(id) {
      for (var i = 0; i < state.chapters.length; i++) {
        if (state.chapters[i].id === id) return state.chapters[i];
      }
      return null;
    }

    // 带退避与重试上限的请求包装
    function request(fn) {
      var attempt = 0;
      function tryOnce() {
        // 每次请求前按当前间隔等待（退避在失败时已拉长 currentDelayMs）
        return delay(currentDelayMs).then(function () {
          return fn();
        }).then(function (result) {
          currentDelayMs = baseDelayMs; // 成功后回落
          return result;
        }, function (err) {
          attempt++;
          currentDelayMs = Math.min(currentDelayMs * backoffFactor, maxDelayMs);
          if (attempt > maxRetries) {
            throw err;
          }
          emit('request-retry', { attempt: attempt, maxRetries: maxRetries, error: String(err && err.message || err) });
          return tryOnce();
        });
      }
      return tryOnce();
    }

    function runChapter(ch) {
      ch.status = STATUS.RUNNING;
      ch.error = null;
      emit('chapter-start', { chapter: ch });
      return persist().then(function () {
        return request(function () { return fetchChapterPages(ch); });
      }).then(function (pages) {
        ch.totalPages = pages.length;
        emit('chapter-pages', { chapter: ch, totalPages: pages.length });
        // 串行逐页抓取；已成功的页不重试、不重复计数
        var chain = Promise.resolve();
        pages.forEach(function (pageUrl, idx) {
          if (ch.donePages.indexOf(idx) !== -1) return;
          chain = chain.then(function () {
            return request(function () { return fetchPage(ch, pageUrl, idx); }).then(function () {
              ch.donePages.push(idx);
              var fi = ch.failedPages.indexOf(idx);
              if (fi !== -1) ch.failedPages.splice(fi, 1);
              emit('page-ok', { chapter: ch, pageIndex: idx });
            }, function (err) {
              if (ch.failedPages.indexOf(idx) === -1) ch.failedPages.push(idx);
              emit('page-fail', { chapter: ch, pageIndex: idx, error: String(err && err.message || err) });
            }).then(function () {
              return persist();
            });
          });
        });
        return chain.then(function () {
          if (ch.failedPages.length === 0) {
            ch.status = STATUS.COMPLETED;
            ch.error = null;
          } else {
            ch.status = STATUS.PARTIAL;
            ch.error = ch.failedPages.length + ' 页失败（页序号: ' + ch.failedPages.join(', ') + '）';
          }
          emit('chapter-done', { chapter: ch });
          return persist();
        });
      }, function (err) {
        // 整章失败（如章节页无法打开）：记录原因，跳过该章
        ch.status = STATUS.FAILED;
        ch.error = String(err && err.message || err);
        emit('chapter-failed', { chapter: ch, error: ch.error });
        return persist();
      });
    }

    function run(seriesUrl, runOpts) {
      runOpts = runOpts || {};
      var onlyFailed = !!runOpts.onlyFailed;

      return Promise.resolve()
        .then(function () { return request(function () { return fetchChapterList(seriesUrl); }); })
        .then(function (list) {
          return restore().then(function (saved) {
            // 合并持久化状态：保留已成功/失败的页记录，刷新后可区分三类状态
            state.seriesUrl = seriesUrl;
            state.chapters = list.map(function (item) {
              var prev = saved && saved.chapters ? findChapterIn(saved.chapters, item.id) : null;
              return {
                id: item.id,
                title: item.title || item.id,
                url: item.url,
                status: prev ? prev.status : STATUS.PENDING,
                totalPages: prev ? prev.totalPages : 0,
                donePages: prev ? prev.donePages.slice() : [],
                failedPages: prev ? prev.failedPages.slice() : [],
                error: prev ? prev.error : null
              };
            });
            return persist();
          });
        })
        .then(function () {
          var targets = state.chapters.filter(function (ch) {
            if (ch.status === STATUS.COMPLETED) return false; // 已完成不重复采集
            if (onlyFailed) {
              return ch.status === STATUS.FAILED || ch.status === STATUS.PARTIAL;
            }
            return true;
          });
          var chain = Promise.resolve();
          targets.forEach(function (ch) {
            chain = chain.then(function () { return runChapter(ch); });
          });
          return chain.then(function () { return summary(); });
        });
    }

    function findChapterIn(chapters, id) {
      for (var i = 0; i < chapters.length; i++) {
        if (chapters[i].id === id) return chapters[i];
      }
      return null;
    }

    function summary() {
      return {
        seriesUrl: state.seriesUrl,
        total: state.chapters.length,
        completed: state.chapters.filter(function (c) { return c.status === STATUS.COMPLETED; }).length,
        partial: state.chapters.filter(function (c) { return c.status === STATUS.PARTIAL; }).length,
        failed: state.chapters.filter(function (c) { return c.status === STATUS.FAILED; }).length,
        chapters: state.chapters.map(function (c) {
          return {
            id: c.id,
            title: c.title,
            status: c.status,
            totalPages: c.totalPages,
            okPages: c.donePages.length,
            failedPages: c.failedPages.slice(),
            error: c.error
          };
        })
      };
    }

    return {
      STATUS: STATUS,
      run: run,
      summary: summary,
      getState: function () { return state; },
      // 仅供测试/调试观察退避变化
      _getCurrentDelayMs: function () { return currentDelayMs; }
    };
  }

  return {
    STATUS: STATUS,
    createCollector: createCollector
  };
});
