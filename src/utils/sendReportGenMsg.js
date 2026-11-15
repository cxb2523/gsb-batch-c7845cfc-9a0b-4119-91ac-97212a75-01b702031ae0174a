const logger = require('../config/logger.config');

async function sendReportGenMsg(io, jobId, target, msg) {
  try {
    // Required lazily so requiring the utils does not open a DB connection.
    const { getJobs } = require('../services/DBjobFunctions');
    const [job] = (await getJobs({ id: jobId })) || [];
    if (!job) return;

    const { socketId, appStatus } = job;
    if (appStatus === 'connected') {
      io.to(socketId).emit('reportGenStatus', { target, msg });
    }
  } catch (error) {
    // Progress reporting must never abort the scraping run.
    logger.error(
      `<sendReportGenMsg> Error sending progress message: ${
        error?.message || error
      }`
    );
  }
}

module.exports = sendReportGenMsg;
