const cheerio = require('cheerio');

const axiosConfig = require('../services/axios-config');
const logger = require('../config/logger.config');

async function getProductLinksFromPage(pageUrl, deps = {}) {
  const api = deps.en_api || axiosConfig.en_api;
  try {
    const response = await api.get(pageUrl);
    const html = response.data;
    const $ = cheerio.load(html);

    const productLinks = [];
    $('a.product-item').each((index, element) => {
      const link = $(element).attr('href');
      productLinks.push(link);
    });

    return productLinks;
  } catch (error) {
    throw new Error('Error fetching product links: ' + error.message);
  }
}

async function getAllProductLinks(categoryLink, deps = {}) {
  const fetchPage = deps.getProductLinksFromPage || getProductLinksFromPage;
  const pageDeps = deps.en_api ? { en_api: deps.en_api } : {};

  let page = 1;
  let allProductLinks = [];

  while (true) {
    process.stdout.write(`${page}\r`);

    const pageUrl = `${categoryLink}?PAGEN_1=${page}`;
    let productLinks;

    try {
      productLinks = await fetchPage(pageUrl, pageDeps);
    } catch (error) {
      // Keep the links collected so far instead of dropping the whole
      // subcategory: a page failure only stops paging for this subcategory.
      logger.warn(
        `Stopping pagination for ${categoryLink} at page ${page}: ${
          error?.message || error
        }`
      );
      break;
    }

    if (productLinks[0] === allProductLinks[0] || productLinks.length === 0) {
      break; // No more pages, exit loop
    }

    allProductLinks = allProductLinks.concat(productLinks);
    page++;
  }

  return allProductLinks;
}

getAllProductLinks.getProductLinksFromPage = getProductLinksFromPage;

module.exports = getAllProductLinks;
