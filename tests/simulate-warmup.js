// 本地模拟验收：用 vm + 伪造的 DOM/localStorage/fetch/定时器环境，
// 验证预热与正式下载进度分离的修复。
// 运行：node tests/simulate-warmup.js
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SCRIPT_PATH = path.join(__dirname, '..', 'wos-download-bot-enhanced.js');
const SCRIPT = fs.readFileSync(SCRIPT_PATH, 'utf8');

const ORIGIN = 'https://www.webofscience.com';
const QID = 'ABC123';
const SORT = 'relevance';
const PAGE_URL = `${ORIGIN}/wos/woscc/summary/${QID}/${SORT}/1`;
const STORAGE_KEY = `wos_download_bot_task_v3_${ORIGIN}__${QID}__${SORT}`;

const failures = [];
let checkCount = 0;

function check(cond, label) {
    checkCount++;
    if (cond) {
        console.log(`  ✔ ${label}`);
    } else {
        failures.push(label);
        console.log(`  ✘ ${label}`);
    }
}

function fmt(n) {
    return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

// ---------- 伪造环境 ----------

function createPage({ url = PAGE_URL, storage, total = 103500, confirmImpl = () => true, fetchHook = null, downloads, fetches }) {
    const env = {
        url, storage, total, downloads, fetches,
        alerts: [], confirms: [],
        timers: [], freeze: false,
        _timerSeq: 1,
    };

    let doc;
    function makeEl(tag) {
        const el = {
            tagName: tag.toUpperCase(),
            id: '',
            children: [],
            style: {},
            value: '',
            disabled: false,
            _listeners: {},
            _text: '',
            _html: '',
            setAttribute(k, v) { if (k === 'id') el.id = v; el[k] = v; },
            addEventListener(type, fn) { (el._listeners[type] = el._listeners[type] || []).push(fn); },
            appendChild(c) { el.children.push(c); if (c.id) doc._registry[c.id] = c; return c; },
            remove() { if (el.id) delete doc._registry[el.id]; },
            click() {
                if (tag === 'a' && el.download) {
                    downloads.push({ name: el.download, href: el.href });
                }
                (el._listeners.click || []).forEach(fn => fn({}));
            },
        };
        Object.defineProperty(el, 'textContent', {
            get() { return el._text; },
            set(v) { el._text = String(v); },
        });
        Object.defineProperty(el, 'innerHTML', {
            get() { return el._html; },
            set(v) {
                el._html = String(v);
                const re = /id="([^"]+)"/g;
                let m;
                while ((m = re.exec(el._html))) {
                    const child = makeEl('div');
                    child.id = m[1];
                    doc._registry[m[1]] = child;
                }
            },
        });
        return el;
    }

    doc = {
        readyState: 'complete',
        _registry: {},
        getElementById(id) { return this._registry[id] || null; },
        createElement(tag) { return makeEl(tag); },
        querySelector() { return { textContent: `1-50 of ${fmt(total)}` }; },
        addEventListener() {},
        documentElement: makeEl('html'),
        head: makeEl('head'),
        body: makeEl('body'),
    };
    env.document = doc;

    const localStorageStub = {
        getItem: k => (k in storage ? storage[k] : null),
        setItem: (k, v) => { storage[k] = String(v); },
        removeItem: k => { delete storage[k]; },
    };

    const sandbox = {
        document: doc,
        localStorage: localStorageStub,
        location: { href: url, origin: ORIGIN },
        history: { pushState() {}, replaceState() {} },
        sessionData: { BasicProperties: { SID: 'SID-TEST' } },
        MutationObserver: class { constructor() {} observe() {} disconnect() {} },
        URL: { createObjectURL: () => 'blob:fake', revokeObjectURL() {} },
        AbortController,
        console,
        setTimeout(cb, ms) {
            const id = env._timerSeq++;
            env.timers.push({ id, cb, ms: ms || 0 });
            return id;
        },
        clearTimeout(id) {
            const i = env.timers.findIndex(t => t.id === id);
            if (i >= 0) env.timers.splice(i, 1);
        },
        addEventListener() {},
        alert(msg) { env.alerts.push(String(msg)); },
        confirm(msg) { env.confirms.push(String(msg)); return confirmImpl(msg); },
        async fetch(fetchUrl, opts) {
            const body = JSON.parse(opts.body);
            const info = { n: fetches.length + 1, markFrom: Number(body.markFrom), markTo: Number(body.markTo) };
            fetches.push(info);
            if (fetchHook) await fetchHook(info, env);
            return { ok: true, status: 200, blob: async () => ({ size: 1 }), text: async () => '' };
        },
    };
    sandbox.window = sandbox;
    env.sandbox = sandbox;

    vm.runInNewContext(SCRIPT, sandbox, { filename: 'wos-download-bot-enhanced.js' });
    return env;
}

