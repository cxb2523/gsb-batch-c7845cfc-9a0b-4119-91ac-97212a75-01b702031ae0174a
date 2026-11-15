'use strict'

import { writeFile } from 'node:fs/promises'

// Verification outcomes. Every verified User-Agent ends up in exactly one
// of these four buckets, so failures stay auditable after the process ends.
export const OUTCOME = Object.freeze({
  PASSED: 'passed',
  HTTP_ERROR: 'http-error',
  REQUEST_ERROR: 'request-error',
  CONTENT_CHECK_FAILED: 'content-check-failed'
})

export const compareUserAgents = (a, b) => a.localeCompare(b)

export const formatError = error => {
  if (!(error instanceof Error)) return String(error)
  // Keep the error name (e.g. AbortError on timeouts) alongside the message,
  // otherwise operators cannot tell a timeout apart from a refused connection.
  return error.name && error.name !== 'Error'
    ? `${error.name}: ${error.message}`
    : error.message
}

const runWithConcurrency = async (items, concurrency, worker) => {
  const results = new Array(items.length)
  let cursor = 0
  const runners = Array.from(
    { length: Math.min(concurrency, items.length) },
    async () => {
      while (cursor < items.length) {
        const index = cursor++
        results[index] = await worker(items[index], index)
      }
    }
  )
  await Promise.all(runners)
  return results
}

// Verifies one User-Agent against every verification target.
//
// A target is `[url, verifyFn, fetcher]`. The fetcher keeps its existing
// semantics: a non-200 response resolves with its status code instead of
// throwing, while a network problem (including the AbortError raised after
// the fetch timeout) rejects. Targets are evaluated in order and the first
// decisive failure short-circuits the rest, matching `pEvery`.
//
// Returns `{ userAgent, outcome, statusCode, error, checks, passed }`.
// `checks` is per-target detail used for logging; only the four documented
// fields are persisted to verification-report.json.
export const verifyUserAgent = async (
  userAgent,
  verifications,
  { schedule = task => task(), load } = {}
) => {
  const checks = []

  for (const [url, verifyFn, fetcher] of verifications) {
    let statusCode = null
    let error = null

    try {
      const response = await schedule(() => fetcher(url, userAgent))
      statusCode = response.statusCode

      if (response.statusCode !== 200) {
        // Non-200 responses are not exceptions: the fetcher resolved.
        return {
          userAgent,
          outcome: OUTCOME.HTTP_ERROR,
          statusCode,
          error: null,
          checks: [...checks, { url, statusCode, error: null, passed: false }],
          passed: false
        }
      }

      const passed = await verifyFn(load(response.html))
      checks.push({ url, statusCode, error: null, passed })
      if (!passed) {
        return {
          userAgent,
          outcome: OUTCOME.CONTENT_CHECK_FAILED,
          statusCode,
          error: null,
          checks,
          passed: false
        }
      }
    } catch (err) {
      // Real network failures (including timeout AbortErrors) must surface;
      // there is no response, so there is no status code to record.
      error = formatError(err)
      checks.push({ url, statusCode, error, passed: false })
      return {
        userAgent,
        outcome: OUTCOME.REQUEST_ERROR,
        statusCode: null,
        error,
        checks,
        passed: false
      }
    }
  }

  return {
    userAgent,
    outcome: OUTCOME.PASSED,
    statusCode: checks.length ? checks[checks.length - 1].statusCode : null,
    error: null,
    checks,
    passed: true
  }
}

// Verifies every User-Agent independently. A failure of a single entry can
// never abort the remaining ones: anything unexpected is recorded as a
// request error instead of rejecting the whole run.
export const verifyUserAgents = async (
  userAgents,
  verifications,
  { concurrency = 1, schedule, load, onResult } = {}
) => {
  const results = await runWithConcurrency(
    userAgents,
    concurrency,
    async (userAgent, index) => {
      let result
      try {
        result = await verifyUserAgent(userAgent, verifications, {
          schedule,
          load
        })
      } catch (error) {
        result = {
          userAgent,
          outcome: OUTCOME.REQUEST_ERROR,
          statusCode: null,
          error: formatError(error),
          checks: [],
          passed: false
        }
      }
      if (onResult) await onResult(result, index)
      return result
    }
  )

  const report = results
    .map(({ checks, passed, ...entry }) => entry)
    .sort((a, b) => compareUserAgents(a.userAgent, b.userAgent))

  const passed = results
    .filter(result => result.passed)
    .map(result => result.userAgent)
    .sort(compareUserAgents)

  return { results, report, passed }
}

// Persists the verification outcomes.
//
// `verification-report.json` is always written (every entry has an outcome);
// `index.json` is only written when at least one User-Agent passed, keeping
// it a plain sorted array of UA strings with no outcome data mixed in.
// Returns the paths actually written.
export const writeVerificationOutputs = async (
  { report, passed },
  {
    reportPath = 'verification-report.json',
    indexPath = 'index.json'
  } = {}
) => {
  await writeFile(reportPath, JSON.stringify(report, null, 2))

  const written = [reportPath]
  if (passed.length > 0) {
    await writeFile(indexPath, JSON.stringify(passed, null, 2))
    written.push(indexPath)
  }

  return written
}
