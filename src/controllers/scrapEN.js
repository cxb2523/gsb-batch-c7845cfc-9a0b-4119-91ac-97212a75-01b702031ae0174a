const services = require('../services');
const utils = require('../utils');
const logger = require('../config/logger.config');

const TARGET = 'EN';
const PRODUCTS_PER_BATCH = 20;

// Turn one product into a report row. Kept in one place so successful rows
// keep exactly the same field structure as before.
const buildResultObject = (product, categoryName, subcategoryName) => {
  const resultObject = {
    category: categoryName,
    subcategory: subcategoryName,
    id: product.productID,
    name: product.productName,
    description: product.productDescription,
  };
  (product.properties || []).forEach((property, index) => {
    resultObject[`властивість-${index + 1}`] = property.propertyName;
    resultObject[`значення-${index + 1}`] = property.propertyValue;
  });
  return resultObject;
};

const getErrorMessage = error => {
  if (!error) return 'Unknown error';
  return error.message || String(error);
};

// Request every link of one batch concurrently. All promises are settled, so
// a single failure (or a whole failed batch) never rejects the batch itself.
const fetchProductBatch = async (links, fetchProductData) => {
  const settled = await Promise.all(
    links.map(link =>
      Promise.resolve()
        .then(() => fetchProductData(link))
        .then(
          product => ({ link, product, error: null }),
          error => ({ link, product: null, error })
        )
    )
  );

  const products = [];
  const failures = [];

  settled.forEach(({ link, product, error }) => {
    if (error || !product) {
      failures.push({
        link,
        error: error ? getErrorMessage(error) : 'Empty product data',
      });
      return;
    }
    products.push({ link, product });
  });

  return { products, failures };
};

const scrapEN = async (io, jobId, overrides = {}) => {
  const fetchCategoryData =
    overrides.getCategoryData || services.getCategoryData;
  const fetchSubcategoryData =
    overrides.getSubcategoryData || services.getSubcategoryData;
  const fetchProductLinks =
    overrides.getAllProductLinks || services.getAllProductLinks;
  const fetchProductData = overrides.getProductData || services.getProductData;

  // Resolved lazily at call time so the notifiers (and the DB layer they use)
  // can be replaced with fakes in tests.
  const sendReportGenMsg = overrides.sendReportGenMsg || utils.sendReportGenMsg;
  const sendSubcategorySummary =
    overrides.sendSubcategorySummary || utils.sendSubcategorySummary;

  const fetchedData = [];
  const allFailures = [];
  logger.info('🐙 Scraping data in PRODUCTION mode');

  try {
    const categoryData = await fetchCategoryData('/uk/');

    for (const category of categoryData) {
      console.log(`Category: ${category.name}`);

      const branch0 = {
        name: category.name,
        type:
          categoryData.indexOf(category) === categoryData.length - 1
            ? 'last'
            : 'middle',
        parent: 'last',
      };

      await sendReportGenMsg(io, jobId, TARGET, branch0);

      const subcategories = await fetchSubcategoryData(category.link);
      for (const subcategory of subcategories) {
        console.log(`- Subcategory: ${subcategory.name}`);
        const branch1 = {
          name: subcategory.name,
          type:
            subcategories.indexOf(subcategory) === subcategories.length - 1
              ? 'last'
              : 'middle',
          branch0: branch0.type === 'last' ? 'last' : 'middle',
        };
        await sendReportGenMsg(io, jobId, TARGET, branch1);

        const productLinks = await fetchProductLinks(subcategory.link);

        let successCount = 0;
        const failures = [];

        if (productLinks.length > 0) {
          const branch2 = {
            name: `Продуктів: ${productLinks.length}`,
            type: 'last',
            branch0: branch0.type === 'last' ? 'last' : 'middle',
            branch1: branch1.type === 'last' ? 'last' : 'middle',
          };

          await sendReportGenMsg(io, jobId, TARGET, branch2);

          // Batches are processed sequentially; a batch that fails entirely
          // must not stop the following ones.
          for (let i = 0; i < productLinks.length; i += PRODUCTS_PER_BATCH) {
            const batchLinks = productLinks.slice(i, i + PRODUCTS_PER_BATCH);

            const batch = await fetchProductBatch(batchLinks, fetchProductData);

            batch.failures.forEach(failure => {
              failures.push({
                ...failure,
                category: category.name,
                subcategory: subcategory.name,
              });
            });

            if (batch.failures.length > 0) {
              logger.warn(
                `Some product requests failed in "${category.name}" / ` +
                  `"${subcategory.name}": batch ${
                    Math.floor(i / PRODUCTS_PER_BATCH) + 1
                  }, total ${batchLinks.length} / failed ${
                    batch.failures.length
                  }`
              );
            }

            // Only successfully fetched products produce report rows.
            batch.products.forEach(({ product }) => {
              fetchedData.push(
                buildResultObject(product, category.name, subcategory.name)
              );
              successCount++;
            });
          }
        }

        // Per-subcategory settlement: sent even when every product failed.
        // A broken notifier must not abort the remaining subcategories.
        try {
          await sendSubcategorySummary(io, jobId, TARGET, {
            category: category.name,
            subcategory: subcategory.name,
            successCount,
            failedCount: failures.length,
            failures,
          });
        } catch (summaryError) {
          logger.error(
            `Error sending settlement for "${category.name}" / ` +
              `"${subcategory.name}": ${summaryError?.message || summaryError}`
          );
        }

        allFailures.push(...failures);
      }
    } //001
    // `failures` is additive metadata; `data` keeps the original shape.
    return { success: true, data: fetchedData, failures: allFailures };
  } catch (error) {
    logger.error(
      `Error in <scrapEN> function:
        ${error?.message || error}`
    );
    return { succes: false, data: error.message, dateString: '' };
  }
};

scrapEN.TARGET = TARGET;
scrapEN.PRODUCTS_PER_BATCH = PRODUCTS_PER_BATCH;
scrapEN.buildResultObject = buildResultObject;
scrapEN.fetchProductBatch = fetchProductBatch;

module.exports = scrapEN;
