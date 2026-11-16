'use strict';

const cheerio = require('cheerio');

// Conservative defaults: retry only a couple of times and wait a while between
// attempts so a rate limiting or struggling server is not put under extra
// pressure.
const DEFAULT_RETRIES = 2;
const DEFAULT_RETRY_DELAY = 1000;
const DEFAULT_MAX_RETRY_DELAY = 30000;

function sleep(ms) {
  return ms > 0 ? new Promise(resolve => setTimeout(resolve, ms)) : Promise.resolve();
}

function toNonNegativeNumber(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : fallback;
}

function pick(options, names) {
  for (const name of names) {
    if (options[name] !== undefined && options[name] !== null) {
      return options[name];
    }
  }
  return undefined;
}

/**
 * Normalizes the retry configuration. Accepts an options object
 * (`{ retries, retryDelay, maxRetryDelay, fetch }`) or, as a convenience, a
 * bare retry count (`getHtml(tag, platform, 3, 500)`). A function is treated as
 * a fetch implementation.
 */
function normalizeOptions(options, retryDelay) {
  if (typeof options === 'number' || typeof options === 'string') {
    options = { retries: options, retryDelay: retryDelay };
  } else if (typeof options === 'function') {
    options = { fetch: options };
  } else if (!options || typeof options !== 'object') {
    options = {};
  }

  const retries = Math.floor(toNonNegativeNumber(
    pick(options, ['retries', 'maxRetries', 'retryCount']),
    DEFAULT_RETRIES
  ));

  const delay = toNonNegativeNumber(
    pick(options, ['retryDelay', 'retryInterval', 'delay', 'interval']),
    DEFAULT_RETRY_DELAY
  );

  const maxDelay = Math.max(delay, toNonNegativeNumber(
    pick(options, ['maxRetryDelay', 'maxDelay']),
    DEFAULT_MAX_RETRY_DELAY
  ));

  const fetchImpl = pick(options, ['fetch', 'fetchImpl']) || getHtml.fetch;

  return {
    retries,
    retryDelay: delay,
    maxRetryDelay: maxDelay,
    fetch: typeof fetchImpl === 'function' ? fetchImpl : null
  };
}

/** Resolved lazily so tests (and embedding code) can swap the implementation. */
function resolveFetch(options) {
  if (options.fetch) {
    return options.fetch;
  }

  return require('node-fetch');
}

function getStatus(res) {
  return res && typeof res.status === 'number' ? res.status : undefined;
}

function isOk(res) {
  const status = getStatus(res);
  return Boolean(res) && (res.ok === true || (status >= 200 && status < 300));
}

function getRetryAfterDelay(res) {
  try {
    const headers = res && res.headers;
    if (!headers) {
      return 0;
    }

    const value = typeof headers.get === 'function'
      ? headers.get('retry-after')
      : headers['retry-after'];

    if (!value) {
      return 0;
    }

    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return seconds * 1000;
    }

    const timestamp = Date.parse(value);
    if (!Number.isNaN(timestamp)) {
      return Math.max(0, timestamp - Date.now());
    }
  } catch (error) { // eslint-disable-line no-unused-vars
    // Ignore malformed Retry-After headers.
  }

  return 0;
}

function getRetryDelay(attempt, res, options) {
  const exponential = options.retryDelay * Math.pow(2, attempt);
  const retryAfter = getRetryAfterDelay(res);

  // Back off exponentially, honour a longer Retry-After, and never exceed the
  // configured ceiling so a huge Retry-After cannot stall the caller.
  return Math.min(Math.max(exponential, retryAfter), options.maxRetryDelay);
}

function isTransientStatus(status) {
  return status === 429 || (status >= 500 && status <= 599);
}

/** Error for a 429/5xx response that outlived the configured retries. */
function createHttpError(status, statusText) {
  const error = new Error(`HTTP ${status}${statusText ? ` ${statusText}` : ''}`);

  error.status = status;
  error.statusCode = status;
  error.retryable = true;
  error.code = status === 429 ? 'RATE_LIMITED' : 'SERVER_ERROR';
  if (statusText) {
    error.statusText = statusText;
  }

  return error;
}

function parse(html) {
  // load into cheerio
  const $ = cheerio.load(html);

  // check if profile exists
  if (!$('.masthead-player').html()) {
    throw new Error('PROFILE_NOT_FOUND');
  }

  // check if profile private
  if ($('.masthead-permission-level-text').text() === 'Private Profile') {
    throw new Error('PROFILE_PRIVATE');
  }

  return html;
}

async function getHtml(battletag, platform, options, retryDelay) {

  const opts = normalizeOptions(options, retryDelay);
  const fetchImpl = resolveFetch(opts);

  const url = encodeURI(`https://playoverwatch.com/en-us/career/${platform}/${battletag}`);

  let attempt = 0;

  for (;;) {
    let res;

    try {
      res = await fetchImpl(url);
    } catch (error) {
      // Network jitter is the transient failure this whole path exists for:
      // retry within the same bounded budget, then surface the original error.
      if (attempt >= opts.retries) {
        throw error;
      }

      await sleep(getRetryDelay(attempt, null, opts));
      attempt += 1;
      continue;
    }

    if (isOk(res)) {
      return parse(await res.text());
    }

    const status = getStatus(res);

    if (isTransientStatus(status)) {
      if (attempt < opts.retries) {
        await sleep(getRetryDelay(attempt, res, opts));
        attempt += 1;
        continue;
      }

      // Rate limits and temporary server errors that outlived the retries are
      // reported with the upstream status code attached.
      throw createHttpError(status, res && res.statusText);
    }

    // Every other non-2xx response keeps the original semantics.
    throw new Error('PROFILE_NOT_FOUND');
  }

}

// Injection point: tests (or embedding applications) can substitute the fetch
// implementation either through `{ fetch }` or by assigning `getHtml.fetch`.
getHtml.fetch = null;

module.exports = getHtml;
