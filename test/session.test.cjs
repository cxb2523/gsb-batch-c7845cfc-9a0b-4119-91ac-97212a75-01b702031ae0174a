'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const api = require('../index.cjs');

/** Chromium-family markers that must never appear in a Safari UA. */
const CHROMIUM = /(Chrome\/|Chromium\/|Edg\/|EdgA\/|OPR\/|SamsungBrowser|CriOS)/;

test('createSession is exposed and returns a session object', () => {
  assert.equal(typeof api.createSession, 'function');
  const session = api.createSession(3);
  assert.equal(typeof session.next, 'function');
  assert.equal(typeof session.current, 'function');
  assert.equal(typeof session.headers, 'function');
  assert.equal(session.size, 3);
  assert.equal(session.round, 1);
  assert.equal(session.index, 0);
});

test('a round hands out count distinct UAs in order', () => {
  const session = api.createSession(5);
  const firstRound = [];
  for (let i = 0; i < 5; i += 1) {
    firstRound.push(session.next());
  }
  assert.equal(new Set(firstRound).size, 5, 'no UA may repeat within a round');
  for (const value of firstRound) {
    assert.equal(typeof value, 'string');
    assert.match(value, /^Mozilla\/5\.0/);
    assert.doesNotMatch(value, /[\r\n]/);
  }
});

test('exhausting a round wraps back to the first UA and starts a new round', () => {
  const session = api.createSession(4);
  const firstRound = Array.from({ length: 4 }, () => session.next());
  assert.equal(session.round, 2, 'the round counter advances after a full round');
  assert.equal(session.index, 0, 'the cursor wraps to the start of the pool');

  const secondRound = Array.from({ length: 4 }, () => session.next());
  assert.deepEqual(secondRound, firstRound, 'the same pool is replayed in order');
  assert.equal(new Set(secondRound).size, 4, 'the new round still has no repeats');
  assert.equal(session.round, 3);
});

test('round and index track the cursor position', () => {
  const session = api.createSession(2);
  assert.equal(session.round, 1);
  session.next();
  assert.equal(session.index, 1);
  session.next();
  assert.equal(session.round, 2);
  assert.equal(session.index, 0);
  session.next();
  assert.equal(session.round, 2);
  assert.equal(session.index, 1);
});

test('current() returns the last dispensed UA without advancing the cursor', () => {
  const session = api.createSession(3);
  // Before the first next() call current() points at the first pool UA.
  const first = session.current();
  assert.equal(typeof first, 'string');
  assert.equal(session.index, 0, 'current() must not advance the cursor');

  const dispensed = session.next();
  assert.equal(dispensed, first);
  assert.equal(session.current(), dispensed);
  assert.equal(session.current(), dispensed, 'current() is stable across calls');
});

