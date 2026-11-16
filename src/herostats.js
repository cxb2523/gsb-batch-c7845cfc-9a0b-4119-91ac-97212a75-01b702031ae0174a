'use strict';

const cheerio = require('cheerio')
  , getHtml = require('./gethtml.js')
  , heroIDMap = require('./heroids.json');

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

  // Presence of stat groups is what tells us the section exists. Zero means the
  // page is missing it (or its layout changed), which must not look like an
  // empty but successfully parsed profile.
  const sectionPresent = $('[data-group-id=stats]').length > 0;

  ['competitive', 'quickplay'].forEach(type => {

    // find stat groups (grouping per-hero or "overall")
    $('#' + type).find('[data-group-id=stats]').each(function() {

      // check for hero ID
      const heroID = $(this).attr('data-category-id');
      if (heroID) {

        // get the hero name
        const heroName = heroIDMap[heroID];
        if (heroName) {

          // hero stat object
          const hero = {
            hero_specific: {},
            combat: {},
            assists: {},
            best: {},
            average: {},
            game: {},
            miscellaneous: {},
            match_awards: {}
          };

          // look at the table data for info
          $(this).find('.stat-title').each(function() {
            // get the category of this info by looking at table header
            const category = $(this).text().replace(/\s+/g, '_').toLowerCase();
            if (!hero[category]) {
              // Unknown category: keep its stats instead of failing the block.
              hero[category] = {};
            }
            $(this).parent().parent().parent().next().children().each(function() {
              const row = $(this).children();
              const stat = row.first().text()
                .replace(/-/g, '') // remove - for "x - most in game" stats
                .replace(/\s+/g, '_') // replace spaces with underscores
                .toLowerCase();
              const result = row.last().text().replace(/,/g, '');
              hero[category][stat] = result;
            });
          });
          // then add all the stats for the hero to the main object
          data[type][heroName] = hero;
        }
      }
    });
  });

  if (!sectionPresent) {
    throw missingSection('heroStats section not found on page');
  }

  return data;

};
