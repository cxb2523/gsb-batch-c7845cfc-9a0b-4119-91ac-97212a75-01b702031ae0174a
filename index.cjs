/*
 * @title user-agents-generator
 * @desc Generate realistic user agent strings for browsers and mobile devices.
 * @version 2.1.0
 * @license MIT
 */

'use strict';

const { FALLBACKS } = require('./lib/fallbacks.cjs');
const { pick, escapeRegExp, sanitizeUserAgent } = require('./lib/helpers.cjs');

/**
 * The `user-agents` dataset is loaded once at require time. If it cannot be
 * resolved (pruned install, offline cache), every method still returns a
 * realistic string from the embedded fallbacks instead of throwing.
 */
let UserAgent = null;
try {
  const mod = require('user-agents');
  if (typeof mod === 'function') {
    UserAgent = mod;
  } else if (mod && typeof mod.default === 'function') {
    UserAgent = mod.default;
  }
} catch {
  UserAgent = null;
}

/** Vendor tokens that ride on a Chromium UA and must not pass as plain Chrome. */
const CHROMIUM_PARTNERS =
  /(Edg|EdgA|EdgiOS|OPR|Opera|SamsungBrowser|CriOS|FxiOS|Vivaldi|YaBrowser|Brave|Silk|UCBrowser)/i;

const isDesktop = (entry) => entry.deviceCategory === 'desktop';
const ua = (entry) => String(entry.userAgent || '');

/**
 * One filter per family. These are functions because the dataset accepts them
 * as predicates over the raw record, which lets us express exclusions that a
 * plain substring filter cannot.
 */
const FAMILIES = {
  chrome: (entry) => isDesktop(entry) && /Chrome\//.test(ua(entry)) && !CHROMIUM_PARTNERS.test(ua(entry)),
  firefox: (entry) => isDesktop(entry) && /Firefox\//.test(ua(entry)),
  // Safari, correctly: WebKit only, rendered by Apple, and never a Chromium
  // build wearing a Safari/ token. v1 could return Chrome and Edge here.
  safari: (entry) =>
    isDesktop(entry) &&
    /Safari\//.test(ua(entry)) &&
    /Version\//.test(ua(entry)) &&
    /AppleWebKit\//.test(ua(entry)) &&
    !/Chrome\//.test(ua(entry)) &&
    !CHROMIUM_PARTNERS.test(ua(entry)),
  edge: (entry) => isDesktop(entry) && /Edg\//.test(ua(entry)),
  opera: (entry) => isDesktop(entry) && /OPR\//.test(ua(entry)),
  android: (entry) => /Android/.test(ua(entry)) && /Mobile/.test(ua(entry)),
  ios: (entry) => /(iPhone|iPad|iPod)/.test(ua(entry)),
};

const FAMILY_NAMES = Object.keys(FAMILIES);

/** Operating system predicates, applied to the UA text. */
const OS_PATTERNS = {
  windows: /Windows NT/i,
  macos: /Macintosh|Mac OS X/i,
  linux: /Linux/i,
  android: /Android/i,
  ios: /iPhone|iPad|iPod/i,
};

/**
 * Draw one record from the dataset, weighted as the dataset intends.
 * @param {string} family
 * @returns {string|null} null when the dataset is absent or has no match
 */
function fromDataset(family) {
  if (!UserAgent) return null;
  const filter = FAMILIES[family];
  if (!filter) return null;
  return drawDataset(filter);
}

/**
 * Run an arbitrary predicate against the dataset.
 * @param {(entry: object) => boolean} filter
 * @returns {string|null}
 */
function drawDataset(filter) {
  if (!UserAgent) return null;
  try {
    const value = String(new UserAgent(filter));
    return value.length > 0 ? value : null;
  } catch {
    return null;
  }
}

/**
 * Resolve a UA for a family: dataset first, embedded template second.
 * @param {string} family
 * @returns {string}
 */
function resolve(family) {
  const fromData = fromDataset(family);
  if (fromData) return fromData;
  return pick(FALLBACKS[family] || FALLBACKS.chrome);
}

/** @returns {string} Chrome on desktop */
function chrome() {
  return resolve('chrome');
}

/** @returns {string} Firefox on desktop */
function firefox() {
  return resolve('firefox');
}