async function pump(env, maxRounds = 3000) {
    const flush = async rounds => { for (let i = 0; i < rounds; i++) await new Promise(r => setImmediate(r)); };
    let n = 0;
    while (n++ < maxRounds) {
        await flush(30);
        if (env.freeze) break;
        if (!env.timers.length) {
            await flush(10);
            if (!env.timers.length || env.freeze) break;
        }
        if (env.freeze) break;
        const t = env.timers.shift();
        t.cb();
    }
    await flush(30);
}

function readRecord(storage) {
    return storage[STORAGE_KEY] ? JSON.parse(storage[STORAGE_KEY]) : null;
}

// 核心防污染不变量：预热未完成时，正式下载游标必须等于用户指定起始条目
function makeInvariantHook(storage, violations) {
    return () => {
        const rec = readRecord(storage);
        if (rec && rec.warmupRequired && !rec.warmupDone && rec.nextStart !== rec.requestedStart) {
            violations.push(`预热期间 nextStart=${rec.nextStart} 污染（requestedStart=${rec.requestedStart}）`);
        }
    };
}

async function startTaskViaUI(env, { start, wait = 10, format = 'ris' }) {
    env.document.getElementById('wos-download-bot-btn').click();
    await pump(env);
    env.document.getElementById('startDownloadFrom').value = String(start);
    env.document.getElementById('downloadSpeed').value = String(wait);
    env.document.getElementById('fileFormat').value = format;
    env.document.getElementById('confirmButton').click();
    await pump(env);
}

// ---------- 场景 1：预热中途暂停后恢复 ----------

async function scenario1() {
    console.log('\n[场景 1] 预热到中途暂停后恢复');
    const storage = {};
    const downloads = [];
    const fetches = [];
    const violations = [];
    const invariant = makeInvariantHook(storage, violations);

    let paused = false;
    const env = createPage({
        storage, downloads, fetches,
        fetchHook: async (info, e) => {
            invariant();
            if (info.n === 2 && !paused) {
                paused = true;
                e.document.getElementById('wos-btn-pause').click();
            }
        },
    });

    await startTaskViaUI(env, { start: 102001 });

    const rec = readRecord(storage);
    check(rec && rec.status === 'paused', '暂停后任务状态为 paused');
    check(rec && rec.warmupRequired === true && rec.warmupDone === false, '记录标明需要预热且预热未完成');
    check(rec && rec.warmupNextStart === 100501, `预热游标推进到 100501（实际 ${rec && rec.warmupNextStart}）`);
    check(rec && rec.requestedStart === 102001, '记录保存用户请求起始条目 102001');
    check(rec && rec.nextStart === 102001, `正式下载游标未被预热污染，仍为 102001（实际 ${rec && rec.nextStart}）`);
    check(downloads.length === 0, '预热阶段未保存任何文件');
    check(fetches.length === 2 && fetches[0].markFrom === 99501 && fetches[1].markFrom === 100001, '预热批次从 99501 起按 500 一批推进');
    check(env.document.getElementById('wos-download-bot-btn').textContent === '继续下载', '主按钮显示「继续下载」');
    check(env.document.getElementById('wos-task-status').textContent === '已暂停（预热阶段）', '控制台状态显示「已暂停（预热阶段）」');
    check(env.document.getElementById('wos-task-next-range').textContent === '预热 100501 - 101000', '控制台下批范围显示预热批次');
    check(env.document.getElementById('wos-task-requested-start').textContent === '102001', '控制台显示用户指定起始条目');

    // 恢复
    env.document.getElementById('wos-download-bot-btn').click();
    await pump(env);

    const warmupFetches = fetches.slice(2, 5).map(f => f.markFrom);
    check(JSON.stringify(warmupFetches) === JSON.stringify([100501, 101001, 101501]), `恢复后预热从 100501 继续（实际 ${warmupFetches}）`);
    check(downloads.length === 3, `正式下载共 3 个文件（实际 ${downloads.length}）`);
    check(downloads[0] && downloads[0].name === `wos_${SORT}_${QID}_102001-102500.ris`, `首个正式文件从 102001 开始（实际 ${downloads[0] && downloads[0].name}）`);
    check(downloads[2] && downloads[2].name === `wos_${SORT}_${QID}_103001-103500.ris`, '最后一个文件覆盖到总数 103500');

    const done = readRecord(storage);
    check(done && done.status === 'completed', '任务最终状态 completed');
    check(done && done.warmupDone === true, '预热标记为已完成');
    check(done && done.lastSuccessStop === 103500, '进度记录到 103500');
    check(done && done.completedRanges.length === 3 && done.completedRanges[0].start === 102001, '已完成范围只包含正式下载区间');
    check(env.alerts.includes('下载完成'), '弹出「下载完成」');
    check(violations.length === 0, `全程无预热污染正式游标（违规 ${violations.length} 次）`);
}

