const axios = require('axios');
const sources = require('../data/sources.json');
const getShortId = require('./getShortId');
const getDataFromCheerio = require('./getDataFromCheerio');
const getImage = require('./getImage');

// Fetch and parse a single source. Throws on request/parse failure —
// the caller (getArticles) isolates failures per source.
async function fetchSourceArticles(source) {
  const response = await axios.get(source.site);
  const returnedArticles = getDataFromCheerio(response.data);

  const sourceArticles = {};

  // fetch each article's image in parallel, and actually await them all
  await Promise.all(
    returnedArticles.map(async (returned) => {
      const key = getShortId(returned.url); // "hash" of the url which works as unique ID
      const articleUrl = source.base + returned.url;

      let image;
      try {
        image = await getImage(articleUrl);
      } catch (error) {
        // a missing image must not kill the article (or the whole source)
        image = undefined;
      }

      sourceArticles[key] = {
        title: returned.title,
        url: articleUrl,
        source: source.name,
        region: source.region,
        image: image,
      };
    })
  );

  return sourceArticles;
}

async function getArticles() {
  const articles = {};
  const sourceStatuses = {};

  // wait for EVERY source to settle before returning; one bad source
  // only skips itself, never the others
  const results = await Promise.all(
    sources.map(async (source) => {
      try {
        const sourceArticles = await fetchSourceArticles(source);
        const count = Object.keys(sourceArticles).length;
        return {
          source: source,
          articles: sourceArticles,
          status: count > 0 ? 'ok' : 'empty',
          count: count,
        };
      } catch (error) {
        console.log(`source "${source.name}" failed: ${error.message}`);
        return {
          source: source,
          articles: {},
          status: 'error',
          reason: error.message,
        };
      }
    })
  );

  for (const result of results) {
    Object.assign(articles, result.articles);

    sourceStatuses[result.source.name] =
      result.status === 'error'
        ? { status: 'error', reason: result.reason }
        : { status: result.status, count: result.count };
  }

  return { articles: articles, sources: sourceStatuses };
}

module.exports = { getArticles };
