'use strict';

const getHtml = require('./gethtml.js')
  , getBasicInfo = require('./basicinfo.js')
  , getHeroStats = require('./herostats.js')
  , getMostPlayed = require('./mostplayed.js');

function describeError(error) {
  if (!error) {
    return 'unknown error';
  }

  if (typeof error === 'string') {
    return error;
  }

  return error.message || String(error);
}

/**
 * Resolves one part of the profile in isolation.
 * A failure here only marks that part as missing; the rest of the profile is
 * still delivered. Page-level failures (not found, private, rate limited) are
 * raised before this point and still reject the whole call.
 */
async function settle(part, parser, battletag, platform, html, errors) {
  try {
    return await parser(battletag, platform, html);
  } catch (error) {
    errors[part] = describeError(error);
    return null;
  }
}

async function getAllStats(battletag, platform, options, retryDelay) {

  // Resolved through module.exports so tests/embedders can substitute the
  // fetching layer on the public object.
  const html = await module.exports.getHtml(battletag, platform, options, retryDelay);

  const errors = {};

  // Each section settles independently. A broken block only nulls that part
  // and is recorded in `errors`; it never discards the parts that did work.
  const basicInfo = await settle('basicInfo', module.exports.getBasicInfo, battletag, platform, html, errors);
  const heroStats = await settle('heroStats', module.exports.getHeroStats, battletag, platform, html, errors);
  const mostPlayed = await settle('mostPlayed', module.exports.getMostPlayed, battletag, platform, html, errors);

  return {
    // Basic info has historically been spread into the top level; keep that
    // exact shape and also expose it under its own key so all three sections
    // settle the same way (null when the section could not be collected).
    ...(basicInfo || {}),
    basicInfo,
    heroStats,
    mostPlayed,
    errors
  };

}

module.exports = {
  getAllStats: getAllStats,
  getBasicInfo: getBasicInfo,
  getHeroStats: getHeroStats,
  getMostPlayed: getMostPlayed,
  getHtml: getHtml
};
