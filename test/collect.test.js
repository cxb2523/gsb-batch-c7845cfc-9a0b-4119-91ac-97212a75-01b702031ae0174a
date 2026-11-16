'use strict';

// Tests the collection layer (getHtml retries + getAllStats partial results)
// using locally constructed HTML and a controllable fetch double.
// No request ever reaches the real Blizzard site.

const assert = require('assert');
const ow = require('../src/index.js');
const getHtml = require('../src/gethtml.js');

const BORDER_URL = 'https://d15f34w2p8l1cc.cloudfront.net/overwatch/ba68d2c0f1b55e1991161cb1f88f369b97311452564b200ea1da226eb493e2e8.png'; // Silver Lv 41 => 6
const STARS_URL = 'https://d15f34w2p8l1cc.cloudfront.net/overwatch/426c754c76cd12e6aacd30293a67363571341eea37880df549d3e02015a588fe.png'; // 1 Silver star => +1
const RANK_URL = 'https://d1u1mce87gyfbn.cloudfront.net/game/rank-icons/rank-GrandmasterTier.png';
const ROLE_URL = 'https://static.playoverwatch.com/img/pages/career/icon-tank-8a52daaf01.png';

function styleBackground(url) {
  return `background-image:url(${url})`;
}

function basicInfoBlock(options = {}) {
  const playerLevel = options.withPlayerLevel === false
    ? ''
    : `<div class="player-level" style="${styleBackground(BORDER_URL)}"><div>42</div></div>`;
  const playerRank = options.withStars
    ? `<div class="player-rank" style="${styleBackground(STARS_URL)}"></div>`
    : '';

  return `
    <div class="masthead-player">
      <img class="player-portrait" src="https://example.invalid/portrait.png">
      ${playerLevel}
      ${playerRank}
      <div class="endorsement-level">Endorsement Level</div>
      <div class="endorsement-value">3</div>
      <div class="EndorsementIcon-border--shotcaller" data-value="0.22"></div>
      <div class="EndorsementIcon-border--teammate" data-value="0.58"></div>
      <div class="EndorsementIcon-border--sportsmanship" data-value="0.20"></div>
      <div class="competitive-rank-role">
        <img class="competitive-rank-role-icon" src="${ROLE_URL}">
        <span class="competitive-rank-tier-tooltip" data-ow-tooltip-text="Tank Tier"></span>
        <img class="competitive-rank-tier-icon" src="${RANK_URL}">
        <div class="competitive-rank-level">3556</div>
      </div>
    </div>
  `;
}

// Mirrors the page structure the parser walks: the stat title's third ancestor
// is followed by the sibling table holding the stat rows.
function heroStatsGroup(heroID, eliminations) {
  return `
    <div data-group-id="stats" data-category-id="${heroID}">
      <div class="stat-category">
        <div class="stat-header">
          <div class="stat-sub"><div class="stat-title">Combat</div></div>
        </div>
      </div>
      <div class="stat-table">
        <div class="stat-row"><div class="stat-name">Eliminations</div><div class="stat-value">${eliminations}</div></div>
        <div class="stat-row"><div class="stat-name">Time Spent on Fire - Most in Game</div><div class="stat-value">01:23</div></div>
      </div>
    </div>
  `;
}

function mostPlayedSection(heroID, heroName, time) {
  return `
    <div class="career-section">
      <div data-category-id="0x0860000000000021">
        <div class="progress-row">
          <img src="https://example.invalid/heroes/${heroID}.png">
          <div class="progress-inner">
            <div class="progress-text">
              <div class="ProgressBar-title">${heroName}</div>
              <div class="ProgressBar-time">${time}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `;
}

/**
 * Builds a career page that mirrors the real one: a masthead plus, per mode, a
 * hero stats group and a most played section inside the same mode container.
 */
function buildPage(options = {}) {
  const competitive = `
    <div id="competitive">
      ${options.heroStats === false ? '' : heroStatsGroup('0x02E0000000000002', '1,234')}
      ${options.mostPlayed === false ? '' : mostPlayedSection('0x02E00000000000DD', 'Mei', '16:05:18')}
    </div>
  `;

  const quickplay = `
    <div id="quickplay">
      ${options.heroStats === false ? '' : heroStatsGroup('0x02E0000000000003', '500')}
      ${options.mostPlayed === false ? '' : mostPlayedSection('0x02E0000000000002', 'Reaper', '02:00:00')}
    </div>
  `;

  return `<!doctype html><html><body>
    ${basicInfoBlock(options)}
    ${competitive}
    ${quickplay}
  </body></html>`;
}

