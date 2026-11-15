const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

const aggregate = require('../api/all');

const makeSite = (name, getData) => ({
    name,
    path: '/' + name.toLowerCase().replace(/\s+/g, '-'),
    category: '',
    getData,
});

const sampleArticles = (n) =>
    Array.from({ length: n }, (_, i) => ({
        title: `Artikel ${i + 1}`,
        link: `https://example.com/${i + 1}`,
        time: '2026-11-16 10:00',
    }));

test('mixed outcome: one throws, one error-object, one empty array, one undefined, one normal', async () => {
    const sites = [
        makeSite('Tribunnews', async () => {
            throw new Error('connect ECONNREFUSED');
        }),
        makeSite('CNN Indonesia', async () => ({
            error: {
                message: 'Tidak ada berita yang bisa diparsing dari halaman https://www.cnnindonesia.com',
                status: 502,
                code: 'BAD_GATEWAY',
            },
        })),
        makeSite('Tempo', async () => []),
        makeSite('Kompas', async () => undefined),
        makeSite('Liputan 6', async () => sampleArticles(3)),
    ];

    const body = await aggregate.getAll(sites);

    assert.equal(body.results.length, 5);
    assert.deepEqual(
        body.results.map((r) => r.name),
        ['Tribunnews', 'CNN Indonesia', 'Tempo', 'Kompas', 'Liputan 6']
    );

    // 1) Thrown exception is captured, not propagated.
    const tribun = body.results[0];
    assert.equal(tribun.status, 'error');
    assert.match(tribun.error.message, /ECONNREFUSED/);
    assert.equal(tribun.error.status, null);
    assert.equal(tribun.error.code, null);
    assert.equal(tribun.path, '/tribunnews');

    // 2) getData's own { error } object keeps helpers.errorResult fields.
    const cnn = body.results[1];
    assert.equal(cnn.status, 'error');
    assert.match(cnn.error.message, /Tidak ada berita/);
    assert.equal(cnn.error.status, 502);
    assert.equal(cnn.error.code, 'BAD_GATEWAY');

    // 3) Empty array is explicitly marked, not treated as a failure.
    const tempo = body.results[2];
    assert.equal(tempo.status, 'empty');
    assert.equal(tempo.count, 0);
    assert.deepEqual(tempo.data, []);
    assert.ok(tempo.message && tempo.message.includes('Tempo'));

    // 4) undefined return is caught as a locatable error.
    const kompas = body.results[3];
    assert.equal(kompas.status, 'error');
    assert.match(kompas.error.message, /undefined/);
    assert.equal(kompas.name, 'Kompas');

    // 5) Normal site keeps its full item array.
    const liputan = body.results[4];
    assert.equal(liputan.status, 'success');
    assert.equal(liputan.count, 3);
    assert.equal(liputan.data.length, 3);
    assert.equal(liputan.data[0].title, 'Artikel 1');
    assert.equal(liputan.data[2].link, 'https://example.com/3');

    assert.deepEqual(body.summary, {
        total: 5,
        success: 1,
        empty: 1,
        failed: 3,
        all_failed: false,
    });
});

test('getData returning { error: {} } without fields falls back to a usable message', async () => {
    const body = await aggregate.getAll([
        makeSite('Kompas', async () => ({ error: {} })),
        makeSite('Tempo', async () => sampleArticles(1)),
    ]);
    assert.equal(body.results[0].status, 'error');
    assert.equal(body.results[0].error.message, 'Unknown error');
    assert.equal(body.results[0].error.status, null);
    assert.equal(body.results[0].error.code, null);
    assert.equal(body.summary.all_failed, false);
});

test('all five sites failing: every site reports an error and all_failed is true', async () => {
    const sites = [
        makeSite('Tribunnews', async () => {
            throw new Error('timeout of 20000ms exceeded');
        }),
        makeSite('CNN Indonesia', async () => ({ error: { message: 'parse failed', status: 500, code: 'ERR' } })),
        makeSite('Tempo', async () => undefined),
        makeSite('Kompas', async () => {
            throw new Error('socket hang up');
        }),
        makeSite('Liputan 6', async () => ({ error: { message: 'empty page' } })),
    ];

    const body = await aggregate.getAll(sites);

    assert.equal(body.summary.failed, 5);
    assert.equal(body.summary.success, 0);
    assert.equal(body.summary.all_failed, true);
    for (const result of body.results) {
        assert.equal(result.status, 'error');
        assert.ok(typeof result.error.message === 'string' && result.error.message.length > 0);
        assert.ok('status' in result.error && 'code' in result.error);
        assert.ok(result.name && result.path);
    }
});

