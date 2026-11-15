const logger = require('../config/logger.config');

const SUBCATEGORY_SUMMARY_EVENT = 'subcategorySummary';

// Sends a per-subcategory settlement message to the connected front-end.
// It is additive: the reportGenerated message carrying the scraped rows is
// still emitted separately by the socket layer.
async function sendSubcategorySummary(io, jobId, target, summary, deps = {}) {
  try {
    // Required lazily so requiring the utils does not open a DB connection.
    const getJobs =
      deps.getJobs || require('../services/DBjobFunctions').getJobs;
    const [job] = (await getJobs({ id: jobId })) || [];
    if (!job) {
      logger.warn(
        `<sendSubcategorySummary> Job ${jobId} was not found, summary skipped`
      );
      return;
    }

    const { socketId, appStatus } = job;
    if (appStatus === 'connected') {
      io.to(socketId).emit(SUBCATEGORY_SUMMARY_EVENT, {
        jobId,
        target,
        ...summary,
      });
    }
  } catch (error) {
    // A failed summary must never abort the scraping run.
    logger.error(
      `<sendSubcategorySummary> Error sending summary: ${
        error?.message || error
      }`
    );
  }
}

sendSubcategorySummary.EVENT = SUBCATEGORY_SUMMARY_EVENT;

module.exports = sendSubcategorySummary;