function okResponse(body) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    headers: { get: () => null },
    text: async () => body
  };
}

function errorResponse(status, statusText = '', headers = {}) {
  const normalized = {};
  Object.keys(headers).forEach(key => {
    normalized[key.toLowerCase()] = String(headers[key]);
  });

  return {
    ok: false,
    status,
    statusText,
    headers: { get: name => normalized[String(name).toLowerCase()] || null },
    text: async () => ''
  };
}

/** Returns a fetch double that hands out the supplied responses in order. */
function makeFetch(responses) {
  const calls = [];
  const fake = async url => {
    calls.push(url);
    const next = responses.shift();
    if (!next) {
      throw new Error('fetch double ran out of responses');
    }
    return next;
  };
  fake.calls = calls;
  return fake;
}

const FAST = { retries: 2, retryDelay: 1 };

const tests = [];

function test(name, fn) {
  tests.push({ name, fn });
}

test('normal page: every section is parsed and errors is empty', async () => {
  const fake = makeFetch([okResponse(buildPage({ withStars: true }))]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(fake.calls.length, 1);
  assert.strictEqual(fake.calls[0], 'https://playoverwatch.com/en-us/career/pc/Test-1234');

  // basicInfo
  assert.strictEqual(stats.battletag, 'Test-1234');
  assert.strictEqual(stats.level, '42');
  assert.strictEqual(stats.borderURL, BORDER_URL);
  assert.strictEqual(stats.starsURL, STARS_URL);
  assert.strictEqual(stats.prestige, 7, 'border 6 + one silver star 1');
  assert.strictEqual(stats.endorsementLevel, '3');
  assert.strictEqual(stats.endorsements.shotcaller, '22');
  assert.deepStrictEqual(stats.rank.tank, {
    sr: '3556',
    roleIcon: ROLE_URL,
    tierIcon: RANK_URL
  });
  assert.strictEqual(stats.profileURL, 'https://playoverwatch.com/en-us/career/pc/Test-1234');
  assert.strictEqual(stats.basicInfo.level, '42', 'basic info also settles as its own section');

  // heroStats
  assert.ok(stats.heroStats.competitive.reaper);
  assert.strictEqual(stats.heroStats.competitive.reaper.combat.eliminations, '1234');
  assert.strictEqual(
    stats.heroStats.competitive.reaper.combat.time_spent_on_fire_most_in_game,
    '01:23'
  );
  assert.ok(stats.heroStats.quickplay.tracer);
  assert.strictEqual(stats.heroStats.quickplay.tracer.combat.eliminations, '500');

  // mostPlayed
  assert.deepStrictEqual(stats.mostPlayed.competitive.mei, {
    time: '16:05:18',
    img: 'https://example.invalid/heroes/0x02E00000000000DD.png'
  });
  assert.strictEqual(stats.mostPlayed.quickplay.reaper.time, '02:00:00');

  assert.deepStrictEqual(stats.errors, {});
});

test('missing heroStats block: that section is null, the rest is complete', async () => {
  const fake = makeFetch([okResponse(buildPage({ heroStats: false }))]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(stats.heroStats, null);
  assert.strictEqual(typeof stats.errors.heroStats, 'string');
  assert.ok(stats.errors.heroStats.length > 0);
  assert.strictEqual(stats.errors.mostPlayed, undefined);
  assert.strictEqual(stats.errors.basicInfo, undefined);

  assert.strictEqual(stats.level, '42');
  assert.ok(stats.mostPlayed.competitive.mei);
  assert.ok(stats.mostPlayed.quickplay.reaper);
});

test('missing mostPlayed block: that section is null, the rest is complete', async () => {
  const fake = makeFetch([okResponse(buildPage({ mostPlayed: false }))]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(stats.mostPlayed, null);
  assert.strictEqual(typeof stats.errors.mostPlayed, 'string');
  assert.strictEqual(stats.errors.heroStats, undefined);
  assert.strictEqual(stats.errors.basicInfo, undefined);
  assert.ok(stats.heroStats.competitive.reaper);
  assert.strictEqual(stats.level, '42');
});

test('missing basicInfo nodes: borderURL and prestige degrade to empty strings', async () => {
  const fake = makeFetch([okResponse(buildPage({ withPlayerLevel: false }))]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(stats.borderURL, '');
  assert.strictEqual(stats.prestige, '');
  // Field names and types are unchanged.
  assert.strictEqual(typeof stats.borderURL, 'string');
  assert.strictEqual(typeof stats.prestige, 'string');
  assert.strictEqual(stats.level, '');
  assert.ok(stats.heroStats);
  assert.ok(stats.mostPlayed);
  assert.deepStrictEqual(stats.errors, {});
});

test('429 followed by success is retried and returns the full result', async () => {
  const fake = makeFetch([
    errorResponse(429, 'Too Many Requests'),
    okResponse(buildPage())
  ]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(fake.calls.length, 2, 'retried exactly once');
  assert.strictEqual(stats.level, '42');
  assert.ok(stats.heroStats);
  assert.ok(stats.mostPlayed);
  assert.deepStrictEqual(stats.errors, {});
});

test('5xx followed by success is retried and returns the full result', async () => {
  const fake = makeFetch([
    errorResponse(503, 'Service Unavailable'),
    errorResponse(500, 'Internal Server Error'),
    okResponse(buildPage())
  ]);
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(fake.calls.length, 3);
  assert.strictEqual(stats.level, '42');
});

test('persistent 429 rejects with the status code attached', async () => {
  const fake = makeFetch([
    errorResponse(429, 'Too Many Requests'),
    errorResponse(429, 'Too Many Requests'),
    errorResponse(429, 'Too Many Requests')
  ]);
  getHtml.fetch = fake;

  await assert.rejects(
    ow.getAllStats('Test-1234', 'pc', FAST),
    err => {
      assert.strictEqual(err.status, 429);
      assert.strictEqual(err.statusCode, 429);
      assert.ok(/429/.test(err.message), 'message carries the status code');
      return true;
    }
  );

  assert.strictEqual(fake.calls.length, 3, 'initial attempt plus the two configured retries');
});

test('persistent 5xx rejects with the status code attached', async () => {
  const fake = makeFetch([
    errorResponse(502, 'Bad Gateway'),
    errorResponse(502, 'Bad Gateway')
  ]);
  getHtml.fetch = fake;

  await assert.rejects(
    ow.getAllStats('Test-1234', 'pc', { retries: 1, retryDelay: 1 }),
    err => {
      assert.strictEqual(err.status, 502);
      assert.ok(/502/.test(err.message));
      return true;
    }
  );

  assert.strictEqual(fake.calls.length, 2);
});

test('other non-2xx responses still throw PROFILE_NOT_FOUND', async () => {
  const fake = makeFetch([errorResponse(404, 'Not Found')]);
  getHtml.fetch = fake;

  await assert.rejects(ow.getAllStats('Test-1234', 'pc', FAST), err => {
    assert.strictEqual(err.message, 'PROFILE_NOT_FOUND');
    assert.strictEqual(fake.calls.length, 1, 'not retried');
    return true;
  });
});

test('private profiles still throw PROFILE_PRIVATE', async () => {
  const page = buildPage().replace(
    '<div class="masthead-player">',
    '<div class="masthead-player"><div class="masthead-permission-level-text">Private Profile</div>'
  );
  const fake = makeFetch([okResponse(page)]);
  getHtml.fetch = fake;

  await assert.rejects(ow.getAllStats('Test-1234', 'pc', FAST), err => {
    assert.strictEqual(err.message, 'PROFILE_PRIVATE');
    return true;
  });
});

test('retries can be disabled and calls stay bounded', async () => {
  const fake = makeFetch([errorResponse(429, 'Too Many Requests')]);
  getHtml.fetch = fake;

  await assert.rejects(
    ow.getAllStats('Test-1234', 'pc', { retries: 0, retryDelay: 1 }),
    err => {
      assert.strictEqual(err.status, 429);
      return true;
    }
  );

  assert.strictEqual(fake.calls.length, 1);
});

test('retry delay is configurable and backs off between attempts', async () => {
  const fake = makeFetch([
    errorResponse(429, 'Too Many Requests'),
    errorResponse(429, 'Too Many Requests'),
    okResponse(buildPage())
  ]);
  getHtml.fetch = fake;

  const started = Date.now();
  const stats = await ow.getAllStats('Test-1234', 'pc', { retries: 2, retryDelay: 20 });
  const elapsed = Date.now() - started;

  assert.strictEqual(fake.calls.length, 3);
  assert.strictEqual(stats.level, '42');
  // Two waits: 20ms then 40ms (exponential backoff).
  assert.ok(elapsed >= 45, `expected backoff waits, elapsed ${elapsed}ms`);
});

test('getHtml alone reports retry exhaustion with the status code', async () => {
  const fake = makeFetch([
    errorResponse(500, 'Internal Server Error'),
    errorResponse(500, 'Internal Server Error')
  ]);
  getHtml.fetch = fake;

  await assert.rejects(getHtml('Test-1234', 'pc', { retries: 1, retryDelay: 1 }), err => {
    assert.strictEqual(err.status, 500);
    assert.strictEqual(err.statusCode, 500);
    return true;
  });
});

test('a parser that throws is contained to its own section', async () => {
  const fake = makeFetch([okResponse(buildPage())]);
  getHtml.fetch = fake;

  const original = ow.getHeroStats;
  ow.getHeroStats = async () => {
    throw new Error('boom while parsing');
  };

  try {
    const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

    assert.strictEqual(stats.heroStats, null);
    assert.strictEqual(stats.errors.heroStats, 'boom while parsing');
    assert.strictEqual(stats.level, '42');
    assert.ok(stats.mostPlayed.competitive.mei);
  } finally {
    ow.getHeroStats = original;
  }
});

test('a failed basicInfo section is null and reported while others survive', async () => {
  const fake = makeFetch([okResponse(buildPage())]);
  getHtml.fetch = fake;

  const original = ow.getBasicInfo;
  ow.getBasicInfo = async () => {
    throw new Error('basic info layout changed');
  };

  try {
    const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

    assert.strictEqual(stats.basicInfo, null);
    assert.strictEqual(stats.errors.basicInfo, 'basic info layout changed');
    assert.ok(stats.heroStats.competitive.reaper);
    assert.ok(stats.mostPlayed.quickplay.reaper);
  } finally {
    ow.getBasicInfo = original;
  }
});

test('getHtml failure still rejects the whole call even if parsers would cope', async () => {
  const fake = makeFetch([
    errorResponse(429, 'Too Many Requests'),
    errorResponse(429, 'Too Many Requests'),
    errorResponse(429, 'Too Many Requests')
  ]);
  getHtml.fetch = fake;

  const original = ow.getHeroStats;
  ow.getHeroStats = async () => {
    throw new Error('should never run');
  };

  try {
    await assert.rejects(
      ow.getAllStats('Test-1234', 'pc', FAST),
      err => {
        assert.strictEqual(err.status, 429);
        return true;
      }
    );
    assert.strictEqual(fake.calls.length, 3);
  } finally {
    ow.getHeroStats = original;
  }
});

test('a transient network error is retried within the same budget', async () => {
  let calls = 0;
  const fake = async () => {
    calls += 1;
    if (calls === 1) {
      throw new Error('ECONNRESET');
    }
    return okResponse(buildPage());
  };
  getHtml.fetch = fake;

  const stats = await ow.getAllStats('Test-1234', 'pc', FAST);

  assert.strictEqual(calls, 2);
  assert.strictEqual(stats.level, '42');
});

(async () => {
  let failures = 0;

  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (error) {
      failures += 1;
      console.error(`not ok - ${name}`);
      console.error(error && error.stack ? error.stack : error);
    } finally {
      getHtml.fetch = null;
    }
  }

  if (failures) {
    console.error(`\n${failures} test(s) failed`);
    process.exitCode = 1;
  } else {
    console.log(`\n${tests.length} tests passed`);
  }
})();
