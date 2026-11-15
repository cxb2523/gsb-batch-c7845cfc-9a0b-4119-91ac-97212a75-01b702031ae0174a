/*
 * Copyright (c) 2018, Hugo Freire <hugo@exec.sh>.
 *
 * This source code is licensed under the license found in the
 * LICENSE.md file in the root directory of this source tree.
 */

describe('Browser', () => {
  let subject
  let puppeteer
  let RandomHttpUserAgent

  beforeEach(() => {
    puppeteer = require('puppeteer-core')
    jest.mock('puppeteer-core')

    RandomHttpUserAgent = require('random-http-useragent')
    jest.mock('random-http-useragent')
  })

  describe('when opening a url', () => {
    const url = 'https://my-url'
    const userAgent = 'my-user-agent'
    let browser
    let page

    beforeEach(() => {
      browser = { newPage: jest.fn(), close: jest.fn() }
      page = {
        setUserAgent: jest.fn(),
        on: jest.fn(),
        goto: jest.fn(),
        evaluate: jest.fn(),
      }

      browser.newPage.mockResolvedValue(page)
      puppeteer.launch.mockResolvedValue(browser)

      RandomHttpUserAgent.get.mockResolvedValue(userAgent)

      subject = require('../src/browser')
    })

    it('should use random http user-agent', async () => {
      await subject.open(url)

      expect(page.setUserAgent).toHaveBeenCalledWith(userAgent)
    })

    it('should listen to console events', async () => {
      await subject.open(url)

      expect(page.on).toHaveBeenCalledWith('console', expect.anything())
    })

    it('should go to url', async () => {
      await subject.open(url)

      expect(page.goto).toHaveBeenCalledWith(url)
    })

    it('should evaluate javascript code', async () => {
      await subject.open(url)

      expect(page.evaluate).toHaveBeenCalled()
    })

    it('should close browser', async () => {
      await subject.open(url)

      expect(browser.close).toHaveBeenCalled()
    })

    it('should return serialized page elements', async () => {
      const elements = '<html></html>'
      page.evaluate.mockResolvedValue(elements)

      const result = await subject.open(url)

      expect(result.elements).toEqual(elements)
    })

    it('should not return console messages when there are none', async () => {
      const result = await subject.open(url)

      expect(result.console).toBeUndefined()
    })

    it('should return console messages when the page emits them', async () => {
      const consoleMessage = {
        type: () => 'log',
        text: () => 'my-console-message',
      }

      let onConsole
      const consoleListenerRegistered = new Promise((resolve) => {
        onConsole = resolve
      })
      page.on.mockImplementation((event, listener) => {
        if (event === 'console') {
          onConsole(listener)
        }
      })

      const promise = subject.open(url)
      const listener = await consoleListenerRegistered
      listener(consoleMessage)

      const result = await promise

      expect(result.console).toEqual([
        { type: 'log', text: 'my-console-message' },
      ])
    })
  })

  describe('when opening a url fails while navigating', () => {
    const url = 'https://my-url'
    const error = new Error('Navigation timeout of 30000 ms exceeded')
    error.name = 'TimeoutError'
    let browser
    let page

    beforeEach(() => {
      browser = { newPage: jest.fn(), close: jest.fn() }
      page = {
        setUserAgent: jest.fn(),
        on: jest.fn(),
        goto: jest.fn(),
        evaluate: jest.fn(),
      }

      browser.newPage.mockResolvedValue(page)
      puppeteer.launch.mockResolvedValue(browser)

      page.goto.mockRejectedValue(error)

      RandomHttpUserAgent.get.mockResolvedValue('my-user-agent')

      subject = require('../src/browser')
    })

    it('should reject with an error tagged with the navigation stage', async () => {
      await expect(subject.open(url)).rejects.toMatchObject({
        stage: 'navigation',
        name: 'TimeoutError',
      })
    })

    it('should close browser', async () => {
      await expect(subject.open(url)).rejects.toThrow()

      expect(browser.close).toHaveBeenCalledTimes(1)
    })
  })

  describe('when opening a url fails while evaluating page scripts', () => {
    const url = 'https://my-url'
    const error = new Error('my-script-error')
    let browser
    let page

    beforeEach(() => {
      browser = { newPage: jest.fn(), close: jest.fn() }
      page = {
        setUserAgent: jest.fn(),
        on: jest.fn(),
        goto: jest.fn(),
        evaluate: jest.fn(),
      }

      browser.newPage.mockResolvedValue(page)
      puppeteer.launch.mockResolvedValue(browser)

      page.evaluate.mockRejectedValue(error)

      RandomHttpUserAgent.get.mockResolvedValue('my-user-agent')

      subject = require('../src/browser')
    })

    it('should reject with an error tagged with the page-script stage', async () => {
      await expect(subject.open(url)).rejects.toMatchObject({
        stage: 'page-script',
      })
    })

    it('should close browser', async () => {
      await expect(subject.open(url)).rejects.toThrow()

      expect(browser.close).toHaveBeenCalledTimes(1)
    })
  })

  describe('when opening without a url', () => {
    beforeEach(() => {
      subject = require('../src/browser')
    })

    it('should reject with an error tagged with the parameters stage', async () => {
      await expect(subject.open()).rejects.toMatchObject({
        stage: 'parameters',
      })
    })

    it('should not launch browser', async () => {
      await expect(subject.open()).rejects.toThrow()

      expect(puppeteer.launch).not.toHaveBeenCalled()
    })
  })

  describe('when opening an invalid url', () => {
    const url = 'not-a-valid-url'

    beforeEach(() => {
      subject = require('../src/browser')
    })

    it('should reject with an error tagged with the parameters stage', async () => {
      await expect(subject.open(url)).rejects.toMatchObject({
        stage: 'parameters',
      })
    })

    it('should not launch browser', async () => {
      await expect(subject.open(url)).rejects.toThrow()

      expect(puppeteer.launch).not.toHaveBeenCalled()
    })
  })
})
