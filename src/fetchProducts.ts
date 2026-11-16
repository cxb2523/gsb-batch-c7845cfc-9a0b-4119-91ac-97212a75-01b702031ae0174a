import {
  Category,
  productSchema,
  type ProductRequestOptions,
  type ProductSnapshot,
} from '../types/types.js'
import { camelCaseKeys } from './utils/camelCaseKeys.js'
import { endpoint, request } from './request.js'
import { parse } from 'valibot'

const DEFAULT_PAGE_SIZE = 99_999
const DEFAULT_MAX_PAGES = 100

/** A complete category snapshot, suitable for reconciliation by importers. */
export async function getProductSnapshot(
  category: `${Category}`,
  options: ProductRequestOptions = {},
): Promise<ProductSnapshot> {
  const storeCode = options.storeCode
  if (!Object.values(Category).includes(category as Category))
    throw new Error('Invalid product category')
  if (
    storeCode !== undefined &&
    (typeof storeCode !== 'string' || !/^\d{3}$/.test(storeCode))
  )
    throw new Error('Invalid store code')
  const pageSize = options.pageSize ?? DEFAULT_PAGE_SIZE
  const maxPages = options.maxPages ?? DEFAULT_MAX_PAGES
  if (!Number.isSafeInteger(pageSize) || pageSize <= 0)
    throw new Error('pageSize must be a positive integer')
  if (!Number.isSafeInteger(maxPages) || maxPages <= 0)
    throw new Error('maxPages must be a positive integer')

  const fetchedAt = new Date().toISOString()
  const items: unknown[] = []
  let total: number | null = null
  // Page through DoSearch so one truncated or failed page cannot silently
  // discard the rest of the category; any failure rejects the whole snapshot.
  for (let page = 0; ; page++) {
    if (page >= maxPages)
      throw new Error(
        `Vínbúðin snapshot exceeded the maximum of ${maxPages} pages`,
      )
    const url = new URL('DoSearch', endpoint)
    url.search = new URLSearchParams({
      skip: String(items.length),
      count: String(pageSize),
      category,
    }).toString()
    if (storeCode) url.searchParams.set('shop', storeCode)
    const raw = await request(url.href, options)
    if (
      !raw ||
      typeof raw !== 'object' ||
      !('data' in raw) ||
      !Array.isArray(raw.data) ||
      !('total' in raw) ||
      typeof raw.total !== 'number' ||
      !Number.isSafeInteger(raw.total) ||
      raw.total < 0 ||
      raw.data.length > pageSize
    ) {
      throw new Error('Incomplete or invalid Vínbúðin product snapshot')
    }
    if (total === null) total = raw.total
    else if (raw.total !== total)
      throw new Error('Vínbúðin product total changed during pagination')
    items.push(...raw.data)
    if (items.length >= total) break
    if (raw.data.length < pageSize)
      throw new Error('Incomplete or invalid Vínbúðin product snapshot')
  }
  if (items.length !== total)
    throw new Error('Incomplete or invalid Vínbúðin product snapshot')

  const seen = new Set<number>()
  const products = items.map((item) => {
    const product = parse(productSchema, camelCaseKeys(item), {
      abortEarly: true,
    })
    if (product.productCategory.name !== category)
      throw new Error('Product belongs to a different category')
    if (
      !Number.isSafeInteger(product.productId) ||
      product.productId <= 0 ||
      seen.has(product.productId)
    ) {
      throw new Error('Invalid or duplicate product ID')
    }
    seen.add(product.productId)
    const stock = product.productStoreSelected
    if (
      stock &&
      (stock.code !== storeCode ||
        !Number.isSafeInteger(stock.quantity) ||
        stock.quantity < 0)
    ) {
      throw new Error('Invalid stock or mismatched store code')
    }
    const photo = `${String(product.productId).padStart(5, '0')}_r.jpg`
    return {
      ...product,
      productImages: {
        original: `https://www.vinbudin.is/Portaldata/1/Resources/vorumyndir/original/${photo}`,
        medium: `https://www.vinbudin.is/Portaldata/1/Resources/vorumyndir/medium/${photo}`,
      },
    }
  })
  return {
    category,
    storeCode: storeCode ?? null,
    fetchedAt,
    total,
    products,
  }
}
