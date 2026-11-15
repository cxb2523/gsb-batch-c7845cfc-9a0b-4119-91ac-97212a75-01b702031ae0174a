import assert from 'node:assert/strict'
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { test } from 'node:test'
import { load as cheerioLoad } from 'cheerio'

import {
  OUTCOME,
  compareUserAgents,
  formatError,
  verifyUserAgent,
  verifyUserAgents,
  writeVerificationOutputs
} from '../scripts/verify-user-agents.mjs'
import { generateIndex } from '../scripts/generate.mjs'

const OG_HTML =
  '<html><head><meta property="og:image" content="https://img.test/x.png"></head></html>'
const PLAIN_HTML = '<html><head><title>no metadata here</title></head>'
const TEST_URL = 'https://example.test/page'

const hasOgImage = $ => !!$('meta[property="og:image"]').attr('content')

const abortError = () => {
  const error = new Error('The operation was aborted')
  error.name = 'AbortError'
  return error
}

// A fetcher is `(url, userAgent) => Promise<{ html, statusCode }>`.
// It mirrors withFetch semantics: non-200 responses RESOLVE with the status
// code, while network problems (including timeout AbortErrors) REJECT.
const fetcher = routes => async (url, userAgent) => {
  const route = routes[userAgent]
  if (route instanceof Error) throw route
  return route
}

const silentLogger = () => ({ log () {}, error () {} })

const makeTempDir = () => mkdtempSync(join(tmpdir(), 'ua-verify-'))

test('formatError keeps the error name, e.g. AbortError on timeouts', () => {
  assert.equal(formatError(abortError()), 'AbortError: The operation was aborted')
  assert.equal(formatError(new TypeError('boom')), 'TypeError: boom')
  assert.equal(formatError(new Error('plain')), 'plain')
  assert.equal(formatError('weird'), 'weird')
})

test('a 200 response with og:image passes and records the status code', async () => {
  const result = await verifyUserAgent(
    'Slackbot',
    [[TEST_URL, hasOgImage, fetcher({ Slackbot: { html: OG_HTML, statusCode: 200 } })]],
    { load: cheerioLoad }
  )

  assert.equal(result.outcome, OUTCOME.PASSED)
  assert.equal(result.statusCode, 200)
  assert.equal(result.error, null)
  assert.equal(result.passed, true)
})

test('a non-200 response is an http-error, not an exception', async () => {
  const result = await verifyUserAgent(
    'BlockedBot',
    [[TEST_URL, hasOgImage, fetcher({ BlockedBot: { html: 'forbidden', statusCode: 403 } })]],
    { load: cheerioLoad }
  )

  assert.equal(result.outcome, OUTCOME.HTTP_ERROR)
  assert.equal(result.statusCode, 403)
  assert.equal(result.error, null)
  assert.equal(result.passed, false)
})

test('a 200 response without og:image fails the content check', async () => {
  const result = await verifyUserAgent(
    'EmptyBot',
    [[TEST_URL, hasOgImage, fetcher({ EmptyBot: { html: PLAIN_HTML, statusCode: 200 } })]],
    { load: cheerioLoad }
  )

  assert.equal(result.outcome, OUTCOME.CONTENT_CHECK_FAILED)
  assert.equal(result.statusCode, 200)
  assert.equal(result.error, null)
})

test('a thrown request (incl. timeout AbortError) is a request-error with null status', async () => {
  const result = await verifyUserAgent(
    'TimeoutBot',
    [[TEST_URL, hasOgImage, fetcher({ TimeoutBot: abortError() })]],
    { load: cheerioLoad }
  )

  assert.equal(result.outcome, OUTCOME.REQUEST_ERROR)
  assert.equal(result.statusCode, null)
  assert.equal(result.error, 'AbortError: The operation was aborted')
})

test('the first decisive failure short-circuits the remaining targets', async () => {
  let secondCalled = false
  const second = async () => {
    secondCalled = true
    return { html: OG_HTML, statusCode: 200 }
  }

  const httpResult = await verifyUserAgent(
    'BlockedBot',
    [
      [TEST_URL, hasOgImage, fetcher({ BlockedBot: { html: '', statusCode: 500 } })],
      [TEST_URL, hasOgImage, second]
    ],
    { load: cheerioLoad }
  )
  assert.equal(httpResult.outcome, OUTCOME.HTTP_ERROR)
  assert.equal(secondCalled, false)

  const errorResult = await verifyUserAgent(
    'TimeoutBot',
    [
      [TEST_URL, hasOgImage, fetcher({ TimeoutBot: abortError() })],
      [TEST_URL, hasOgImage, second]
    ],
    { load: cheerioLoad }
  )
  assert.equal(errorResult.outcome, OUTCOME.REQUEST_ERROR)
  assert.equal(secondCalled, false)
})

