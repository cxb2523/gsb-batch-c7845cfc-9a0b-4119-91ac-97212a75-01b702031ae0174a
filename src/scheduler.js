// Page broker: lends a page to a crawl request and takes it back afterwards.
//
// Guarantees:
//  * At most `limit` pages are ever created/occupied at the same time.
//  * When every page is busy, callers are queued and woken strictly in FIFO
//    order, exactly once, as soon as a page is handed back.
//  * A page is released no matter how the request ended (success, navigation
//    error, script error). A release never shrinks the usable capacity: if the
//    page is still alive it goes back to the pool even when resetting it to
//    "about:blank" failed; if it is really gone (closed/crashed/hung) the pool
//    drops it and creates a replacement for the next waiter.
//  * A page handed to a caller is marked busy before the caller sees it, so the
//    same page can never be lent to two callers at once.
//  * Slot ids are never reused, so a stale or duplicated handle can never
//    release a page that currently belongs to another caller.

const IDLE = 0;
const BUSY = 1;
const PENDING = 2; // being created or being reset before going back to the pool

const DEFAULT_LIMIT = 100;
const DEFAULT_RESET_TIMEOUT = 10000;
const DEFAULT_CLOSE_TIMEOUT = 5000;

const isPageClosed = page => {
    try {
        return typeof page.isClosed === 'function' && !!page.isClosed();
    } catch (e) {
        return true;
    }
};

const has = (collection, value) => !!collection && collection.has(value);

// Run `promise`, but never wait longer than `ms`. The loser is swallowed so a
// hanging browser call cannot leave an unhandled rejection behind, and cannot
// keep the process alive.
const bounded = (promise, ms) => {
    if (typeof promise !== 'object' || typeof promise.then !== 'function') {
        return Promise.resolve({ ok: true, value: promise });
    }
    return new Promise(resolve => {
        let done = false;
        const timer = setTimeout(() => {
            if (done) return;
            done = true;
            resolve({ ok: false, timeout: true });
        }, ms);
        promise.then(
            value => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve({ ok: true, value });
            },
            error => {
                if (done) return;
                done = true;
                clearTimeout(timer);
                resolve({ ok: false, error });
            }
        );
    });
};

const pickNumber = (value, fallback, min) => {
    const n = typeof value === 'number' ? value : parseFloat(value);
    return (isFinite(n) && n >= min) ? n : fallback;
};

