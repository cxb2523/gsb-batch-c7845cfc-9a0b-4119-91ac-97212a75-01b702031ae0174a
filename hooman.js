import got from 'got';
import solveChallenge from './lib/core.js';
import solveCaptcha from './lib/captcha.js';
import delay from './lib/delay.js';
import log from './lib/logging.js';
import { CookieJar } from 'tough-cookie';
import UserAgent from 'user-agents';

const cookieJar = new CookieJar(); // Automatically parse and store cookies
const challengeInProgress = {}; // Captcha solving in progress, keyed by host
// In-flight (or recently settled) js-challenge solve promises, keyed by host.
// Concurrent requests to the same host await the shared promise instead of
// each starting their own solve; different hosts never block each other.
const jsChallengeSolvers = new Map();

// Retry a request through the instance. got v16 rejects `url` inside options
// objects, so it is passed as the first argument instead
const retryRequest = (options, overrides = {}) => {
  const url = overrides.url ?? options.url;
  const rest = { ...options, ...overrides };
  delete rest.url;
  return instance(url, rest);
};

// Got instance to handle cloudflare bypass
// Custom hooman options (cloudflareRetry, notFoundRetry, captchaRetry, onCaptcha,
// captchaKey, rucaptcha) live inside got's `context` since got v12 rejects unknown
// top-level options
const instance = got.extend({
  cookieJar,
  retry: {
    limit: 2,
    statusCodes: [408, 413, 429, 500, 502, 504, 521, 522, 524],
  }, // Do not retry 503, we will handle it
  context: {
    cloudflareRetry: 5, // Prevent cloudflare loop
    notFoundRetry: 1, // Handle redirect issue
    captchaRetry: 1, // Max retry on captcha
    onCaptcha: null, // Custom function to handle captcha
    captchaKey: process.env.HOOMAN_CAPTCHA_KEY,
    rucaptcha: process.env.HOOMAN_RUCAPTCHA,
  },
  http2: false, // http2 doesn't work well with proxies
  headers: {
    'accept-encoding': 'gzip, deflate',
    'accept-language': 'en-US,en;q=0.5',
    'cache-control': 'max-age=0',
    'upgrade-insecure-requests': '1',
    'user-agent': new UserAgent().toString(), // Use random user agent between sessions
  }, // Mimic browser environment
  hooks: {
    beforeRequest: [
      async (options) => {
        // In case a js-challenge is being solved for this host, hold the
        // request and reuse that result instead of firing a duplicate solve
        const pendingSolve = jsChallengeSolvers.get(options.url.host);
        if (pendingSolve) {
          try {
            await pendingSolve;
          } catch {
            // Solving failed, the request continues and the afterResponse
            // hook hands back the original 503 response
          }
        }

        // In case the host is in challenge progress wait
        while (challengeInProgress[options.url.host]) {
          await delay(1000);
        }

        // Add required headers to mimic browser environment
        options.headers.host = options.url.host;
        options.headers.origin = options.url.origin;
        options.headers.referer = options.url.href;
      },
    ],
    afterResponse: [
      async (response) => {
        const context = response.request.options.context;
        if (
          // If site is not hosted on cloudflare skip
          response.statusCode === 503 &&
          response.headers.server === 'cloudflare' &&
          context.cloudflareRetry > 0 &&
          response.body.includes('jschl-answer') &&
          response.body.includes('var s')
        ) {
          // Solve js challange
          const host = response.request.options.url.host;
          const inFlight = jsChallengeSolvers.get(host);
          if (inFlight) {
            // Another request is already solving for this host, wait for that
            // shared solve and reuse its result instead of solving again
            log.info('Waiting for js-challenge to be solved: ' + host);
            try {
              await inFlight;
            } catch {
              // Solving failed, wake up and hand back the original 503
              log.info('js-challenge solving failed, returning original response: ' + host);
              return response;
            }
            log.info('JS-Challenge were solved and waiting is over, refreshing: ' + host);
            return retryRequest(response.request.options, {
              context: { ...context, cloudflareRetry: context.cloudflareRetry - 1 },
            });
          }

          log.info('Solving js-challenge: ' + response.url);
          const solver = solveChallenge(response.url, response.body);
          jsChallengeSolvers.set(host, solver);
          // Keep the settled promise around briefly so late arrivals still
          // reuse the result, then clear so future challenges can be solved
          const clearSolver = () => {
            setTimeout(() => {
              if (jsChallengeSolvers.get(host) === solver) {
                log.info('Clear js-challenge in progress: ' + host);
                jsChallengeSolvers.delete(host);
              }
            }, 2000);
          };
          solver.then(clearSolver, clearSolver);

          let data;
          try {
            data = await solver;
          } catch (error) {
            // Parsing/solving failed, return the original 503 response
            // instead of leaking an unhandled rejection
            log.info('Failed to solve js-challenge: ' + response.url);
            log.error(error && error.message ? error.message : String(error));
            return response;
          }
          if (!data) {
            log.info('Unable to parse js-challenge: ' + response.url);
            return response;
          }

          return retryRequest(response.request.options, {
            ...data,
            context: { ...context, cloudflareRetry: context.cloudflareRetry - 1 },
          });
        } else if (
          // Handle redirect issue for cloudflare
          response.statusCode === 404 &&
          response.headers.server === 'cloudflare' &&
          context.notFoundRetry > 0 &&
          response.url.includes('?__cf_chl_jschl_tk')
        ) {
          // Do not retry again
          return instance({
            url: response.url.split('?__cf_chl_jschl_tk')[0],
            context: { ...context, notFoundRetry: context.notFoundRetry - 1 },
          });
        } else if (
          response.statusCode === 403 &&
          response.headers.server === 'cloudflare' &&
          context.captchaKey &&
          context.captchaRetry > 0 &&
          response.body.includes('cf_captcha_kind')
        ) {
          // Solve g/hCaptcha
          // If there are captcha solving in progress for current domain do not request for solving
          const host = response.request.options.url.host;
          if (challengeInProgress[host] && !context.ignoreInProgress) {
            log.info('Waiting for captcha to be solved: ' + host);
            while (challengeInProgress[host]) {
              await delay(1000);
            }
            log.info('Captcha were solved and waiting is over, refreshing: ' + host);
            return instance({ ...response.request.options });
          }

          challengeInProgress[host] = true;
          const captchaData = await solveCaptcha(response).finally(() => {
            setTimeout(() => {
              if (challengeInProgress[host]) {
                log.info('Clear captcha in progress: ' + host);
                delete challengeInProgress[host];
              }
            }, 2000);
          });

          // Submit captcha data
          if (captchaData) {
            log.info('Submit captcha: ' + captchaData.url);
            return instance({
              ...response.request.options,
              ...captchaData,
              context: { ...context, captchaRetry: context.captchaRetry - 1, ignoreInProgress: true },
            });
          }
        }

        return response;
      },
    ],
  },
  mutableDefaults: true, // Defines if config can be changed later
});

export default instance;
