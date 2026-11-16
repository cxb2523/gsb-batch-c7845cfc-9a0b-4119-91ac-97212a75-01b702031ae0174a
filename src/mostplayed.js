'use strict';

const cheerio = require('cheerio')
  , getHtml = require('./gethtml.js')
  , heroIDMap = require('./heroids.json');

const MOST_PLAYED_CATEGORY = '0x0860000000000021';

function missingSection(message) {
  const error = new Error(message);
  error.code = 'MISSING_SECTION';
  return error;
}

module.exports = async (battletag, platform, html) => {

  const $ = cheerio.load(html || await getHtml(battletag, platform));

  const data = {
    quickplay: {},
    competitive: {}
  };

  // Presence of the most played container is what tells us the section exists;
  // profiles without data in a mode simply have no progress bars there.
  const sectionPresent = $('[data-category-id="' + MOST_PLAYED_CATEGORY + '"]').length > 0;

  ['competitive', 'quickplay'].forEach(type => {
    $('#' + type)
      .find('.career-section').first()
      .find('[data-category-id="' + MOST_PLAYED_CATEGORY + '"]').first()
      .find('.ProgressBar-title')
      .each(function() {
        const timePlayed = $(this).next().text();
        const imgURL = $(this).parent().parent().prev().attr('src');

        if (!imgURL) {
          // A progress bar without its hero image means the markup changed.
          return;
        }

        const heroID = imgURL.substring(imgURL.length - 22, imgURL.length - 4);
        const heroName = heroIDMap[heroID];
        if (!heroName) {
          return;
        }

        data[type][heroName] = { time: timePlayed, img: imgURL };
      });
  });

  if (!sectionPresent) {
    throw missingSection('mostPlayed section not found on page');
  }

  return data;

};
