// Service-level end-to-end test: starts the real Koa service (src/index.js),
// fires concurrent POST /run requests at a local, controllable page server and
// checks the borrow/return behaviour from the outside.
//
// This is the acceptance scenario described in the task: with a small page
// pool, a burst of concurrent crawls must all complete, and requests that had
// to queue must run in the order they were submitted.
//
// Run with: node test/pool.service.js

const assert = require('assert');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

const Service = require('../src');

const LIMIT = 2;
const BASE = 8; // requests per burst

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Local page server. Every request is recorded so the test can reconstruct
// which order the crawler actually reached the pages in.
// ---------------------------------------------------------------------------
const arrivals = [];
const holds = new Map();
let inFlight = 0;
let maxInFlight = 0;

const html = id => `<!doctype html><html><head><title>svc-${id}</title></head><body>${id}</body></html>`;

const pages = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const id = url.searchParams.get('id') || url.pathname;

    if (url.pathname === '/favicon.ico') {
        res.writeHead(204);
        res.end();
        return;
    }

    if (url.pathname === '/boom') {
        // Kill the connection: the navigation fails on the client side. This
        // must not touch the in-flight bookkeeping, because it never handed
        // out a page.
        req.socket.destroy();
        return;
    }

    arrivals.push({ id, at: Date.now() });
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);

    const finish = () => {
        inFlight--;
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(html(id));
    };

    if (url.pathname === '/hold') {
        holds.set(id, finish);
        return;
    }

    setTimeout(finish, +(url.searchParams.get('ms') || 0));
});

const listen = server => new Promise(resolve =>
    server.listen(0, '127.0.0.1', () => resolve(server.address().port)));

// The service treats port 0 as "use the default", so reserve a free port by
// opening a throwaway listener and handing its number over.
const freePort = async () => {
    const probe = http.createServer(() => {});
    const port = await listen(probe);
    await new Promise(resolve => probe.close(resolve));
    return port;
};

// ---------------------------------------------------------------------------
// The request body format the service expects: a JS literal with a url and a
// runner function, both serialised as source text.
// ---------------------------------------------------------------------------
const body = (url, runnerSource) => JSON.stringify({
    sessId: 'test-sess',
    data: `({ url: ${JSON.stringify(url)}, run: ${runnerSource}, options: { whiteList: [] } })`
});

const post = (port, payload) => new Promise((resolve, reject) => {
    const data = Buffer.from(payload, 'utf8');
    const req = http.request({
        host: '127.0.0.1',
        port,
        path: '/run',
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': data.length }
    }, res => {
        let text = '';
        res.setEncoding('utf8');
        res.on('data', chunk => { text += chunk; });
        res.on('end', () => {
            try {
                resolve(JSON.parse(text));
            } catch (e) {
                reject(new Error('bad response: ' + text));
            }
        });
    });
    req.on('error', reject);
    req.end(data);
});

// Deterministically prove the pool has `n` usable pages: n requests pin their
// slot on the page server, which is only possible if n slots are free.
const assertCapacity = async (servicePort, at, n, tag) => {
    holds.clear();
    const ids = Array.from({ length: n }, (_, i) => `${tag}${i}`);
    const pending = ids.map(id => post(servicePort, body(at(`/hold?id=${id}`), probe(id))));
    const reached = await Promise.race([
        (async () => {
            while (!ids.every(id => arrivals.some(a => a.id === id))) await delay(10);
            return true;
        })(),
        delay(20000).then(() => false)
    ]);
    assert.ok(reached, 'the pool must have ' + n + ' usable pages at ' + tag);
    ids.forEach(id => holds.get(id)());
    await Promise.all(pending);
};

// A runner that reports what it saw; the service serialises it into the sandbox
// so it can only use `page`.
const probe = id => `async function (page) {
    return { id: ${JSON.stringify(id)}, url: page.url(), title: await page.title() };
}`;

// ---------------------------------------------------------------------------

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

let pagePort;
let userDataDir;

