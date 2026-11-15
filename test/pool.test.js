// Unit tests for the page-pool borrow/return protocol (src/scheduler.js).
//
// A fake browser is used so the interleavings are deterministic: every page is
// a small object with the handful of methods PageBroker relies on, and the
// test can decide when a reset (goto('about:blank')) fails, when a page dies,
// or when newPage() is slow.
//
// Run with: node test/pool.test.js

const assert = require('assert');
const { PageBroker } = require('../src/scheduler');

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

const createFakePage = (id) => {
    const listeners = {};
    const page = {
        id,
        closed: false,
        // Knobs the tests flip on individual pages.
        failReset: false,
        resetDelay: 0,
        goto: async (url) => {
            if (page.resetDelay) await delay(page.resetDelay);
            if (page.closed) throw new Error('page ' + id + ' is closed');
            if (url === 'about:blank' && page.failReset) {
                throw new Error('navigation to about:blank failed on page ' + id);
            }
            return null;
        },
        isClosed: () => page.closed,
        on: (event, fn) => {
            (listeners[event] = listeners[event] || []).push(fn);
        },
        emit: (event, arg) => {
            (listeners[event] || []).slice().forEach(fn => fn(arg));
        },
        crash: () => {
            page.closed = true;
            page.emit('error', new Error('Page crashed!'));
        },
        close: async () => {
            page.closed = true;
            return null;
        }
    };
    return page;
};

const createFakeBrowser = (options = {}) => {
    const browser = {
        pages: [],
        created: 0,
        newPageCalls: 0,
        failNewPage: !!options.failNewPage,
        newPageDelay: options.newPageDelay || 0,
        // Pages that have been handed out since the last open(), used to spot
        // the same page being lent twice at the same time.
        newPage: async () => {
            browser.newPageCalls++;
            if (browser.newPageDelay) await delay(browser.newPageDelay);
            if (browser.failNewPage) throw new Error('newPage failed');
            const page = createFakePage(browser.created++);
            browser.pages.push(page);
            return page;
        }
    };
    return browser;
};

const tests = [];
const test = (name, fn) => tests.push({ name, fn });

// ---------------------------------------------------------------------------
// Queueing: once the pool is full, requests wait and are served FIFO.
// ---------------------------------------------------------------------------
test('queues requests when the pool is full and serves them in FIFO order', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 2 });

    const a = await broker.open();
    const b = await broker.open();
    assert.notStrictEqual(a.page, b.page, 'a page must never be lent twice');

    const order = [];
    const waiters = [0, 1, 2, 3].map(i =>
        broker.open().then(handle => {
            order.push(i);
            return handle;
        })
    );

    // Nothing can be served until a page is returned.
    await delay(30);
    assert.deepStrictEqual(order, [], 'waiting requests must stay queued while the pool is full');
    assert.strictEqual(browser.newPageCalls, 2, 'the pool must not grow past its limit');

    // Return one page at a time; the released page goes to the oldest waiter.
    const held = [a, b];
    for (let i = 0; i < waiters.length; i++) {
        await broker.close(held.shift());
        held.push(await waiters[i]);
    }

    assert.deepStrictEqual(order, [0, 1, 2, 3], 'waiters must be woken in submission order');
    assert.strictEqual(browser.newPageCalls, 2, 'only the first two requests may create pages');

    for (const handle of held) await broker.close(handle);
});

// ---------------------------------------------------------------------------
// One slot: each release wakes exactly the next waiter.
// ---------------------------------------------------------------------------
test('wakes exactly one waiter per returned page', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 1 });

    const order = [];
    let current = await broker.open();
    const waiters = [1, 2, 3].map(i => broker.open().then(handle => {
        order.push(i);
        return handle;
    }));

    for (let i = 0; i < waiters.length; i++) {
        await broker.close(current);
        current = await waiters[i];
        assert.ok(current && current.page, 'waiter ' + (i + 1) + ' should have received a page');
    }
    await broker.close(current);

    assert.deepStrictEqual(order, [1, 2, 3]);
    assert.strictEqual(browser.newPageCalls, 1, 'the same page must be reused');
});

// ---------------------------------------------------------------------------
// A failed reset must not shrink the pool while the page is still alive.
// ---------------------------------------------------------------------------
test('keeps capacity when a page reset fails but the page is alive', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 2 });

    const a = await broker.open();
    const b = await broker.open();
    a.page.failReset = true;

    await broker.close(a);
    await broker.close(b);

    const againA = await broker.open();
    const againB = await broker.open();
    assert.deepStrictEqual([againA.id, againB.id].sort(), [a.id, b.id].sort(),
        'the same two pages should be handed back out');
    assert.strictEqual(browser.newPageCalls, 2, 'a failed reset must not create a replacement page');

    await broker.close(againA);
    await broker.close(againB);
});

