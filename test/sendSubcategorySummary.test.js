const { test } = require('./harness');
const assert = require('assert').strict;

const sendSubcategorySummary = require('../src/utils/sendSubcategorySummary');

const makeIo = () => {
  const emitted = [];
  return {
    emitted,
    to: socketId => ({
      emit: (event, payload) => {
        emitted.push({ socketId, event, payload });
      },
    }),
  };
};

test('emits a settlement message with counts and failures', async () => {
  const io = makeIo();
  const deps = {
    getJobs: async () => [{ socketId: 'socket-1', appStatus: 'connected' }],
  };
  const summary = {
    category: 'Cat',
    subcategory: 'Sub',
    successCount: 17,
    failedCount: 3,
    failures: [
      { link: '/p-1', error: 'boom', category: 'Cat', subcategory: 'Sub' },
      { link: '/p-2', error: 'boom', category: 'Cat', subcategory: 'Sub' },
      { link: '/p-3', error: 'boom', category: 'Cat', subcategory: 'Sub' },
    ],
  };

  await sendSubcategorySummary(io, 42, 'EN', summary, deps);

  assert.equal(io.emitted.length, 1);
  const [{ socketId, event, payload }] = io.emitted;
  assert.equal(socketId, 'socket-1');
  // Dedicated event: it must not replace the reportGenerated data message.
  assert.equal(event, 'subcategorySummary');
  assert.notEqual(event, 'reportGenerated');
  assert.equal(payload.jobId, 42);
  assert.equal(payload.target, 'EN');
  assert.equal(payload.category, 'Cat');
  assert.equal(payload.subcategory, 'Sub');
  assert.equal(payload.successCount, 17);
  assert.equal(payload.failedCount, 3);
  assert.equal(payload.failures.length, 3);
});

test('sends an empty failure list when nothing failed', async () => {
  const io = makeIo();
  const deps = {
    getJobs: async () => [{ socketId: 'socket-1', appStatus: 'connected' }],
  };

  await sendSubcategorySummary(
    io,
    7,
    'EN',
    {
      category: 'Cat',
      subcategory: 'Sub',
      successCount: 4,
      failedCount: 0,
      failures: [],
    },
    deps
  );

  const [{ payload }] = io.emitted;
  assert.equal(payload.failedCount, 0);
  assert.deepEqual(payload.failures, []);
  assert.ok(Array.isArray(payload.failures));
});

test('does not emit while the front-end is disconnected', async () => {
  const io = makeIo();
  const deps = {
    getJobs: async () => [{ socketId: 'socket-1', appStatus: 'disconnected' }],
  };

  await sendSubcategorySummary(
    io,
    1,
    'EN',
    { successCount: 1, failedCount: 0, failures: [] },
    deps
  );

  assert.equal(io.emitted.length, 0);
});

test('a failing lookup never throws into the scraping loop', async () => {
  const io = makeIo();
  const deps = {
    getJobs: async () => {
      throw new Error('db is down');
    },
  };

  await assert.doesNotReject(() =>
    sendSubcategorySummary(
      io,
      1,
      'EN',
      { successCount: 0, failedCount: 1, failures: [] },
      deps
    )
  );
  assert.equal(io.emitted.length, 0);
});
