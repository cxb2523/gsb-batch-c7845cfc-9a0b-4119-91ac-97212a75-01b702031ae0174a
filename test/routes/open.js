/*
 * Copyright (c) 2018, Hugo Freire <hugo@exec.sh>.
 *
 * This source code is licensed under the license found in the
 * LICENSE.md file in the root directory of this source tree.
 */

describe('Open', () => {
  let subject
  let Browser
  let Boom

  beforeEach(() => {
    const { Route } = require('serverful')
    jest.mock('serverful')
    Route.BASE_PATH = '/'

    Browser = require('../../src/browser')
    jest.mock('../../src/browser')

    // eslint-disable-next-line import/no-extraneous-dependencies
    Boom = require('@hapi/boom')
    jest.mock('@hapi/boom')
  })

  describe('when handling a request to open a url', () => {
    const url = 'https://my-url'
    const query = { url }
    const request = { query }
    const browserResult = { elements: '<html></html>' }
    let h

    beforeEach(() => {
      h = jest.fn()

      Browser.open.mockResolvedValue(browserResult)

      subject = require('../../src/routes/open')
    })

    it('should open the url in the browser', async () => {
      await subject.handler(request, h)

      expect(Browser.open).toHaveBeenCalledWith(url)
    })

    it('should return result', async () => {
      const result = await subject.handler(request, h)

      expect(result).toEqual(browserResult)
    })
  })

  describe('when handling a request with missing or invalid arguments', () => {
    const request = { query: {} }
    const error = new Error('invalid arguments: url is missing')
    error.stage = 'parameters'
    let h

    beforeEach(() => {
      h = jest.fn()

      Boom.badRequest.mockImplementation((message) => ({
        output: {
          payload: { statusCode: 400, error: 'Bad Request', message },
        },
      }))

      Browser.open.mockRejectedValue(error)

      subject = require('../../src/routes/open')
    })

    it('should return a bad request error with the failure stage and reason', async () => {
      await expect(subject.handler(request, h)).rejects.toMatchObject({
        output: {
          payload: expect.objectContaining({
            statusCode: 400,
            stage: 'parameters',
            reason: error.message,
          }),
        },
      })

      expect(Boom.badRequest).toHaveBeenCalledWith(error.message)
    })
  })

  describe('when handling a request that fails to navigate to the url', () => {
    const request = { query: { url: 'https://my-url' } }
    const error = new Error('Navigation timeout of 30000 ms exceeded')
    error.stage = 'navigation'
    error.name = 'TimeoutError'
    let h

    beforeEach(() => {
      h = jest.fn()

      Boom.gatewayTimeout.mockImplementation((message) => ({
        output: {
          payload: {
            statusCode: 504,
            error: 'Gateway Time-out',
            message,
          },
        },
      }))

      Browser.open.mockRejectedValue(error)

      subject = require('../../src/routes/open')
    })

    it('should return a gateway timeout error with the failure stage and reason', async () => {
      await expect(subject.handler(request, h)).rejects.toMatchObject({
        output: {
          payload: expect.objectContaining({
            statusCode: 504,
            stage: 'navigation',
            reason: error.message,
          }),
        },
      })

      expect(Boom.gatewayTimeout).toHaveBeenCalledWith(error.message)
    })
  })

  describe('when handling a request that fails inside the browser', () => {
    const request = { query: { url: 'https://my-url' } }
    const error = new Error('my-script-error')
    error.stage = 'page-script'
    let h

    beforeEach(() => {
      h = jest.fn()

      Boom.badGateway.mockImplementation((message) => ({
        output: {
          payload: {
            statusCode: 502,
            error: 'Bad Gateway',
            message,
          },
        },
      }))

      Browser.open.mockRejectedValue(error)

      subject = require('../../src/routes/open')
    })

    it('should return a bad gateway error with the failure stage and reason', async () => {
      await expect(subject.handler(request, h)).rejects.toMatchObject({
        output: {
          payload: expect.objectContaining({
            statusCode: 502,
            stage: 'page-script',
            reason: error.message,
          }),
        },
      })

      expect(Boom.badGateway).toHaveBeenCalledWith(error.message)
    })
  })

  describe('when handling a request that fails for an unknown reason', () => {
    const request = { query: { url: 'https://my-url' } }
    const error = new Error('my-unexpected-error')
    let h

    beforeEach(() => {
      h = jest.fn()

      Boom.badGateway.mockImplementation((message) => ({
        output: {
          payload: {
            statusCode: 502,
            error: 'Bad Gateway',
            message,
          },
        },
      }))

      Browser.open.mockRejectedValue(error)

      subject = require('../../src/routes/open')
    })

    it('should return a bad gateway error with a default failure stage', async () => {
      await expect(subject.handler(request, h)).rejects.toMatchObject({
        output: {
          payload: expect.objectContaining({
            statusCode: 502,
            stage: 'browser',
            reason: error.message,
          }),
        },
      })
    })
  })
})