// ---------------------------------------------------------------------------
// A dead page is replaced; capacity recovers instead of leaking a slot.
// ---------------------------------------------------------------------------
test('replaces a dead page so capacity does not drop after a failure', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 2 });

    const a = await broker.open();
    const b = await broker.open();

    b.page.crash(); // renderer went away while the request was running

    await broker.close(b);
    await broker.close(a);

    // Both slots must be usable again: one reused page + one replacement.
    const x = await broker.open();
    const y = await broker.open();
    assert.notStrictEqual(x.page, y.page);
    assert.strictEqual(browser.newPageCalls, 3, 'the dead page should have been replaced exactly once');
    await broker.close(x);
    await broker.close(y);

    // ...and the pool is still full-sized afterwards.
    const z = await broker.open();
    const w = await broker.open();
    assert.ok(z.page && w.page);
    assert.ok(!z.page.closed && !w.page.closed);
    await broker.close(z);
    await broker.close(w);
});

// ---------------------------------------------------------------------------
// Failing newPage must not leak a slot or wedge the queue.
// ---------------------------------------------------------------------------
test('recovers when creating a page fails', async () => {
    const browser = createFakeBrowser({ failNewPage: true });
    const broker = PageBroker(browser, { pooling: true, limit: 1 });

    await assert.rejects(broker.open(), /newPage failed/);

    browser.failNewPage = false;
    const got = await broker.open();
    assert.ok(got.page, 'a request after a creation failure must still get a page');
    await broker.close(got);

    const reused = await broker.open();
    assert.strictEqual(reused.page, got.page);
    await broker.close(reused);
});

// ---------------------------------------------------------------------------
// close() is safe to call twice and with unknown/stale handles.
// ---------------------------------------------------------------------------
test('close is idempotent and tolerates unknown pages', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 1 });

    const a = await broker.open();
    await broker.close(a);
    await broker.close(a); // second release: no-op
    await broker.close({ id: a.id }); // stale id, no page
    await broker.close({ page: createFakePage(99), id: 0 }); // page the pool does not know
    await broker.close({});
    await broker.close();

    const b = await broker.open();
    assert.strictEqual(b.page, a.page, 'a stale close must not free a page that is in use');
    await broker.close(b);

    const c = await broker.open();
    assert.strictEqual(c.page, a.page);
    await broker.close(c);
});

// ---------------------------------------------------------------------------
// A no-pool broker still closes pages.
// ---------------------------------------------------------------------------
test('non-pooled broker closes the page it lent', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: false });

    const { page } = await broker.open();
    assert.strictEqual(page.closed, false);
    await broker.close({ page });
    assert.strictEqual(page.closed, true);
});

// ---------------------------------------------------------------------------
// Stress: a burst of concurrent borrowers never exceeds the limit and all of
// them finish; no page is ever lent to two borrowers at the same time.
// ---------------------------------------------------------------------------
test('serves a burst of concurrent requests without exceeding the limit', async () => {
    const browser = createFakeBrowser({ newPageDelay: 1 });
    const broker = PageBroker(browser, { pooling: true, limit: 3 });

    let busy = 0;
    let maxBusy = 0;
    const inUse = new Set();

    const tasks = [];
    for (let i = 0; i < 20; i++) {
        tasks.push((async () => {
            const handle = await broker.open();
            busy++;
            maxBusy = Math.max(maxBusy, busy);
            assert.ok(!inUse.has(handle.page), 'page lent to two requests at once');
            inUse.add(handle.page);
            await delay(i % 4);
            inUse.delete(handle.page);
            busy--;
            await broker.close(handle);
        })());
    }

    await Promise.all(tasks);
    assert.ok(maxBusy <= 3, 'at most 3 requests may run at once, saw ' + maxBusy);
    assert.strictEqual(maxBusy, 3, 'the pool should actually reach its limit under load');
    assert.strictEqual(browser.newPageCalls, 3, 'no more than limit pages should ever be created');
});

