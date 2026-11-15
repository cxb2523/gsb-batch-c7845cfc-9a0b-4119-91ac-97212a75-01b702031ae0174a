const { test } = require('./harness');
const assert = require('assert').strict;

const getAllProductLinks = require('../src/services/getAllProductLinks');

const pageHtml = links =>
  links.map(link => `<a class="product-item" href="${link}"></a>`).join('');

// A controllable fake axios-like client: each page can succeed or throw.
const makeApi = pages => {
  const requested = [];
  return {
    requested,
    get: async url => {
      requested.push(url);
      const match = url.match(/PAGEN_1=(\d+)/);
      const page = match ? Number(match[1]) : 1;
      const pageConfig = pages[page];

      if (!pageConfig) {
        return { data: pageHtml([]) };
      }
      if (pageConfig.error) {
        throw new Error(pageConfig.error);
      }
      return { data: pageHtml(pageConfig.links) };
    },
  };
};

test('keeps collected links and stops paging when a page request fails', async () => {
  const api = makeApi({
    1: { links: ['/p-1', '/p-2'] },
    2: { error: 'network down' },
    // Even if paging continued, this page would return more links.
    3: { links: ['/p-3', '/p-4'] },
  });

  const links = await getAllProductLinks('/category', { en_api: api });

  assert.deepEqual(links, ['/p-1', '/p-2']);
  // Paging stopped at the failed page: page 3 was never requested.
  assert.deepEqual(api.requested, [
    '/category?PAGEN_1=1',
    '/category?PAGEN_1=2',
  ]);
});

test('returns an empty list instead of throwing when the first page fails', async () => {
  const api = makeApi({ 1: { error: 'connection refused' } });

  const links = await getAllProductLinks('/category', { en_api: api });

  assert.deepEqual(links, []);
  assert.deepEqual(api.requested, ['/category?PAGEN_1=1']);
});

test('normal pagination still stops on an empty page', async () => {
  const api = makeApi({
    1: { links: ['/p-1', '/p-2'] },
    2: { links: ['/p-3'] },
    3: { links: [] },
  });

  const links = await getAllProductLinks('/category', { en_api: api });

  assert.deepEqual(links, ['/p-1', '/p-2', '/p-3']);
  assert.deepEqual(api.requested, [
    '/category?PAGEN_1=1',
    '/category?PAGEN_1=2',
    '/category?PAGEN_1=3',
  ]);
});