test('constrained sessions honour browser/os/device semantics like generate()', () => {
  const safari = api.createSession(4, { browser: 'safari' });
  assert.equal(safari.size, 4);
  for (let i = 0; i < safari.size; i += 1) {
    const value = safari.next();
    assert.match(value, /Safari\//);
    assert.doesNotMatch(value, CHROMIUM, `safari session leaked a Chromium UA: ${value}`);
  }

  const firefox = api.createSession(3, { browser: 'firefox' });
  for (let i = 0; i < firefox.size; i += 1) {
    assert.match(firefox.next(), /Firefox\//);
  }

  const windows = api.createSession(3, { os: 'windows' });
  for (let i = 0; i < windows.size; i += 1) {
    assert.match(windows.next(), /Windows NT/);
  }

  const desktop = api.createSession(3, { device: 'desktop' });
  for (let i = 0; i < desktop.size; i += 1) {
    assert.doesNotMatch(desktop.next(), /(Mobile|iPhone|iPad|Android)/);
  }

  const mobile = api.createSession(3, { device: 'mobile' });
  for (let i = 0; i < mobile.size; i += 1) {
    assert.match(mobile.next(), /(Mobile|iPhone|iPad|Android)/);
  }
});

test('constraints stay honoured after the round wraps', () => {
  const edge = api.createSession(3, { browser: 'edge' });
  for (let round = 0; round < 3; round += 1) {
    for (let i = 0; i < edge.size; i += 1) {
      assert.match(edge.next(), /Edg\//);
    }
  }
});

test('unsatisfiable constraints degrade to unconstrained draws without throwing', () => {
  assert.doesNotThrow(() => api.createSession(5, {
    browser: 'netscape-navigator-3',
    os: 'beos',
  }));
  const session = api.createSession(5, { browser: 'netscape-navigator-3', os: 'beos' });
  assert.ok(session.size >= 1, 'the degraded session still has a usable pool');
  const values = Array.from({ length: session.size }, () => session.next());
  assert.equal(new Set(values).size, values.length, 'degraded UAs remain distinct per round');
  for (const value of values) {
    assert.equal(typeof value, 'string');
    assert.ok(value.length > 0);
  }
  // It must keep rotating after wrapping rather than getting stuck.
  const wrapped = session.next();
  assert.equal(typeof wrapped, 'string');
  assert.ok(wrapped.length > 0);
});

test('a device that matches nothing degrades instead of throwing', () => {
  const session = api.createSession(4, { device: 'smart-fridge' });
  assert.ok(session.size >= 1);
  for (let i = 0; i < session.size * 2; i += 1) {
    assert.ok(session.next().length > 0);
  }
});

test('createSession rejects a bad count with TypeError', () => {
  assert.throws(() => api.createSession(0), TypeError);
  assert.throws(() => api.createSession(-1), TypeError);
  assert.throws(() => api.createSession(1.5), TypeError);
  assert.throws(() => api.createSession(Number.POSITIVE_INFINITY), TypeError);
  assert.throws(() => api.createSession('4'), TypeError);
  assert.throws(() => api.createSession(null), TypeError);
  assert.throws(() => api.createSession(undefined), TypeError);
});

test('createSession rejects non-object options with TypeError', () => {
  assert.throws(() => api.createSession(2, 'chrome'), TypeError);
  assert.throws(() => api.createSession(2, ['chrome']), TypeError);
  assert.doesNotThrow(() => api.createSession(2));
  assert.doesNotThrow(() => api.createSession(2, null));
  assert.doesNotThrow(() => api.createSession(2, {}));
});

test('concurrent next() calls never repeat a UA inside a round', async () => {
  const session = api.createSession(10);
  const concurrentBurst = () =>
    Promise.all(Array.from({ length: session.size }, () => Promise.resolve().then(() => session.next())));

  const burstOne = await concurrentBurst();
  assert.equal(new Set(burstOne).size, session.size, 'each concurrent caller gets a unique UA');
  assert.equal(session.round, 2, 'the round only advances once the pool is exhausted');

  const burstTwo = await concurrentBurst();
  assert.equal(new Set(burstTwo).size, session.size);
  assert.deepEqual(new Set(burstTwo), new Set(burstOne), 'the next round replays the same pool');
  assert.equal(session.round, 3);
});

test('interleaved concurrent draws keep every round duplicate-free and never skip rounds', async () => {
  const size = 6;
  const session = api.createSession(size);
  const seenByRound = new Map();
  const roundsSeen = [];

  const draw = async () => {
    // Yield to the event loop between reads so the calls genuinely interleave.
    await Promise.resolve();
    const roundAtDraw = session.round;
    const value = session.next();
    if (!seenByRound.has(roundAtDraw)) seenByRound.set(roundAtDraw, new Set());
    seenByRound.get(roundAtDraw).add(value);
    roundsSeen.push(roundAtDraw);
  };

  const workers = Array.from({ length: 4 }, () => (async () => {
    for (let i = 0; i < size; i += 1) await draw();
  })());
  await Promise.all(workers);

  const totalRounds = (size * 4) / size;
  assert.equal(seenByRound.size, totalRounds, `exactly ${totalRounds} rounds must occur`);
  for (const [round, values] of seenByRound) {
    assert.equal(values.size, size, `round ${round} must contain ${size} distinct UAs`);
  }
  // Rounds are dispensed in order; no round may be skipped.
  const uniqueInOrder = [...new Set(roundsSeen)];
  assert.deepEqual(uniqueInOrder, [1, 2, 3, 4]);
});

test('session.headers() returns a usable header map for the current UA', () => {
  const session = api.createSession(3);
  const current = session.next();
  const headers = session.headers();
  assert.equal(headers['User-Agent'], current);
  assert.equal(typeof headers.Accept, 'string');
  assert.equal(typeof headers['Accept-Language'], 'string');
  assert.equal(typeof headers['Accept-Encoding'], 'string');
});

test('session.headers() works before the first next() call and does not move the cursor', () => {
  const session = api.createSession(2);
  const headers = session.headers();
  assert.equal(headers['User-Agent'], session.current());
  assert.equal(session.index, 0);
  assert.equal(session.round, 1);
});

test('session.headers() passes through an explicit safe UA without changing rotation', () => {
  const session = api.createSession(2);
  const before = session.index;
  const explicit = 'Mozilla/5.0 (X11; Linux x86_64) TestAgent/1.0';
  assert.equal(session.headers(explicit)['User-Agent'], explicit);
  assert.equal(session.index, before, 'an explicit header UA must not rotate the session');
});

test('session.headers() rejects header injection and unsafe values', () => {
  const session = api.createSession(2);
  const attacks = [
    'Mozilla/5.0\r\nX-Injected: 1',
    'Mozilla/5.0\nX-Injected: 1',
    'Mozilla/5.0\rX-Injected: 1',
    'Mozilla/5.0\r\n\r\nGET /admin HTTP/1.1',
    'agent\u0000null',
    'agent\u0007bell',
    '',
    'x'.repeat(513),
    42,
    null,
    {},
  ];
  for (const value of attacks) {
    assert.throws(() => session.headers(value), TypeError, `should reject ${JSON.stringify(value)}`);
  }
});

test('CJS and ESM entry points expose the same createSession implementation', async () => {
  const url = pathToFileURL(path.resolve(__dirname, '..', 'index.mjs')).href;
  const esm = await import(url);
  assert.equal(typeof esm.createSession, 'function');
  assert.equal(esm.createSession, api.createSession, 'same function reference in both entries');

  const session = esm.createSession(2);
  const values = [session.next(), session.next()];
  assert.equal(new Set(values).size, 2);
  assert.equal(typeof esm.default.createSession, 'function');
  assert.equal(esm.default.createSession, api.createSession);
});