// ---------------------------------------------------------------------------
// A reset that hangs must not wedge the return path: the page is dropped and
// the slot is replaced, so queued requests keep moving.
// ---------------------------------------------------------------------------
test('does not hang when resetting a page never settles', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 1, resetTimeout: 30 });

    const a = await broker.open();
    a.page.goto = () => new Promise(() => {}); // navigation that never resolves

    const queued = broker.open();
    const started = Date.now();
    await broker.close(a);
    const next = await queued;

    assert.ok(Date.now() - started < 2000, 'close() must not wait forever on a hung reset');
    assert.notStrictEqual(next.page, a.page, 'a page with a hung reset must not be lent again');
    assert.strictEqual(browser.newPageCalls, 2);
    await broker.close(next);
});

// ---------------------------------------------------------------------------
// A hanging newPage() rejects the request instead of wedging the queue, and the
// reserved capacity is released for the next caller.
// ---------------------------------------------------------------------------
test('releases capacity when page creation hangs or fails', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 1, resetTimeout: 30 });

    browser.newPage = () => new Promise(() => {});
    await assert.rejects(broker.open(), /Timed out/);

    browser.newPage = createFakeBrowser().newPage;
    const got = await broker.open();
    assert.ok(got.page, 'the slot must be free again after a hung creation');
    await broker.close(got);
});

// ---------------------------------------------------------------------------
// Stale ids are never reused: releasing with an old id cannot free somebody
// else's page.
// ---------------------------------------------------------------------------
test('never reuses a slot id, so stale handles cannot free a live page', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 1 });

    const a = await broker.open();
    a.page.crash(); // forces the slot to be dropped and replaced
    await broker.close(a);

    const b = await broker.open();
    assert.notStrictEqual(b.id, a.id, 'the replacement must get a fresh id');

    // A duplicate of the old handle arrives late. It must not hand `b`'s page
    // back out (or reset it) while `b` is still in use.
    await broker.close({ id: a.id });

    const stillBusy = broker.stats();
    assert.strictEqual(stillBusy.busy, 1, 'the live page must stay lent');
    assert.strictEqual(stillBusy.waiting, 0);

    const queued = broker.open();
    await delay(20);
    assert.strictEqual(broker.stats().waiting, 1, 'the pool is still full');

    await broker.close(b);
    const c = await queued;
    assert.strictEqual(c.page, b.page);
    await broker.close(c);
});

// ---------------------------------------------------------------------------
// stats() reflects the borrow/return cycle.
// ---------------------------------------------------------------------------
test('reports pool state through stats()', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 2 });

    assert.deepStrictEqual(broker.stats(), { limit: 2, slots: 0, idle: 0, busy: 0, pending: 0, waiting: 0 });

    const a = await broker.open();
    const b = await broker.open();
    assert.deepStrictEqual(broker.stats(), { limit: 2, slots: 2, idle: 0, busy: 2, pending: 0, waiting: 0 });

    const queued = broker.open();
    assert.strictEqual(broker.stats().waiting, 1);

    await broker.close(a);
    const handed = await queued;
    assert.strictEqual(broker.stats().busy, 2);
    await broker.close(a); // stale duplicate: no-op
    assert.strictEqual(broker.stats().busy, 2);

    await broker.close(b);
    await broker.close(handed);
    assert.strictEqual(broker.stats().idle, 2);
});

// ---------------------------------------------------------------------------
// Same burst, but a third of the requests fail (nav error / script error)
// while holding a page. Capacity must not drop.
// ---------------------------------------------------------------------------
test('capacity does not drop when borrowers fail', async () => {
    const browser = createFakeBrowser();
    const broker = PageBroker(browser, { pooling: true, limit: 2 });

    for (let round = 0; round < 10; round++) {
        const handles = [await broker.open(), await broker.open()];
        for (let i = 0; i < handles.length; i++) {
            if (i % 2 === 0 && round % 3 === 0) handles[i].page.failReset = true;
            await broker.close(handles[i]);
        }
    }

    assert.strictEqual(browser.newPageCalls, 2, 'repeated failures must not erode the pool');
    const a = await broker.open();
    const b = await broker.open();
    assert.ok(a.page && b.page);
    await broker.close(a);
    await broker.close(b);
});

// ---------------------------------------------------------------------------

(async () => {
    let failed = 0;
    for (const { name, fn } of tests) {
        try {
            await fn();
            console.log('ok - ' + name);
        } catch (e) {
            failed++;
            console.error('not ok - ' + name);
            console.error(e && e.stack ? e.stack : e);
        }
    }
    console.log('\n' + (tests.length - failed) + '/' + tests.length + ' passed');
    process.exit(failed ? 1 : 0);
})();