/** @returns {string} Safari on desktop Apple platforms only */
function safari() {
  return resolve('safari');
}

/** @returns {string} Chrome on Android */
function android() {
  return resolve('android');
}

/** @returns {string} Safari on iOS */
function ios() {
  return resolve('ios');
}

/** @returns {string} Microsoft Edge on desktop */
function edge() {
  return resolve('edge');
}

/** @returns {string} Opera on desktop */
function opera() {
  return resolve('opera');
}

/**
 * Throw on anything that is not a plain options object. A string passed here
 * is a common mistake (v1 callers used no arguments at all), so the message
 * names the expected shape.
 * @param {unknown} options
 */
function assertOptions(options) {
  if (options === undefined || options === null) return;
  if (typeof options !== 'object' || Array.isArray(options)) {
    throw new TypeError('generate(options) expects an object, e.g. generate({ browser: "chrome" })');
  }
}

/**
 * Turn a caller-supplied string into a case-insensitive, literal matcher.
 * Escaping is what makes generate({ browser: 'chrome(' }) safe: without it
 * the string becomes an invalid RegExp and throws.
 * @param {string} value
 * @returns {RegExp}
 */
function literal(value) {
  return new RegExp(escapeRegExp(value), 'i');
}

/**
 * Generate a UA string, optionally constrained by browser, os and device.
 *
 * Unknown or unsatisfiable constraints degrade to an unconstrained weighted
 * draw instead of throwing: a generator that raises on an empty result set is
 * worse than one that returns something plausible, and callers overwhelmingly
 * wrap it in loops.
 *
 * @param {{ browser?: string, os?: string, device?: string }} [options]
 * @returns {string}
 */
function generate(options) {
  assertOptions(options);
  const opts = options || {};

  const browser = typeof opts.browser === 'string' && opts.browser.trim() ? opts.browser.trim() : null;
  const os = typeof opts.os === 'string' && opts.os.trim() ? opts.os.trim().toLowerCase() : null;
  const device = typeof opts.device === 'string' && opts.device.trim() ? opts.device.trim().toLowerCase() : null;

  const family = browser ? browser.toLowerCase() : null;
  const familyFilter = family && FAMILIES[family] ? FAMILIES[family] : null;
  const browserPattern = familyFilter ? null : browser ? literal(browser) : null;
  const osPattern = os ? (OS_PATTERNS[os] || literal(os)) : null;

  const predicate = (entry) => {
    const text = ua(entry);
    if (familyFilter && !familyFilter(entry)) return false;
    if (browserPattern && !browserPattern.test(text)) return false;
    if (osPattern && !osPattern.test(text)) return false;
    if (device && String(entry.deviceCategory || '').toLowerCase() !== device) return false;
    return true;
  };

  if (!familyFilter && !browserPattern && !osPattern && !device) {
    return random();
  }

  const fromData = drawDataset(predicate);
  if (fromData) return fromData;

  // Dataset absent or unsatisfiable: filter the embedded templates the same way.
  const pool = (family && FALLBACKS[family] ? FALLBACKS[family] : Object.values(FALLBACKS).flat()).filter((value) => {
    if (browserPattern && !browserPattern.test(value)) return false;
    if (osPattern && !osPattern.test(value)) return false;
    // Templates carry no deviceCategory, so a desktop-only request still has
    // to exclude the mobile ones by text.
    if (device === 'desktop' && /Android|iPhone|iPad|Mobile/i.test(value)) return false;
    if (device === 'mobile' && !/Android|iPhone|iPad|Mobile/i.test(value)) return false;
    return true;
  });
  if (pool.length > 0) return pick(pool);

  return random();
}

/**
 * Random UA drawn from any family, weighted by the dataset.
 * @returns {string}
 */
function random() {
  const fromData = drawDataset(() => true);
  if (fromData) return fromData;
  return pick(FALLBACKS[pick(FAMILY_NAMES)]);
}

/**
 * Ready-to-use request headers carrying a UA.
 *
 * The UA is validated rather than trusted: CR and LF would let a caller
 * inject additional headers, so they are rejected outright.
 *
 * @param {string} [value] defaults to a random UA
 * @returns {Record<string, string>}
 */
