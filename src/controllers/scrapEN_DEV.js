const services = require('../services');
const utils = require('../utils');
const logger = require('../config/logger.config');
const {
  TARGET,
  PRODUCTS_PER_BATCH,
  buildResultObject,
  fetchProductBatch,
} = require('./scrapEN');

const scrapEN_DEV = async (io, jobId, overrides = {}) => {
  const fetchCategoryData =
    overrides.getCategoryData || services.getCategoryData;
  const fetchSubcategoryData =
    overrides.getSubcategoryData || services.getSubcategoryData;
  const fetchProductLinks =
    overrides.getAllProductLinks || services.getAllProductLinks;
  const fetchProductData = overrides.getProductData || services.getProductData;

  const sendReportGenMsg = overrides.sendReportGenMsg || utils.sendReportGenMsg;
  const sendSubcategorySummary =
    overrides.sendSubcategorySummary || utils.sendSubcategorySummary;

  const fetchedData = [];
  const allFailures = [];
  logger.info('Scraping data in development mode');

  try {
    const categoryData = await fetchCategoryData('/uk/');

    //  for (const category of categoryData) {  //001

    const category = categoryData[10]; //001
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
                }, total ${batchLinks.length} / failed ${batch.failures.length}`
            );
          }

          batch.products.forEach(({ product }) => {
            fetchedData.push(
              buildResultObject(product, category.name, subcategory.name)
            );
            successCount++;
          });
        }
      }

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
    //  } //001

    // const [{ email }] = await getJobs({ id: jobId });
    // const { filename, dateString } = newFilename(
    //   `${email} EN ${jobId}`,
    //   'json'
    // );
    // const filePath = path.join(__dirname, 'public', filename);

    // fs.writeFileSync(filePath, JSON.stringify(fetchedData, null, 2), 'utf-8');

    // console.log('File saved successfully.');
    // `failures` is additive metadata; `data` keeps the original shape.
    return { success: true, data: fetchedData, failures: allFailures };
  } catch (error) {
    logger.error(
      `Error in <scrappEN-DEV> function :
        ${error?.message || error}`
    );
    return { succes: false, data: error.message, dateString: '' };
  }
};

module.exports = scrapEN_DEV;