test('concurrent execution: total time tracks the slowest site, not the sum', async () => {
    const slowMs = 400;
    const fastMs = 50;

    const sites = [
        makeSite('Tribunnews', () => new Promise((resolve) => setTimeout(() => resolve(sampleArticles(1)), fastMs))),
        makeSite('CNN Indonesia', () => new Promise((resolve) => setTimeout(() => resolve(sampleArticles(1)), fastMs))),
        makeSite('Tempo', () => new Promise((resolve) => setTimeout(() => resolve(sampleArticles(1)), fastMs))),
        makeSite('Kompas', () => new Promise((resolve) => setTimeout(() => resolve(sampleArticles(1)), fastMs))),
        // One deliberately slow site.
        makeSite('Liputan 6', () => new Promise((resolve) => setTimeout(() => resolve(sampleArticles(2)), slowMs))),
    ];

    const start = Date.now();
    const body = await aggregate.getAll(sites);
    const elapsed = Date.now() - start;

    // Serial execution would take ~slowMs + 4*fastMs = 600ms. Concurrent
    // execution must finish close to the slowest single site (~400ms).
    assert.ok(elapsed >= slowMs - 50, `elapsed ${elapsed}ms should wait for the slowest site`);
    assert.ok(
        elapsed < slowMs + 150,
        `elapsed ${elapsed}ms should be near slowest-site ${slowMs}ms, not the serial sum ~600ms`
    );
    assert.equal(body.results.length, 5);
    assert.equal(body.summary.success, 5);
    assert.equal(body.results[4].count, 2);
});

test('default SITES registry covers the five real providers with their existing categories', () => {
    assert.equal(aggregate.SITES.length, 5);
    const byName = Object.fromEntries(aggregate.SITES.map((s) => [s.name, s]));
    for (const name of ['Tribunnews', 'CNN Indonesia', 'Tempo', 'Kompas', 'Liputan 6']) {
        assert.ok(byName[name], `missing site ${name}`);
        assert.equal(typeof byName[name].getData, 'function');
    }
    // Categories must match what the single-site routes already pass.
    assert.equal(byName['Tribunnews'].category, 'news');
    assert.equal(byName['CNN Indonesia'].category, '');
});

// --- HTTP-level tests: mock api/all via the require cache, then boot index.js ---

test('HTTP routes: partial failure -> 200, total failure -> 500, manifest lists /all', async (t) => {
    const allPath = require.resolve('../api/all');
    let queuedBody = null;
    const fakeModule = new Module(allPath, module);
    fakeModule.exports = {
        getAll: async () => queuedBody,
        SITES: [],
    };
    fakeModule.loaded = true;
    require.cache[allPath] = fakeModule;

    delete require.cache[require.resolve('../index.js')];
    const app = require('../index.js');
    t.after(() => {
        delete require.cache[allPath];
        delete require.cache[require.resolve('../index.js')];
    });

    const server = app.listen(0);
    await new Promise((resolve) => server.once('listening', resolve));
    t.after(() => server.close());

    const base = `http://127.0.0.1:${server.address().port}`;

    // Manifest registration on the root route.
    const manifestRes = await fetch(base + '/');
    assert.equal(manifestRes.status, 200);
    const manifest = await manifestRes.json();
    const aggregateEntry = manifest.api.find((entry) => entry.all === '/all');
    assert.ok(aggregateEntry, 'aggregate route must be registered in the / api listing');
    assert.equal(aggregateEntry.type, 'aggregate');

    // Partial failure still returns HTTP 200 with per-site details.
    queuedBody = {
        message: 'Agregasi daftar berita dari lima situs berita Indonesia',
        summary: { total: 5, success: 3, empty: 1, failed: 1, all_failed: false },
        results: [
            { name: 'Tribunnews', path: '/tribunnews', status: 'success', count: 2, data: sampleArticles(2) },
            { name: 'CNN Indonesia', path: '/cnnindonesia', status: 'empty', count: 0, data: [], message: 'no articles' },
            { name: 'Tempo', path: '/tempo', status: 'error', error: { message: 'boom', status: null, code: null } },
            { name: 'Kompas', path: '/kompas', status: 'success', count: 1, data: sampleArticles(1) },
            { name: 'Liputan 6', path: '/liputan6', status: 'success', count: 4, data: sampleArticles(4) },
        ],
    };
    const partialRes = await fetch(base + '/all');
    assert.equal(partialRes.status, 200);
    const partial = await partialRes.json();
    assert.equal(partial.results.length, 5);
    assert.equal(partial.results[2].status, 'error');
    assert.match(partial.results[2].error.message, /boom/);
    assert.equal(partial.results[1].status, 'empty');

    // Total failure returns HTTP 500 while keeping every per-site error.
    queuedBody = {
        message: 'Agregasi daftar berita dari lima situs berita Indonesia',
        summary: { total: 5, success: 0, empty: 0, failed: 5, all_failed: true },
        results: aggregate.SITES.map((site) => ({
            name: site.name,
            path: site.path,
            status: 'error',
            error: { message: `${site.name} down`, status: null, code: null },
        })),
    };
    const failedRes = await fetch(base + '/all');
    assert.equal(failedRes.status, 500);
    const failed = await failedRes.json();
    assert.equal(failed.results.length, 5);
    assert.equal(failed.summary.all_failed, true);
    for (const result of failed.results) {
        assert.equal(result.status, 'error');
        assert.match(result.error.message, /down/);
    }
});
