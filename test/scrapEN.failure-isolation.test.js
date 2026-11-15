const { test } = require('./harness');
const assert = require('assert').strict;

const scrapEN = require('../src/controllers/scrapEN');
const getAllProductLinks = require('../src/services/getAllProductLinks');
const sendSubcategorySummary = require('../src/utils/sendSubcategorySummary');

const makeProduct = link => ({
  productID: `id-${link}`,
  productName: `name-${link}`,
  productDescription: `description-${link}`,
  properties: [
    { propertyName: 'Колір', propertyValue: 'чорний' },
    { propertyName: 'Розмір', propertyValue: 'M' },
  ],
});

const makeLinks = count => Array.from({ length: count }, (_, i) => `/p-${i}`);

// Builds the dependency set for scrapEN with fully controllable fake requests.
const setup = ({
  links,
  categoryName = 'Категорія',
  subcategoryName = 'Підкатегорія',
  failing = new Set(),
}) => {
  const calls = {
    productRequests: [],
    summaries: [],
    statusMessages: [],
  };

  const deps = {
    getCategoryData: async () => [{ name: categoryName, link: '/category' }],
    getSubcategoryData: async () => [
      { name: subcategoryName, link: '/subcategory' },
    ],
    getAllProductLinks: async () => links,
    getProductData: async link => {
      calls.productRequests.push(link);
      if (failing.has(link)) {
        throw new Error(`failed to load ${link}`);
      }
      return makeProduct(link);
    },
    sendReportGenMsg: async (io, jobId, target, msg) => {
      calls.statusMessages.push(msg);
    },
    sendSubcategorySummary: async (io, jobId, target, summary) => {
      calls.summaries.push(summary);
    },
  };

  return { deps, calls };
};

test('partial failure: only successful products produce rows', async () => {
  const links = makeLinks(5);
  const failing = new Set(['/p-1', '/p-3', '/p-4']);
  const { deps, calls } = setup({ links, failing });

  const result = await scrapEN({}, 1, deps);

  assert.equal(result.success, true);
  assert.equal(result.data.length, 2);

  const resultLinks = result.data.map(row => row.id);
  assert.deepEqual(resultLinks, ['id-/p-0', 'id-/p-2']);

  // Failed products must never appear as empty rows.
  result.data.forEach(row => {
    assert.equal(row.category, 'Категорія');
    assert.equal(row.subcategory, 'Підкатегорія');
    assert.ok(row.id);
    assert.ok(row.name);
    assert.ok(row.description);
    assert.equal(row['властивість-1'], 'Колір');
    assert.equal(row['значення-1'], 'чорний');
    assert.equal(row['властивість-2'], 'Розмір');
    assert.equal(row['значення-2'], 'M');
  });

  assert.equal(calls.summaries.length, 1);
  const [summary] = calls.summaries;
  assert.equal(summary.category, 'Категорія');
  assert.equal(summary.subcategory, 'Підкатегорія');
  assert.equal(summary.successCount, 2);
  assert.equal(summary.failedCount, 3);
  assert.deepEqual(
    summary.failures.map(f => f.link),
    ['/p-1', '/p-3', '/p-4']
  );
  summary.failures.forEach(failure => {
    assert.match(failure.error, /failed to load/);
    assert.equal(failure.category, 'Категорія');
    assert.equal(failure.subcategory, 'Підкатегорія');
  });

  assert.deepEqual(
    result.failures.map(f => f.link),
    ['/p-1', '/p-3', '/p-4']
  );
});

test('successful rows keep the original field structure', async () => {
  const { deps } = setup({ links: ['/p-0'] });

  const result = await scrapEN({}, 1, deps);

  assert.equal(result.data.length, 1);
  assert.deepEqual(result.data[0], {
    category: 'Категорія',
    subcategory: 'Підкатегорія',
    id: 'id-/p-0',
    name: 'name-/p-0',
    description: 'description-/p-0',
    'властивість-1': 'Колір',
    'значення-1': 'чорний',
    'властивість-2': 'Розмір',
    'значення-2': 'M',
  });
});

