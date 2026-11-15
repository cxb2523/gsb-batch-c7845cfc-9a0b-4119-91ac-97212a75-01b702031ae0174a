'use strict'

import Bottleneck from 'bottleneck'
import { styleText } from 'util'
import { pathToFileURL } from 'node:url'
import { load } from 'cheerio'

import crawlerUserAgents from 'crawler-user-agents'

import { acceptedUserAgents } from './accepted-user-agents.mjs'
import {
  withFetch
  /* withPrerender */
} from './fetcher.mjs'
import {
  OUTCOME,
  compareUserAgents,
  verifyUserAgents,
  writeVerificationOutputs
} from './verify-user-agents.mjs'

const CHECK = { true: '✅', false: '❌' }
const MAX_CONCURRENCY = Number(process.env.MAX_CONCURRENCY) || 1
const MIN_WAIT_TIME = Number(process.env.MIN_WAIT_TIME) || 5000

const VERIFICATIONS = [
  [
    'https://x.com/Kikobeats/status/1687837848802578432',
    $ => !!$('meta[property="og:image"]').attr('content'),
    withFetch
  ],
  [
    'https://www.instagram.com/kikobeats',
    $ => !!$('meta[property="og:image"]').attr('content'),
    withFetch
  ]
]

const SOURCES = [
  async () => {
    const res = await fetch(
      'https://raw.githubusercontent.com/arcjet/well-known-bots/main/well-known-bots.json'
    )
    const json = await res.json()
    return acceptedUserAgents(json)
  },
  () => {
    return crawlerUserAgents.map(item => item.instances).flat()
  }
]

export const loadUserAgents = async (sources = SOURCES) =>
  [
    ...new Set((await Promise.all(sources.map(fn => fn()))).flat())
  ].sort(compareUserAgents)

export const generateIndex = async ({
  userAgents,
  verifications = VERIFICATIONS,
  concurrency = MAX_CONCURRENCY,
  minWaitTime = MIN_WAIT_TIME,
  reportPath = 'verification-report.json',
  indexPath = 'index.json',
  logger = console
} = {}) => {
  const total = userAgents.length

  if (total === 0) {
    logger.error('No user agents found to verify.')
    return { report: [], passed: [], exitCode: 1, written: [] }
  }

  const limiter = new Bottleneck({ minTime: minWaitTime })

  logger.log(
    `\nStarting verification of user agents MAX_CONCURRENCY=${concurrency} MIN_WAIT_TIME=${minWaitTime}\n`
  )

  const { report, passed } = await verifyUserAgents(
    userAgents,
    verifications,
    {
      concurrency,
      schedule: task => limiter.schedule(task),
      load,
      onResult: (result, index) => {
        if (index !== 0) logger.log()
        logger.log(`[${index + 1}/${total}] ${result.userAgent}\n`)
        for (const check of result.checks) {
          logger.log(
            ' ' +
              styleText(
                'gray',
                `${check.url} (${check.statusCode ?? check.error}) ${
                  CHECK[check.passed]
                }`
              )
          )
        }
      }
    }
  )

  if (passed.length === 0) {
    // Every entry failed: keep the report for diagnosis, but leave the
    // existing index.json untouched and signal failure to the job.
    await writeVerificationOutputs(
      { report, passed },
      { reportPath, indexPath }
    )
    logger.error(
      `\nAll ${total} user agents failed the verification; index.json was not updated. See ${reportPath}.`
    )
    return { report, passed, exitCode: 1, written: [reportPath] }
  }

  const written = await writeVerificationOutputs(
    { report, passed },
    { reportPath, indexPath }
  )

  logger.log(
    `\n${passed.length}/${total} user agents passed; wrote ${indexPath} and ${reportPath} ✨`
  )
  const failures = report
    .filter(entry => entry.outcome !== OUTCOME.PASSED)
    .map(
      entry =>
        `  ${entry.outcome}: ${entry.userAgent} ` +
        `(status=${entry.statusCode ?? 'null'}, error=${entry.error ?? 'null'})`
    )
  if (failures.length > 0) logger.log(failures.join('\n'))

  return { report, passed, exitCode: 0, written }
}

const isMain =
  process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href

if (isMain) {
  try {
    const userAgents = await loadUserAgents()
    const { exitCode } = await generateIndex({ userAgents })
    if (exitCode !== 0) process.exit(exitCode)
  } catch (error) {
    console.error('Error during verification:', error)
    process.exit(1)
  }
}
