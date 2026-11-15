// End-to-end test of the borrow/return chain with a real headless Chromium and
// a local, fully controllable HTTP server.
//
// It exercises the exact production path: Ppt.open({ poolLimit }) ->
// Ppt.run(url, fn) -> pageBroker.open()/close().
//
// Run with: node test/pool.integration.js
//
// Environment knobs:
//   CHROME_EXE   path to a Chrome/Chromium binary (defaults to the binary
//                bundled with the puppeteer dependency)

const assert = require('assert');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');

const Ppt = require('../src/puppeteer');

const LIMIT = 2;

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------
// Local controllable pages
// ---------------------------------------------------------------------------
const arrivals = []; // ids in the order the server received them
const holds = new Map(); // id -> function that releases the held response

let inFlight = 0;
let maxInFlight = 0;

const page = (id) => `<!doctype html><html><head><title>page-${id}</title></head><body>${id}</body></html>`;

const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    const id = url.searchParams.get('id') || url.pathname;

    if (url.pathname === '/favicon.ico') {
        res.writeHead(204);
        res.end();
        return;
    }

    if (url.pathname === '/abort') {
        // Simulates a navigation that never produces a response.
        req.socket.destroy();
        return;
    }

    arrivals.push(id);
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);

    const finish = () => {
        inFlight--;
        res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
        res.end(page(id));
    };

    if (url.pathname === '/hold') {
        // Response is released by the test, so it can pin a pool slot.
        holds.set(id, finish);
        return;
    }

    const ms = +(url.searchParams.get('ms') || 0);
    setTimeout(finish, ms);
});

const startServer = () => new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => resolve(server.address().port));
});

// ---------------------------------------------------------------------------
// Test helpers
// ---------------------------------------------------------------------------
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

const baseUrl = () => 'http://127.0.0.1:' + port;

const crawler = (id) => async page => {
    // page.url() proves the navigation really happened and the right page is
    // attached to this request.
    return { id, url: page.url(), title: await page.title() };
};

const resetCounters = () => {
    arrivals.length = 0;
    inFlight = 0;
    maxInFlight = 0;
};

// Verify the pool really has `n` usable pages: borrow n pages that pin their
// slot on the page server, which only works if all n slots are free. This is a
// deterministic capacity check, unlike timing-based concurrency counters.
const assertCapacity = async (n, tag) => {
    holds.clear();
    const ids = Array.from({ length: n }, (_, i) => `${tag}${i}`);
    const pending = ids.map(id => Ppt.run(`${baseUrl()}/hold?id=${id}`, crawler(id), {}));
    const reached = await Promise.race([
        (async () => {
            while (!ids.every(id => arrivals.includes(id))) await delay(10);
            return true;
        })(),
        delay(15000).then(() => false)
    ]);
    assert.ok(reached, 'the pool must have ' + n + ' usable pages at ' + tag);
    ids.forEach(id => holds.get(id)());
    await Promise.all(pending);
};

// ---------------------------------------------------------------------------

let port;
let userDataDir;