const PageBroker = function (browser, options = {}) {
    const usePagePool = !!options.pooling;
    const limit = Math.floor(pickNumber(options.limit, DEFAULT_LIMIT, 1));
    const resetTimeout = pickNumber(options.resetTimeout, DEFAULT_RESET_TIMEOUT, 1);
    const closeTimeout = pickNumber(options.closeTimeout, DEFAULT_CLOSE_TIMEOUT, 1);

    const slots = new Map(); // never-reused id -> { id, page, status }
    const waiters = []; // FIFO queue of { resolve, reject }
    const hooked = typeof WeakSet === 'function' ? new WeakSet() : null;
    const broken = typeof WeakSet === 'function' ? new WeakSet() : null;
    let nextId = 0;
    let leaseSeq = 0;
    let size = 0; // page slots that exist or are being created
    let draining = false;

    // Remember pages that reported a crash: puppeteer emits 'error' when the
    // renderer dies and such a page cannot be reused.
    const watch = page => {
        if (!hooked || !page || typeof page.on !== 'function' || hooked.has(page)) return;
        hooked.add(page);
        try {
            page.on('error', () => broken.add(page));
        } catch (e) {
            // Not an EventEmitter-like page; isClosed() still covers most cases.
        }
    };

    const isUsable = page => !!page && !has(broken, page) && !isPageClosed(page);

    // Locate the slot that currently owns a handle. The page identity wins
    // (a page can only belong to one slot), with the id as a fallback for
    // handles that no longer carry their page. Ids are never reused, so the id
    // alone can never point at another caller's page.
    const findOwnedSlot = ({ page, id } = {}) => {
        if (page) {
            for (const slot of slots.values()) {
                if (slot.page === page) return slot;
            }
            return null;
        }
        if (typeof id === 'number' && slots.has(id)) return slots.get(id);
        return null;
    };

    const allocateSlot = () => {
        const slot = { id: nextId++, page: null, status: PENDING, lease: 0 };
        slots.set(slot.id, slot);
        size++;
        return slot;
    };

    // Every lend stamps the slot with a fresh lease token. The caller must
    // present it to release the page, which is what makes a duplicate or very
    // late close() harmless even though the same Page object is reused later.
    const lend = slot => {
        slot.status = BUSY;
        slot.lease = ++leaseSeq;
        return { page: slot.page, id: slot.id, lease: slot.lease };
    };

    const removeSlot = slot => {
        if (!slot || !slots.has(slot.id) || slots.get(slot.id) !== slot) return;
        slots.delete(slot.id);
        size--;
    };

    const idleSlot = () => {
        for (const slot of slots.values()) {
            if (slot.status === IDLE && isUsable(slot.page)) return slot;
        }
        // Idle pages that died while nobody was watching are dropped here.
        for (const slot of slots.values()) {
            if (slot.status === IDLE) removeSlot(slot);
        }
        return null;
    };

    const createFor = async waiter => {
        // Take the capacity synchronously, before the first await, so a burst
        // of open() calls can never exceed `limit`.
        const slot = allocateSlot();
        let pending;
        try {
            pending = browser.newPage();
        } catch (e) {
            removeSlot(slot);
            waiter.reject(e);
            drain();
            return;
        }
        const created = await bounded(pending, resetTimeout);
        if (!created.ok) {
            removeSlot(slot);
            // If the page turns up after the timeout, it is an orphan: close it
            // rather than leaking it in the browser.
            if (created.timeout) {
                Promise.resolve(pending).then(page => discard(page), () => {});
            }
            waiter.reject(created.timeout
                ? new Error('Timed out while creating a page')
                : created.error);
            drain();
            return;
        }
        const page = created.value;
        watch(page);
        slot.page = page;
        waiter.resolve(lend(slot));
    };

    // Wake waiters in FIFO order, handing each the first idle page or, when the
    // pool still has room, a freshly created one.
    const drain = () => {
        if (draining) return;
        draining = true;
        try {
            while (waiters.length) {
                const slot = idleSlot();
                if (slot) {
                    waiters.shift().resolve(lend(slot));
                    continue;
                }
                if (size < limit) {
                    createFor(waiters.shift());
                    continue;
                }
                break;
            }
        } finally {
            draining = false;
        }
    };

    // Put a page back into a clean state. A failed reset alone is not enough
    // reason to throw the page away: only a page that is closed, crashed or
    // unresponsive is unusable.
    const resetPage = async page => {
        if (!isUsable(page)) return false;
        if (typeof page.goto !== 'function') return true;
        const reset = await bounded(page.goto('about:blank'), resetTimeout);
        if (reset.timeout) return false;
        // An error from goto('about:blank') is tolerated; the page is still
        // checked for liveness below.
        return isUsable(page);
    };

    const discard = async page => {
        if (!page || typeof page.close !== 'function') return;
        await bounded(page.close(), closeTimeout);
    };

    const open = async () => {
        if (!usePagePool) {
            return { page: await browser.newPage() };
        }
        return new Promise((resolve, reject) => {
            waiters.push({ resolve, reject });
            drain();
        });
    };

    const close = async ({ page, id, lease } = {}) => {
        if (!usePagePool) {
            await discard(page);
            return;
        }

        const slot = findOwnedSlot({ page, id });
        if (!slot) {
            // The page does not belong to the pool (or never did): make sure it
            // is not left dangling. A page currently lent to someone is always
            // found by identity above, so a live caller's page is not closed
            // behind its back.
            await discard(page);
            return;
        }

        // A handle with a stale lease is a duplicate/late release, or a handle
        // from before the page was re-lent: it must not touch the page now.
        if (typeof lease === 'number' && slot.lease !== lease) return;

        if (slot.status !== BUSY) return;

        const target = slot.page;
        slot.status = PENDING; // not lendable while it is being reset
        const reusable = await resetPage(target);
        if (!reusable) {
            // The page is dead or hung. Drop the slot so the capacity is
            // restored by creating a replacement for the next waiter.
            removeSlot(slot);
            await discard(target);
        } else if (slots.get(slot.id) === slot) {
            slot.status = IDLE;
        }
        drain();
    };

    // Read-only helpers for the tests and for operational visibility.
    const stats = () => {
        let idle = 0;
        let busy = 0;
        let pending = 0;
        for (const slot of slots.values()) {
            if (slot.status === IDLE) idle++;
            else if (slot.status === BUSY) busy++;
            else pending++;
        }
        return { limit, slots: slots.size, idle, busy, pending, waiting: waiters.length };
    };

    return { open, close, stats };
};

module.exports = {
    PageBroker
};