test('a fully failed batch does not stop following batches', async () => {
  const links = makeLinks(25);
  const failing = new Set(links.slice(0, 20)); // first batch (20) all fail
  const { deps, calls } = setup({ links, failing });

  const result = await scrapEN({}, 1, deps);

  // The second batch (5 links) must still be requested and collected.
  assert.equal(result.data.length, 5);
  assert.deepEqual(
    result.data.map(row => row.id),
    links.slice(20).map(link => `id-${link}`)
  );

  // Every link was requested exactly once: the run never stopped early.
  assert.equal(calls.productRequests.length, 25);
  assert.deepEqual(calls.productRequests, links);

  const [summary] = calls.summaries;
  assert.equal(summary.successCount, 5);
  assert.equal(summary.failedCount, 20);
  assert.deepEqual(
    summary.failures.map(f => f.link),
    links.slice(0, 20)
  );

  // Every failed product is also reported back to the caller.
  assert.deepEqual(
    result.failures.map(f => f.link),
    links.slice(0, 20)
  );
});

test('no failures: failure list is an empty array', async () => {
  const { deps, calls } = setup({ links: makeLinks(3) });

  const result = await scrapEN({}, 1, deps);

  assert.equal(result.data.length, 3);
  assert.equal(calls.summaries.length, 1);
  assert.equal(calls.summaries[0].successCount, 3);
  assert.equal(calls.summaries[0].failedCount, 0);
  assert.deepEqual(calls.summaries[0].failures, []);
  assert.deepEqual(result.failures, []);
});

test('every subcategory is settled even when all of its products fail', async () => {
  const calls = { summaries: [], productRequests: [] };
  const deps = {
    getCategoryData: async () => [{ name: 'Cat', link: '/category' }],
    getSubcategoryData: async () => [
      { name: 'Sub-A', link: '/sub-a' },
      { name: 'Sub-B', link: '/sub-b' },
    ],
    getAllProductLinks: async link =>
      link === '/sub-a' ? ['/a-0', '/a-1'] : ['/b-0'],
    getProductData: async link => {
      calls.productRequests.push(link);
      if (link.startsWith('/a-')) throw new Error('boom');
      return makeProduct(link);
    },
    sendReportGenMsg: async () => {},
    sendSubcategorySummary: async (io, jobId, target, summary) => {
      calls.summaries.push(summary);
    },
  };

  const result = await scrapEN({}, 1, deps);

  assert.equal(result.data.length, 1);
  assert.deepEqual(
    calls.summaries.map(s => s.subcategory),
    ['Sub-A', 'Sub-B']
  );
  assert.equal(calls.summaries[0].successCount, 0);
  assert.equal(calls.summaries[0].failedCount, 2);
  assert.equal(calls.summaries[1].successCount, 1);
  assert.equal(calls.summaries[1].failedCount, 0);
  assert.deepEqual(calls.summaries[1].failures, []);
});

test('a broken settlement sender does not abort remaining subcategories', async () => {
  const calls = { summaries: [] };
  const deps = {
    getCategoryData: async () => [{ name: 'Cat', link: '/category' }],
    getSubcategoryData: async () => [
      { name: 'Sub-A', link: '/sub-a' },
      { name: 'Sub-B', link: '/sub-b' },
    ],
    getAllProductLinks: async link => (link === '/sub-a' ? ['/a-0'] : ['/b-0']),
    getProductData: async link => makeProduct(link),
    sendReportGenMsg: async () => {},
    sendSubcategorySummary: async (io, jobId, target, summary) => {
      calls.summaries.push(summary.subcategory);
      if (summary.subcategory === 'Sub-A') throw new Error('socket down');
    },
  };

  const result = await scrapEN({}, 1, deps);

  assert.equal(result.success, true);
  assert.equal(result.data.length, 2);
  assert.deepEqual(calls.summaries, ['Sub-A', 'Sub-B']);
});

