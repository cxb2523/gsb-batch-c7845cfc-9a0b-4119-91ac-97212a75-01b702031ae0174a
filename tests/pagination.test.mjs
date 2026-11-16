import { test } from 'node:test'
import assert from 'node:assert/strict'
import * as vinbudin from '../dist/index.js'

// Synthetic upstream shape; tests never access the live site.
const beer = (overrides = {}) => ({
  ProductID: 2633,
  ProductName: 'Fixture lager',
  ProductBottledVolume: 330,
  ProductAlchoholVolume: 0,
  ProductPrice: 450,
  ProductFoodCategories: '',
  ProductCategory: {
    Name: 'beer',
    ID: ['60'],
    Taste: true,
    Sweet: false,
    SubCategories: null,
  },
  ProductSubCategory: null,
  ProductCountryOfOrigin: 'Ísland',
  ProductSpecialReserve: false,
  ProductOrganic: false,
  ProductContainerType: 'DS.',
  ProductPlaceOfOrigin: '',
  ProductDistrictOfOrigin: '',
  ProductWine: '',
  ProductInventory: 100,
  ProductYear: '',
  ProductDateOnMarket: '2026-11-05T00:00:00',
  ProductIsTemporaryOnSale: false,
  ProductIsGift: false,
  ProductIsInThema: false,
  ProductIsAvailableInStores: true,
  ProductIsSpecialOrder: false,
  ProductStoreSelected: null,
  ProductTasteGroup: '61BE',
  ProductTasteGroup2: 'PILSNER',
  ProductTasteGroup2Description: null,
  ProductPackagingClosing: '',
  ProductPackagingContainer: 'ALDOS',
  ProductSpecialMarking: [],
  ProductSeasonCode: 'JÓL',
  MinimumQuantity: 0,
  UseMinimumQuantityAsUnit: false,
  IsSpecialOrderAndOutOfStock: false,
  ProductSearchGrape: '',
  ProductProducer: 'Fixture producer',
  ProductShortDescription: 'Fixture beer',
  ProductBackupInventory: 0,
  ProductPackagingWeight: 0,
  ProductCarbonFootprint: 0,
  SupplierId: null,
  ProductAvailableUnits: '1',
  ProductSaleStatus: '1',
  ...overrides,
})
const response = (data, status = 200) =>
  new Response(JSON.stringify({ d: JSON.stringify(data) }), { status })
async function withFetch(handler, run) {
  const original = globalThis.fetch
  globalThis.fetch = handler
  try {
    await run()
  } finally {
    globalThis.fetch = original
  }
}
// Serves `pages` in order, keyed by the request's skip parameter.
const pager = (pages, { onRequest } = {}) => async (url) => {
  const params = new URL(url).searchParams
  onRequest?.(params)
  const skip = Number(params.get('skip'))
  const page = pages[skip / Number(params.get('count'))]
  assert.ok(page, `unexpected page request at skip=${skip}`)
  return page instanceof Error ? response({}, 503) : response(page)
}

test('paginated snapshots merge pages in upstream order with a matching total', async () => {
  const ids = [101, 102, 103, 104, 105]
  const pages = [
    { total: 5, data: [beer({ ProductID: 101 }), beer({ ProductID: 102 })] },
    { total: 5, data: [beer({ ProductID: 103 }), beer({ ProductID: 104 })] },
    { total: 5, data: [beer({ ProductID: 105 })] },
  ]
  const requests = []
  await withFetch(
    pager(pages, {
      onRequest: (params) =>
        requests.push({
          skip: params.get('skip'),
          count: params.get('count'),
          category: params.get('category'),
          shop: params.get('shop'),
        }),
    }),
    async () => {
      const snapshot = await vinbudin.getProductSnapshot('beer', {
        pageSize: 2,
        storeCode: '103',
      })
      assert.equal(snapshot.total, 5)
      assert.equal(snapshot.products.length, 5)
      assert.deepEqual(
        snapshot.products.map((p) => p.productId),
        ids,
      )
    },
  )
  assert.deepEqual(requests, [
    { skip: '0', count: '2', category: 'beer', shop: '103' },
    { skip: '2', count: '2', category: 'beer', shop: '103' },
    { skip: '4', count: '2', category: 'beer', shop: '103' },
  ])
})

test('a failing page rejects the whole snapshot instead of returning partial data', async () => {
  const pages = [
    { total: 4, data: [beer({ ProductID: 101 }), beer({ ProductID: 102 })] },
    new Error('upstream exploded'),
  ]
  await withFetch(
    pager(pages),
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 2 }),
        /503/,
      )
    },
  )
})

test('a truncated middle page rejects instead of silently dropping the tail', async () => {
  const pages = [
    { total: 5, data: [beer({ ProductID: 101 })] }, // short page, more promised
  ]
  await withFetch(
    pager(pages),
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 2 }),
        /Incomplete or invalid/,
      )
    },
  )
})

test('duplicate product IDs across pages reject the snapshot', async () => {
  const pages = [
    { total: 4, data: [beer({ ProductID: 101 }), beer({ ProductID: 102 })] },
    { total: 4, data: [beer({ ProductID: 102 }), beer({ ProductID: 103 })] },
  ]
  await withFetch(
    pager(pages),
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 2 }),
        /duplicate product ID/,
      )
    },
  )
})

test('an empty category returns an empty snapshot from a single first page', async () => {
  let requests = 0
  await withFetch(
    async () => {
      requests++
      return response({ total: 0, data: [] })
    },
    async () => {
      const snapshot = await vinbudin.getProductSnapshot('beer', {
        pageSize: 2,
      })
      assert.equal(snapshot.total, 0)
      assert.deepEqual(snapshot.products, [])
      assert.ok(Number.isFinite(Date.parse(snapshot.fetchedAt)))
    },
  )
  assert.equal(requests, 1)
})

test('a total that changes between pages rejects rather than truncating', async () => {
  const pages = [
    { total: 4, data: [beer({ ProductID: 101 }), beer({ ProductID: 102 })] },
    { total: 3, data: [beer({ ProductID: 103 })] },
  ]
  await withFetch(
    pager(pages),
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 2 }),
        /total changed/,
      )
    },
  )
})

test('exceeding the maximum page count rejects instead of looping forever', async () => {
  // Every page promises more but never delivers enough to finish.
  const page = { total: 1_000_000, data: [beer({ ProductID: 101 })] }
  let requests = 0
  await withFetch(
    async () => {
      requests++
      return response(page)
    },
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 1, maxPages: 3 }),
        /maximum of 3 pages/,
      )
    },
  )
  assert.equal(requests, 3)
})

test('invalid pagination options fail without fetching', async () => {
  await withFetch(
    () => {
      assert.fail('must not fetch')
    },
    async () => {
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 0 }),
      )
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { pageSize: 1.5 }),
      )
      await assert.rejects(
        vinbudin.getProductSnapshot('beer', { maxPages: 0 }),
      )
    },
  )
})