(async () => {
    port = await startServer();

    userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'pptr-pool-test-'));
    await Ppt.open({
        poolLimit: LIMIT,
        executablePath: process.env.CHROME_EXE || undefined,
        userDataDir,
        args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage']
    });

    // -----------------------------------------------------------------------
    // 1. More concurrent crawls than pool slots: every request finishes.
    // -----------------------------------------------------------------------
    test('serves more concurrent requests than the pool limit', async () => {
        resetCounters();
        const results = await Promise.all(
            [0, 1, 2, 3, 4, 5, 6, 7].map(i =>
                Ppt.run(`${baseUrl()}/slow?id=r${i}&ms=150`, crawler('r' + i), {})
            )
        );

        assert.strictEqual(results.length, 8);
        results.forEach((r, i) => {
            assert.ok(r && r.title === 'page-r' + i, 'request ' + i + ' should return its own page, got ' + JSON.stringify(r));
            assert.ok(r.url.indexOf('/slow?id=r' + i) !== -1, 'request ' + i + ' should have navigated to its own url');
        });
        assert.ok(maxInFlight <= LIMIT, 'at most ' + LIMIT + ' pages may load at once, saw ' + maxInFlight);
        // Saturation (both slots in use at the same time) is asserted
        // deterministically by the /hold test below, not by timing here.
        const uniqueSlots = new Set(arrivals).size;
        assert.ok(uniqueSlots >= LIMIT, 'every request should have reached the page server');
    });

    // -----------------------------------------------------------------------
    // 2. FIFO wake-up: queued requests start in submission order.
    // -----------------------------------------------------------------------
    test('wakes queued requests in submission order', async () => {
        resetCounters();
        holds.clear();

        // Pin both slots with requests the server will not answer yet.
        const h1 = Ppt.run(`${baseUrl()}/hold?id=h1`, crawler('h1'), {});
        const h2 = Ppt.run(`${baseUrl()}/hold?id=h2`, crawler('h2'), {});
        while (holds.size < 2) await delay(10);

        // These four must queue: the pool has no free page.
        const queued = ['q1', 'q2', 'q3', 'q4'].map(id =>
            Ppt.run(`${baseUrl()}/slow?id=${id}&ms=20`, crawler(id), {})
        );
        await delay(80);
        assert.deepStrictEqual(arrivals.filter(a => a.indexOf('q') === 0), [],
            'queued requests must not touch the browser while the pool is full');

        // Release the holders one at a time; each release should wake exactly
        // the oldest waiter.
        holds.get('h1')();
        while (!arrivals.includes('q1')) await delay(5);
        holds.get('h2')();
        while (!arrivals.includes('q2')) await delay(5);

        // The remaining two wake as q1/q2 complete, still in order.
        await Promise.all([h1, h2].concat(queued));

        assert.deepStrictEqual(arrivals.filter(a => a.indexOf('q') === 0), ['q1', 'q2', 'q3', 'q4'],
            'queued requests must be served in submission order');
    });

    // -----------------------------------------------------------------------
    // 3. Navigation failure: returns {}, page goes back to the pool.
    // -----------------------------------------------------------------------
    test('returns {} after a failed navigation and keeps the pool full', async () => {
        resetCounters();
        const failed = await Ppt.run(`${baseUrl()}/abort`, crawler('abort'), {});
        assert.deepStrictEqual(failed, {}, 'a failed crawl must return an empty object');

        // Both slots must still be available afterwards.
        const results = await Promise.all(['a', 'b', 'c', 'd'].map(id =>
            Ppt.run(`${baseUrl()}/slow?id=after-${id}&ms=150`, crawler(id), {})
        ));
        results.forEach((r, i) => assert.strictEqual(r.title, 'page-after-' + ['a', 'b', 'c', 'd'][i]));
        await assertCapacity(LIMIT, 'cap-after-');
    });

    // -----------------------------------------------------------------------
    // 4. Runner script throws: returns {}, page goes back to the pool.
    // -----------------------------------------------------------------------
    test('returns {} when the runner script throws and keeps the pool full', async () => {
        resetCounters();
        const thrower = async page => {
            throw new Error('boom from runner');
        };
        const failed = await Ppt.run(`${baseUrl()}/slow?id=throw&ms=10`, thrower, {});
        assert.deepStrictEqual(failed, {}, 'a throwing runner must return an empty object');

        const results = await Promise.all(['e', 'f'].map(id =>
            Ppt.run(`${baseUrl()}/slow?id=after2-${id}&ms=150`, crawler(id), {})
        ));
        assert.strictEqual(results[0].title, 'page-after2-e');
        assert.strictEqual(results[1].title, 'page-after2-f');
        await assertCapacity(LIMIT, 'cap-throw-');
    });

    // -----------------------------------------------------------------------
    // 5. A borrower must never see a page another one is still using.
    //    Runner functions are serialised into a VM sandbox, so they only see
    //    `page`: the check is done with the page itself. If the pool lent a
    //    page that somebody else still holds, that other request would have
    //    navigated the shared page away and its url would change under it.
    // -----------------------------------------------------------------------
    test('never lends the same page to two requests at once', async () => {
        resetCounters();
        const flaky = async page => {
            const url = page.url();
            // Wait inside the page (the VM sandbox has no timers of its own).
            await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 25)));
            const urlAfter = page.url();
            if (urlAfter !== url) {
                // The page was navigated while we held it: it was shared.
                return { shared: true, url, urlAfter };
            }
            return { shared: false, url, urlAfter, title: await page.title() };
        };

        const results = await Promise.all(
            Array.from({ length: 12 }, (_, i) =>
                Ppt.run(`${baseUrl()}/slow?id=u${i}&ms=5`, flaky, {}))
        );

        results.forEach((r, i) => {
            assert.strictEqual(r.shared, false,
                'request ' + i + ' had its page navigated away while still borrowed');
            assert.ok(r.url.indexOf('id=u' + i) !== -1, 'request ' + i + ' navigated to ' + r.url);
            assert.strictEqual(r.title, 'page-u' + i, 'request ' + i + ' got ' + JSON.stringify(r));
        });
        assert.ok(maxInFlight <= LIMIT, 'at most ' + LIMIT + ' pages may load at once');
    });

    // -----------------------------------------------------------------------
    // 6. A page that dies while lent (here: the runner closes its own page,
    //    which is what an out-of-memory renderer crash looks like to the pool)
    //    must be replaced, not counted forever.
    // -----------------------------------------------------------------------
    test('replaces a dead page so capacity recovers', async () => {
        resetCounters();
        holds.clear();
        const suicide = async page => {
            await page.close();
            return { closed: page.isClosed() };
        };
        const died = await Ppt.run(`${baseUrl()}/slow?id=dead&ms=5`, suicide, {});
        assert.strictEqual(died.closed, true, 'the runner should have closed its page');

        // Pin both slots: if the dead page had not been replaced, the pool
        // would only have one slot and not all holders would reach the server.
        await assertCapacity(LIMIT, 'cap-dead-');
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

    await Ppt.close();
    try {
        fs.rmSync(userDataDir, { recursive: true, force: true, maxRetries: 3 });
    } catch (e) {
        // Best effort on Windows: the profile may still be locked briefly.
    }
    server.close();
    process.exit(failedCount ? 1 : 0);
})().catch(async e => {
    console.error('FATAL', e && e.stack ? e.stack : e);
    try {
        await Ppt.close();
    } catch (err) {
        // ignore
    }
    server.close();
    process.exit(1);
});
