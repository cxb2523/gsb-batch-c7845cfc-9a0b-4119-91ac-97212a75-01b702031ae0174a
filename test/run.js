// Local test entry point: `npm test`
const { run } = require('./harness');

require('./getAllProductLinks.pagination-failure.test');
require('./scrapEN.failure-isolation.test');
require('./sendSubcategorySummary.test');

run();