test('a page-link failure keeps already collected links for that subcategory', async () => {
  // Page 1 succeeds, page 2 throws: only page 1 links are collected and the
  // subcategory is still scraped/settled rather than dropped entirely.
  const requestedPages = [];
  const pageApi = {
    get: async url => {
      requestedPages.push(url);
      if (url.includes('PAGEN_1=1')) {
        return {
          data: [
            '<a class="product-item" href="/p-0"></a>',
            '<a class="product-item" href="/p-1"></a>',
          ].join(''),
        };
      }
      throw new Error('page 2 is down');
    },
  };

  const calls = { productRequests: [], summaries: [] };
  const deps = {
    getCategoryData: async () => [{ name: 'Cat', link: '/category' }],
    getSubcategoryData: async () => [{ name: 'Sub', link: '/sub' }],
    getAllProductLinks: link => getAllProductLinks(link, { en_api: pageApi }),
    getProductData: async link => {
      calls.productRequests.push(link);
      return makeProduct(link);
    },
    sendReportGenMsg: async () => {},
    sendSubcategorySummary: async (io, jobId, target, summary) => {
      calls.summaries.push(summary);
    },
  };

  const result = await scrapEN({}, 1, deps);

  assert.deepEqual(calls.productRequests, ['/p-0', '/p-1']);
  assert.equal(result.data.length, 2);
  assert.deepEqual(
    calls.summaries.map(s => [s.subcategory, s.successCount, s.failedCount]),
    [['Sub', 2, 0]]
  );
  assert.equal(requestedPages.length, 2);
  assert.equal(requestedPages[1], '/sub?PAGEN_1=2');
});

test('settlement message reaches the socket with counts and failures', async () => {
  const emitted = [];
  const io = {
    to: socketId => ({
      emit: (event, payload) => emitted.push({ socketId, event, payload }),
    }),
  };
  const webApi = {
    get: async url => {
      if (url.includes('PAGEN_1=1')) {
        return {
          data: ['/p-0', '/p-1', '/p-2']
            .map(link => `<a class="product-item" href="${link}"></a>`)
            .join(''),
        };
      }
      return { data: '' };
    },
  };

  const deps = {
    getCategoryData: async () => [{ name: 'Cat', link: '/category' }],
    getSubcategoryData: async () => [{ name: 'Sub', link: '/sub' }],
    getAllProductLinks: link => getAllProductLinks(link, { en_api: webApi }),
    getProductData: async link => {
      if (link === '/p-1') throw new Error('product page is down');
      return makeProduct(link);
    },
    sendReportGenMsg: async () => {},
    sendSubcategorySummary: (ioArg, jobId, target, summary) =>
      sendSubcategorySummary(ioArg, jobId, target, summary, {
        getJobs: async () => [{ socketId: 'socket-1', appStatus: 'connected' }],
      }),
  };

  const result = await scrapEN(io, 99, deps);

  assert.equal(result.data.length, 2);
  assert.equal(emitted.length, 1);
  const [{ socketId, event, payload }] = emitted;
  assert.equal(socketId, 'socket-1');
  // A dedicated settlement event — the data message stays separate.
  assert.equal(event, 'subcategorySummary');
  assert.equal(payload.jobId, 99);
  assert.equal(payload.target, 'EN');
  assert.equal(payload.category, 'Cat');
  assert.equal(payload.subcategory, 'Sub');
  assert.equal(payload.successCount, 2);
  assert.equal(payload.failedCount, 1);
  assert.deepEqual(
    payload.failures.map(f => f.link),
    ['/p-1']
  );
  assert.equal(payload.failures[0].category, 'Cat');
  assert.equal(payload.failures[0].subcategory, 'Sub');
  assert.match(payload.failures[0].error, /product page is down/);
});