test('mixed pass/fail run reports every UA, sorted, with exactly the four fields', async () => {
  const userAgents = ['D-content', 'B-http', 'A-pass', 'C-throw']
  const seen = []

  const { report, passed, results } = await verifyUserAgents(
    userAgents,
    [
      [
        TEST_URL,
        hasOgImage,
        fetcher({
          'A-pass': { html: OG_HTML, statusCode: 200 },
          'B-http': { html: 'blocked', statusCode: 403 },
          'C-throw': abortError(),
          'D-content': { html: PLAIN_HTML, statusCode: 200 }
        })
      ]
    ],
    {
      concurrency: 2,
      load: cheerioLoad,
      onResult: result => seen.push(result.userAgent)
    }
  )

  // Every UA got an outcome; one throwing request did not stop the others.
  assert.deepEqual(seen.sort(), [...userAgents].sort())
  assert.equal(results.length, 4)

  assert.deepEqual(
    report.map(entry => entry.userAgent),
    ['A-pass', 'B-http', 'C-throw', 'D-content']
  )

  assert.deepEqual(report, [
    { userAgent: 'A-pass', outcome: OUTCOME.PASSED, statusCode: 200, error: null },
    { userAgent: 'B-http', outcome: OUTCOME.HTTP_ERROR, statusCode: 403, error: null },
    {
      userAgent: 'C-throw',
      outcome: OUTCOME.REQUEST_ERROR,
      statusCode: null,
      error: 'AbortError: The operation was aborted'
    },
    {
      userAgent: 'D-content',
      outcome: OUTCOME.CONTENT_CHECK_FAILED,
      statusCode: 200,
      error: null
    }
  ])

  // index.json input stays a plain sorted UA string array, no outcome data.
  assert.deepEqual(passed, ['A-pass'])
})

test('a request throwing for one UA never interrupts verification of the others', async () => {
  const userAgents = ['ok-1', 'boom', 'ok-2']
  const { report, passed } = await verifyUserAgents(
    userAgents,
    [
      [
        TEST_URL,
        hasOgImage,
        fetcher({
          'ok-1': { html: OG_HTML, statusCode: 200 },
          boom: abortError(),
          'ok-2': { html: OG_HTML, statusCode: 200 }
        })
      ]
    ],
    { load: cheerioLoad }
  )

  assert.equal(report.length, 3)
  assert.deepEqual(passed, ['ok-1', 'ok-2'].sort(compareUserAgents))
  assert.equal(
    report.find(entry => entry.userAgent === 'boom').outcome,
    OUTCOME.REQUEST_ERROR
  )
})

test('an unexpected failure around a UA is recorded, not thrown', async () => {
  const { report } = await verifyUserAgents(
    ['surprise'],
    [[TEST_URL, hasOgImage, fetcher({ surprise: { html: OG_HTML, statusCode: 200 } })]],
    {
      load: cheerioLoad,
      // A broken scheduler simulates an error escaping the fetch pipeline.
      schedule: () => {
        throw new Error('limiter exploded')
      }
    }
  )

  assert.equal(report[0].outcome, OUTCOME.REQUEST_ERROR)
  assert.equal(report[0].error, 'limiter exploded')
  assert.equal(report[0].statusCode, null)
})