(async () => {
    pagePort = await listen(pages);

    userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pptr-service-test-'));
    const servicePort = await freePort();
    const { server } = await Service({
        port: servicePort,
        test: false,
        puppeteer: {
            poolLimit: LIMIT,
            executablePath: process.env.CHROME_EXE || undefined,
            userDataDir,
            args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
        }
    });
    if (!server.listening) await new Promise(resolve => server.once('listening', resolve));
    const port = server.address().port;
    const at = p => `http://127.0.0.1:${pagePort}${p}`;

    // -----------------------------------------------------------------------
    test('a burst larger than the pool completes and stays within the limit', async () => {
        arrivals.length = 0;
        maxInFlight = 0;

        const responses = await Promise.all(
            Array.from({ length: BASE }, (_, i) =>
                post(port, body(at(`/slow?id=s${i}&ms=150`), probe('s' + i))))
        );

        responses.forEach((r, i) => {
            assert.ok(r && r.title === 'svc-s' + i, 'request ' + i + ' got ' + JSON.stringify(r));
            assert.ok(r.url.indexOf('id=s' + i) !== -1, 'request ' + i + ' hit ' + r.url);
        });
        assert.ok(maxInFlight <= LIMIT, 'the page pool must cap concurrency at ' + LIMIT + ', saw ' + maxInFlight);
        assert.ok(new Set(arrivals.map(a => a.id)).size >= LIMIT, 'every request should have reached the page server');
    });

    // -----------------------------------------------------------------------
    test('queued requests are served in submission order', async () => {
        arrivals.length = 0;
        holds.clear();

        // Pin both slots.
        const holders = ['h1', 'h2'].map(id =>
            post(port, body(at(`/hold?id=${id}`), probe(id))));
        while (holds.size < 2) await delay(10);

        // These must queue behind them.
        const ids = ['q1', 'q2', 'q3', 'q4'];
        const queued = ids.map(id => post(port, body(at(`/slow?id=${id}&ms=20`), probe(id))));

        await delay(120);
        assert.deepStrictEqual(arrivals.map(a => a.id).filter(id => id[0] === 'q'), [],
            'queued requests must not reach the pages while the pool is full');

        // Release one holder; exactly the oldest waiter should get its turn.
        holds.get('h1')();
        while (!arrivals.some(a => a.id === 'q1')) await delay(5);
        assert.deepStrictEqual(arrivals.map(a => a.id).filter(id => id[0] === 'q'), ['q1'],
            'releasing one page must start exactly one queued request, the oldest');

        holds.get('h2')();
        await Promise.all(holders.concat(queued));

        assert.deepStrictEqual(arrivals.map(a => a.id).filter(id => id[0] === 'q'), ids,
            'queued requests must be served in submission order');
    });

    // -----------------------------------------------------------------------
    test('a failed crawl returns {} and does not shrink the pool', async () => {
        arrivals.length = 0;

        // Navigation error (the page server kills the socket).
        const failed = await post(port, body(at('/boom'), probe('boom')));
        assert.deepStrictEqual(failed, {}, 'a failed crawl must still answer with an empty object');

        // Runner error.
        const throwing = await post(port, body(at('/slow?id=throw&ms=10'),
            `async function (page) { throw new Error('runner exploded'); }`));
        assert.deepStrictEqual(throwing, {}, 'a throwing runner must still answer with an empty object');

        // Both slots must be available, which proves both pages were returned.
        await assertCapacity(port, at, LIMIT, 'cap-');
    });

    // -----------------------------------------------------------------------
    let failedCount = 0;
    for (const { name, fn } of tests) {
        try {
            await fn();
            console.log('ok - ' + name);
        } catch (e) {
            failedCount++;
            console.error('not ok - ' + name);
            console.error(e && e.stack ? e.stack : e);
        }
    }
    console.log('\n' + (tests.length - failedCount) + '/' + tests.length + ' passed');

    await new Promise(resolve => server.close(resolve));
    try {
        await require('../src/puppeteer').close();
    } catch (e) {
        // ignore
    }
    pages.close();
    try {
        fs.rmSync(userDataDir, { recursive: true, force: true, maxRetries: 3 });
    } catch (e) {
        // Windows may still hold the profile for a moment.
    }
    process.exit(failedCount ? 1 : 0);
})().catch(async e => {
    console.error('FATAL', e && e.stack ? e.stack : e);
    try {
        await require('../src/puppeteer').close();
    } catch (err) {
        // ignore
    }
    pages.close();
    process.exit(1);
});
