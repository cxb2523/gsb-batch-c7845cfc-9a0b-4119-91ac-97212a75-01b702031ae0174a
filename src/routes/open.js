/*
 * Copyright (c) 2018, Hugo Freire <hugo@exec.sh>.
 *
 * This source code is licensed under the license found in the
 * LICENSE.md file in the root directory of this source tree.
 */

// eslint-disable-next-line import/no-extraneous-dependencies
const Joi = require('@hapi/joi')
// eslint-disable-next-line import/no-extraneous-dependencies
const Boom = require('@hapi/boom')
const { Route } = require('serverful')
const Browser = require('../browser')

const toHttpError = (error) => {
  const stage = error.stage || 'browser'
  const reason = error.message

  let httpError

  if (stage === 'parameters') {
    httpError = Boom.badRequest(reason)
  } else if (stage === 'navigation' && error.name === 'TimeoutError') {
    httpError = Boom.gatewayTimeout(reason)
  } else {
    httpError = Boom.badGateway(reason)
  }

  httpError.output.payload.stage = stage
  httpError.output.payload.reason = reason

  return httpError
}

class Open extends Route {
  constructor() {
    super(
      'GET',
      '/open',
      'Opens web page in headless browser',
      'Returns opened web page'
    )
  }

  async handler({ query }) {
    const { url } = query

    try {
      return await Browser.open(url)
    } catch (error) {
      throw toHttpError(error)
    }
  }

  validate() {
    return {
      query: Joi.object({
        key: Joi.string().optional().description('the access API key'),
        url: Joi.string().required().description('the web page URL to open'),
      }),
      headers: Joi.object({
        'x-api-key': Joi.string().optional().description('the access API key'),
      }).unknown(),
    }
  }
}

module.exports = new Open()
