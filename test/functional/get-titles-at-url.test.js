import assert from 'node:assert/strict';
import {
  after,
  before,
  describe,
  test,
} from 'node:test';
import {builds} from '../helpers/builds.js';
import {startFixtureServer} from '../helpers/fixture-server.js';

let server;

before(async () => {
  server = await startFixtureServer();
});

after(async () => {
  await server.close();
});

for (const {name, lib} of builds) {
  const {getTitlesAtUrl, GetTitleError} = lib;

  describe(`getTitlesAtUrl (${name} build)`, () => {
    test('resolves every URL in input order, mixing successes and failures without rejecting', async () => {
      const urls = [
        `${server.url}/ok`,
        `${server.url}/not-found`,
        `${server.url}/no-title`,
        `${server.url}/json`,
        'not a url',
      ];
      const results = await getTitlesAtUrl(urls);
      assert.equal(results.length, urls.length);
      assert.deepEqual(results[0], {title: 'Fixture Page', url: `${server.url}/ok`, status: 200});
      assert.ok(results[1].error instanceof GetTitleError);
      assert.equal(results[1].error.code, 'HTTP_ERROR');
      assert.equal(results[1].status, 404);
      assert.equal(results[2].error.code, 'NO_TITLE');
      assert.equal(results[3].error.code, 'NOT_HTML');
      assert.equal(results[4].error.code, 'INVALID_URL');
    });

    test('an empty list, or no list at all, resolves to an empty list', async () => {
      assert.deepEqual(await getTitlesAtUrl([]), []);
      assert.deepEqual(await getTitlesAtUrl('not-an-array'), []);
    });

    test('duplicate URLs each get their own result', async () => {
      const results = await getTitlesAtUrl([`${server.url}/ok`, `${server.url}/ok`]);
      assert.deepEqual(results, [
        {title: 'Fixture Page', url: `${server.url}/ok`, status: 200},
        {title: 'Fixture Page', url: `${server.url}/ok`, status: 200},
      ]);
    });

    test('results stay in input order even when a later URL answers first', async () => {
      const results = await getTitlesAtUrl([`${server.url}/held`, `${server.url}/ok`]);
      assert.equal(results[0].title, 'Held Page');
      assert.equal(results[1].title, 'Fixture Page');
    });

    test('at most `concurrency` requests run at once', async () => {
      server.stats.maxConcurrent = 0;
      const urls = Array.from({length: 6}, () => `${server.url}/held`);
      const results = await getTitlesAtUrl(urls, {concurrency: 2});
      assert.ok(results.every(result => result.title === 'Held Page'));
      assert.equal(server.stats.maxConcurrent, 2);
    });

    test('the default concurrency is parallel but bounded', async () => {
      server.stats.maxConcurrent = 0;
      const urls = Array.from({length: 4}, () => `${server.url}/held`);
      await getTitlesAtUrl(urls);
      assert.ok(server.stats.maxConcurrent > 1, `maxConcurrent was ${server.stats.maxConcurrent}`);
      assert.ok(server.stats.maxConcurrent <= 8, `maxConcurrent was ${server.stats.maxConcurrent}`);
    });

    test('totalTimeout settles unfinished entries as TIMEOUT and keeps the finished ones', async () => {
      const results = await getTitlesAtUrl([`${server.url}/ok`, `${server.url}/slow`], {totalTimeout: 200});
      assert.equal(results[0].title, 'Fixture Page');
      assert.equal(results[1].error.code, 'TIMEOUT');
    });

    test('entries not yet started when totalTimeout fires settle as TIMEOUT too', async () => {
      const results = await getTitlesAtUrl([`${server.url}/slow`, `${server.url}/ok`], {concurrency: 1, totalTimeout: 200});
      assert.equal(results[0].error.code, 'TIMEOUT');
      assert.equal(results[1].error.code, 'TIMEOUT');
    });

    test('a NaN totalTimeout means no overall limit', async () => {
      const results = await getTitlesAtUrl([`${server.url}/ok`], {totalTimeout: NaN});
      assert.equal(results[0].title, 'Fixture Page');
    });

    test('per-entry options apply to every URL: timeout, headers, redirects and charset sniffing', async () => {
      const results = await getTitlesAtUrl([
        `${server.url}/record?id=batch`,
        `${server.url}/redirect-301`,
        `${server.url}/latin1`,
      ], {headers: {'x-batch-test': 'yes'}});
      assert.equal(results[0].title, 'Recorded');
      assert.equal(server.received.get('batch')['x-batch-test'], 'yes');
      assert.deepEqual(results[1], {title: 'Fixture Page', url: `${server.url}/ok`, status: 200});
      assert.equal(results[2].title, '“Café crème”');

      const timedOut = await getTitlesAtUrl([`${server.url}/slow`], {timeout: 50});
      assert.equal(timedOut[0].error.code, 'TIMEOUT');
    });

    test('an options object that throws when read settles its entries instead of rejecting', async () => {
      const results = await getTitlesAtUrl([`${server.url}/ok`], {
        get fetch() {
          throw new Error('boom');
        },
      });
      assert.equal(results[0].error.code, 'NETWORK_ERROR');
    });
  });
}