// ---------- 场景 2：预热中途刷新后确认继续 ----------

async function scenario2() {
    console.log('\n[场景 2] 预热中途刷新页面后确认继续');
    const storage = {};
    const downloads = [];
    const fetchesA = [];
    const violations = [];
    const invariant = makeInvariantHook(storage, violations);

    // 页面 A：跑到第 3 个预热请求时“刷新”（冻结页面 A，请求挂起）
    const envA = createPage({
        storage, downloads, fetches: fetchesA,
        fetchHook: async (info, e) => {
            invariant();
            if (info.n === 3) {
                e.freeze = true;
                return new Promise(() => {}); // 页面被刷新，请求永不返回
            }
        },
    });
    await startTaskViaUI(envA, { start: 102001 });

    const recAtRefresh = readRecord(storage);
    check(recAtRefresh && recAtRefresh.status === 'running', '刷新瞬间记录状态为 running');
    check(recAtRefresh && recAtRefresh.warmupDone === false && recAtRefresh.warmupNextStart === 100501, '刷新瞬间预热游标为 100501');
    check(recAtRefresh && recAtRefresh.nextStart === 102001, '刷新瞬间正式游标仍为 102001');

    // 页面 B：同一 localStorage，重新加载脚本，确认继续
    const fetchesB = [];
    const envB = createPage({
        storage, downloads, fetches: fetchesB,
        confirmImpl: () => true,
        fetchHook: async () => { invariant(); },
    });
    await pump(envB);

    check(envB.confirms.some(m => m.includes('检测到未完成下载任务')), '刷新后提示检测到未完成任务');
    const bWarmup = fetchesB.slice(0, 3).map(f => f.markFrom);
    check(JSON.stringify(bWarmup) === JSON.stringify([100501, 101001, 101501]), `续跑预热从 100501 继续而非从头或从 102001（实际 ${bWarmup}）`);
    check(downloads.length === 3 && downloads[0].name === `wos_${SORT}_${QID}_102001-102500.ris`, '预热完成后正式下载从用户指定的 102001 开始');

    const done = readRecord(storage);
    check(done && done.status === 'completed' && done.lastSuccessStop === 103500, '续跑后任务完成，进度到 103500');
    check(violations.length === 0, `全程无预热污染正式游标（违规 ${violations.length} 次）`);
}

// ---------- 场景 3：预热完成后正式下载 + 无需预热的小起始条目 ----------

async function scenario3() {
    console.log('\n[场景 3] 预热完成后正式下载范围正确（含无预热对照）');
    const storage = {};
    const downloads = [];
    const fetches = [];
    const env = createPage({ storage, downloads, fetches });
    await startTaskViaUI(env, { start: 102001 });

    const warmupCount = fetches.filter((f, i) => i < 5).length;
    check(fetches.length === 8 && warmupCount === 5, `共 5 批预热 + 3 批正式（实际 ${fetches.length} 批）`);
    check(downloads.every((d, i) => d.name.endsWith(`_${102001 + i * 500}-${Math.min(102500 + i * 500, 103500)}.ris`)), '正式文件范围连续且从 102001 开始');
    const rec = readRecord(storage);
    check(rec && rec.status === 'completed' && rec.nextStart === 103501, '完成后正式游标推进到 103501');

    // 对照：起始 ≤ 99501 不预热
    const storage2 = {};
    const downloads2 = [];
    const fetches2 = [];
    const env2 = createPage({ storage: storage2, downloads: downloads2, fetches: fetches2, total: 1500 });
    await startTaskViaUI(env2, { start: 1000 });
    check(fetches2.length === 2 && fetches2[0].markFrom === 1000, '小起始条目无预热，直接正式下载');
    check(downloads2.length === 2 && downloads2[0].name.includes('_1000-1499.'), '小起始条目文件范围正确');
    const rec2 = readRecord(storage2);
    check(rec2 && rec2.warmupRequired === false && rec2.warmupDone === true, '小起始条目标记无需预热');
}

// ---------- 场景 4：页面与任务记录不匹配时拒绝恢复 ----------