function headers(value) {
  const userAgent = value === undefined ? random() : sanitizeUserAgent(value);
  return {
    'User-Agent': userAgent,
    Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate, br',
  };
}

/**
 * Upper bound on the number of draws used while filling one session pool.
 * `generate()` degrades to an unconstrained draw on its own, so this only
 * bounds how long we keep deduplicating; it can never make the pool empty.
 */
const SESSION_DRAW_BUDGET = 2000;

/**
 * Build the round pool for a session: distinct UAs drawn under `constraints`
 * first, then (per the no-throw degradation contract) unconstrained draws for
 * whatever the constrained set could not supply.
 *
 * Reusing `generate()` keeps the constraint semantics identical to the rest
 * of the API: family filters (e.g. Safari never carrying a Chromium token),
 * OS/device matching, literal regex escaping and the empty-result degradation
 * all live in one place.
 *
 * @param {number} count requested pool size
 * @param {object} constraints browser/os/device options, already validated
 * @returns {string[]} one or more distinct UA strings
 */
function buildSessionPool(count, constraints) {
  const hasConstraints =
    typeof constraints.browser === 'string' ||
    typeof constraints.os === 'string' ||
    typeof constraints.device === 'string';
  const pool = [];
  const seen = new Set();

  const collect = (options) => {
    let draws = 0;
    while (pool.length < count && draws < SESSION_DRAW_BUDGET) {
      const candidate = generate(options);
      draws += 1;
      if (!seen.has(candidate)) {
        seen.add(candidate);
        pool.push(candidate);
      }
    }
  };

  if (hasConstraints) collect(constraints);
  // Unsatisfiable or partially satisfiable constraints degrade silently:
  // the remainder of the pool comes from unconstrained draws, and the
  // session stays usable instead of throwing.
  if (pool.length < count) collect({});
  return pool;
}

/**
 * Create a scraping session that rotates a fixed pool of distinct UAs round
 * by round. Within a round every `next()` call hands out a UA that has not
 * been handed out in that round yet; once the pool is exhausted the cursor
 * wraps to the start and a new round begins.
 *
 * Dispensing is synchronous state mutation on a single event loop, so
 * concurrent callers are served atomically: no UA can be handed out twice in
 * the same round, and no round can be skipped while UAs remain in it.
 *
 * @param {number} count desired number of distinct UAs per round (>= 1)
 * @param {{ browser?: string, os?: string, device?: string }} [options]
 * @returns {UserAgentSession}
 */
function createSession(count, options) {
  if (typeof count !== 'number' || !Number.isSafeInteger(count) || count < 1) {
    throw new TypeError('createSession(count, options) expects count to be a safe integer >= 1');
  }
  assertOptions(options);
  const opts = options || {};

  const pool = buildSessionPool(count, opts);
  let cursor = 0;
  let round = 1;
  let currentValue = pool[0];

  return {
    /**
     * Hand out the next UA, advancing the round cursor. Wraps to a new round
     * once every pool UA has been handed out once.
     * @returns {string}
     */
    next() {
      const value = pool[cursor];
      currentValue = value;
      cursor += 1;
      if (cursor === pool.length) {
        cursor = 0;
        round += 1;
      }
      return value;
    },

    /**
     * The UA most recently handed out by `next()` (or the first pool UA
     * before `next()` has run). Does not advance the cursor.
     * @returns {string}
     */
    current() {
      return currentValue;
    },

    /**
     * Turn a UA into a ready-to-use request header map, reusing the shared
     * header validation. Defaults to the session's current UA; an explicit
     * value is validated but does not change the rotation state.
     * @param {string} [userAgent]
     * @returns {Record<string, string>}
     */
    headers(userAgent) {
      return headers(userAgent === undefined ? currentValue : userAgent);
    },

    /** Number of distinct UAs in one round. */
    get size() {
      return pool.length;
    },

    /** 1-based number of the round currently being dispensed. */
    get round() {
      return round;
    },

    /** 0-based cursor position of the next `next()` call inside a round. */
    get index() {
      return cursor;
    },
  };
}

module.exports = {
  chrome,
  firefox,
  safari,
  android,
  ios,
  edge,
  opera,
  random,
  generate,
  headers,
  createSession,
};
