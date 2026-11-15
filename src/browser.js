/*
 * Copyright (c) 2018, Hugo Freire <hugo@exec.sh>.
 *
 * This source code is licensed under the license found in the
 * LICENSE.md file in the root directory of this source tree.
 */

/* eslint-disable no-undef */

const _ = require('lodash')
const RandomHttpUserAgent = require('random-http-useragent')
const puppeteer = require('puppeteer-core')

const { PUPPETEER_EXECUTABLE_PATH } = process.env

const defaultOptions = {
  puppeteer: {
    executablePath: PUPPETEER_EXECUTABLE_PATH,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  },
  'random-http-useragent': {},
}

class Browser {
  constructor() {
    this._options = _.defaultsDeep({}, defaultOptions)

    RandomHttpUserAgent.configure(_.get(this._options, 'random-http-useragent'))
  }

  async open(url) {
    if (!url || typeof url !== 'string') {
      const error = new Error('invalid arguments: url is missing')
      error.stage = 'parameters'

      throw error
    }

    try {
      // eslint-disable-next-line no-new
      new URL(url)
    } catch (_error) {
      const error = new Error(
        `invalid arguments: url "${url}" is not a valid URL`
      )
      error.stage = 'parameters'

      throw error
    }

    const result = {}

    const userAgent = await RandomHttpUserAgent.get()

    const browser = await puppeteer.launch(_.get(this._options, 'puppeteer'))

    try {
      const page = await browser.newPage()
      await page.setUserAgent(userAgent)

      page.on('console', (consoleMessage) => {
        if (!result.console) {
          result.console = []
        }

        result.console.push({
          type: consoleMessage.type(),
          text: consoleMessage.text(),
        })
      })

      try {
        await page.goto(url)
      } catch (error) {
        error.stage = 'navigation'

        throw error
      }

      try {
        result.elements = await page.evaluate(() => {
          return (
            new XMLSerializer().serializeToString(document.doctype) +
            document.documentElement.outerHTML
          )
        })
      } catch (error) {
        error.stage = 'page-script'

        throw error
      }

      return result
    } finally {
      await browser.close()
    }
  }
}

module.exports = new Browser()