async function scenario4() {
    console.log('\n[场景 4] 页面与排序不匹配时拒绝恢复');
    const storage = {};
    // 伪造一条 key 匹配当前页、但内容 parentQid 不一致的任务记录
    storage[STORAGE_KEY] = JSON.stringify({
        taskKey: `${ORIGIN}__${QID}__${SORT}`,
        origin: ORIGIN,
        url: PAGE_URL,
        parentQid: 'OTHER-QID',
        sortBy: SORT,
        total: 103500,
        fileFormat: 'ris',
        waitSecond: 10,
        requestedStart: 102001,
        warmupRequired: true,
        warmupDone: false,
        warmupNextStart: 100501,
        nextStart: 102001,
        nextStop: 102500,
        lastSuccessStart: null,
        lastSuccessStop: null,
        completedRanges: [],
        status: 'paused',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        finishedAt: null,
        lastError: null,
    });

    const downloads = [];
    const fetches = [];
    const env = createPage({ storage, downloads, fetches });
    await pump(env);
    check(env.confirms.length === 0, '记录与页面不匹配时不弹出恢复确认');

    env.document.getElementById('wos-download-bot-btn').click();
    await pump(env);
    check(env.alerts.some(m => m.includes('不能恢复')), '手动点击时提示页面不匹配、拒绝恢复');
    check(fetches.length === 0 && downloads.length === 0, '拒绝恢复时不发起任何请求');
    check(!!readRecord(storage), '拒绝恢复后任务记录保留未被清除');
}

// ---------- 场景 5：停止仍清空任务记录 ----------

async function scenario5() {
    console.log('\n[场景 5] 预热途中停止，任务记录被清空');
    const storage = {};
    const downloads = [];
    const fetches = [];
    const env = createPage({
        storage, downloads, fetches,
        confirmImpl: () => true,
        fetchHook: async (info, e) => {
            if (info.n === 1) e.document.getElementById('wos-btn-stop').click();
        },
    });
    await startTaskViaUI(env, { start: 102001 });
    check(readRecord(storage) === null, '停止后任务记录已清空');
    check(downloads.length === 0, '停止时未保存文件');
    check(env.document.getElementById('wos-download-bot-btn').textContent === '一键下载', '停止后主按钮恢复「一键下载」');
}

// ---------- 场景 6：旧格式任务记录迁移后续跑 ----------

async function scenario6() {
    console.log('\n[场景 6] 旧格式（warmupMode）记录迁移后，预热续跑不污染正式起点');
    const storage = {};
    // 旧版本记录：预热途中刷新留下的样子（nextStart 被预热推进到 100501）
    storage[STORAGE_KEY] = JSON.stringify({
        taskKey: `${ORIGIN}__${QID}__${SORT}`,
        origin: ORIGIN,
        url: PAGE_URL,
        parentQid: QID,
        sortBy: SORT,
        total: 103500,
        fileFormat: 'ris',
        waitSecond: 10,
        requestedStart: 102001,
        warmupMode: true,
        nextStart: 100501,
        nextStop: 101000,
        lastSuccessStart: null,
        lastSuccessStop: null,
        completedRanges: [],
        status: 'running',
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
        finishedAt: null,
        lastError: null,
    });

    const downloads = [];
    const fetches = [];
    const violations = [];
    const invariant = makeInvariantHook(storage, violations);
    const env = createPage({
        storage, downloads, fetches,
        confirmImpl: () => true,
        fetchHook: async () => { invariant(); },
    });
    await pump(env);

    const warmup = fetches.slice(0, 3).map(f => f.markFrom);
    check(JSON.stringify(warmup) === JSON.stringify([100501, 101001, 101501]), `旧记录迁移后预热从 100501 继续（实际 ${warmup}）`);
    check(downloads.length === 3 && downloads[0].name === `wos_${SORT}_${QID}_102001-102500.ris`, '旧记录迁移后正式下载仍从 102001 开始');
    const done = readRecord(storage);
    check(done && done.status === 'completed' && !('warmupMode' in done), '迁移后记录不再含 warmupMode');
    check(violations.length === 0, `迁移后全程无预热污染（违规 ${violations.length} 次）`);
}

(async () => {
    await scenario1();
    await scenario2();
    await scenario3();
    await scenario4();
    await scenario5();
    await scenario6();

    console.log(`\n========================================`);
    if (failures.length) {
        console.log(`失败 ${failures.length}/${checkCount} 项：`);
        failures.forEach(f => console.log(`  - ${f}`));
        process.exit(1);
    }
    console.log(`全部 ${checkCount} 项检查通过。`);
})().catch(err => {
    console.error('模拟运行异常：', err);
    process.exit(1);
});