test('writeVerificationOutputs writes report and index when UAs pass', async () => {
  const dir = makeTempDir()
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')

  try {
    const written = await writeVerificationOutputs(
      {
        report: [
          { userAgent: 'A', outcome: OUTCOME.PASSED, statusCode: 200, error: null },
          {
            userAgent: 'B',
            outcome: OUTCOME.REQUEST_ERROR,
            statusCode: null,
            error: 'AbortError: timeout'
          }
        ],
        passed: ['A']
      },
      { reportPath, indexPath }
    )

    assert.deepEqual(written, [reportPath, indexPath])
    assert.deepEqual(JSON.parse(readFileSync(indexPath, 'utf8')), ['A'])
    assert.deepEqual(JSON.parse(readFileSync(reportPath, 'utf8')), [
      { userAgent: 'A', outcome: OUTCOME.PASSED, statusCode: 200, error: null },
      {
        userAgent: 'B',
        outcome: OUTCOME.REQUEST_ERROR,
        statusCode: null,
        error: 'AbortError: timeout'
      }
    ])
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('writeVerificationOutputs never overwrites index.json when nothing passes', async () => {
  const dir = makeTempDir()
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')
  const previousIndex = '["previous","untouched"]'
  writeFileSync(indexPath, previousIndex)

  try {
    const written = await writeVerificationOutputs(
      {
        report: [
          { userAgent: 'B', outcome: OUTCOME.HTTP_ERROR, statusCode: 503, error: null }
        ],
        passed: []
      },
      { reportPath, indexPath }
    )

    assert.deepEqual(written, [reportPath])
    assert.equal(readFileSync(indexPath, 'utf8'), previousIndex)
    assert.equal(existsSync(reportPath), true)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('generateIndex produces report and index together on mixed results', async () => {
  const dir = makeTempDir()
  const cleanup = () => rmSync(dir, { recursive: true, force: true })
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')

  const verifications = [
    [
      TEST_URL,
      hasOgImage,
      fetcher({
        'A-pass': { html: OG_HTML, statusCode: 200 },
        'B-http': { html: 'blocked', statusCode: 429 },
        'C-throw': abortError(),
        'D-content': { html: PLAIN_HTML, statusCode: 200 }
      })
    ]
  ]

  const { exitCode, written } = await generateIndex({
    userAgents: ['D-content', 'B-http', 'A-pass', 'C-throw'],
    verifications,
    concurrency: 2,
    minWaitTime: 0,
    reportPath,
    indexPath,
    logger: silentLogger()
  })

  assert.equal(exitCode, 0)
  assert.deepEqual(written, [reportPath, indexPath])

  const report = JSON.parse(readFileSync(reportPath, 'utf8'))
  const index = JSON.parse(readFileSync(indexPath, 'utf8'))

  assert.deepEqual(
    report.map(entry => entry.userAgent),
    ['A-pass', 'B-http', 'C-throw', 'D-content']
  )
  assert.deepEqual(
    report.map(entry => entry.outcome),
    [
      OUTCOME.PASSED,
      OUTCOME.HTTP_ERROR,
      OUTCOME.REQUEST_ERROR,
      OUTCOME.CONTENT_CHECK_FAILED
    ]
  )
  assert.deepEqual(index, ['A-pass'])
  assert.ok(index.every(entry => typeof entry === 'string'))

  cleanup()
})

test('generateIndex keeps the old index.json and fails when every UA fails', async () => {
  const dir = makeTempDir()
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')
  const previousIndex = '["legacy-agent"]'
  writeFileSync(indexPath, previousIndex)

  const verifications = [
    [
      TEST_URL,
      hasOgImage,
      fetcher({
        'boom-network': abortError(),
        'bad-status': { html: '', statusCode: 502 }
      })
    ]
  ]

  const { exitCode, written } = await generateIndex({
    userAgents: ['boom-network', 'bad-status'],
    verifications,
    concurrency: 1,
    minWaitTime: 0,
    reportPath,
    indexPath,
    logger: silentLogger()
  })

  assert.equal(exitCode, 1)
  assert.deepEqual(written, [reportPath])
  // The previous index.json is byte-for-byte untouched.
  assert.equal(readFileSync(indexPath, 'utf8'), previousIndex)

  const report = JSON.parse(readFileSync(reportPath, 'utf8'))
  assert.equal(report.length, 2)
  assert.ok(report.every(entry => entry.outcome !== OUTCOME.PASSED))

  rmSync(dir, { recursive: true, force: true })
})

test('generateIndex fails without writing anything when the source list is empty', async () => {
  const dir = makeTempDir()
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')

  const { exitCode, written } = await generateIndex({
    userAgents: [],
    reportPath,
    indexPath,
    logger: silentLogger()
  })

  assert.equal(exitCode, 1)
  assert.deepEqual(written, [])
  assert.equal(existsSync(reportPath), false)
  assert.equal(existsSync(indexPath), false)

  rmSync(dir, { recursive: true, force: true })
})

test('the failing exit code propagates at process level (job fails, no commit)', () => {
  const dir = makeTempDir()
  const reportPath = join(dir, 'verification-report.json')
  const indexPath = join(dir, 'index.json')
  writeFileSync(indexPath, '["legacy-agent"]')
  const runner = join(dir, 'run.mjs')
  const moduleUrl = new URL('../scripts/generate.mjs', import.meta.url).href

  writeFileSync(
    runner,
    `
import { generateIndex } from ${JSON.stringify(moduleUrl)}
const abortError = () => {
  const error = new Error('The operation was aborted')
  error.name = 'AbortError'
  return error
}
const { exitCode } = await generateIndex({
  userAgents: ['only-agent'],
  verifications: [
    [
      'https://example.test',
      $ => !!$('meta[property="og:image"]').attr('content'),
      async () => { throw abortError() }
    ]
  ],
  minWaitTime: 0,
  reportPath: ${JSON.stringify(reportPath)},
  indexPath: ${JSON.stringify(indexPath)},
  logger: { log () {}, error () {} }
})
process.exit(exitCode)
`
  )

  const result = spawnSync(process.execPath, [runner], { encoding: 'utf8' })
  assert.equal(result.status, 1)
  assert.equal(readFileSync(indexPath, 'utf8'), '["legacy-agent"]')
  assert.equal(existsSync(reportPath), true)

  rmSync(dir, { recursive: true, force: true })
})
