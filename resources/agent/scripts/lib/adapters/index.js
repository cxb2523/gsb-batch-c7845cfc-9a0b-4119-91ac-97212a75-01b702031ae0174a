'use strict';

/**
 * Adapter wiring.
 *
 * The batch core (lib/stages.js) only knows these ports:
 *   uploader.upload({fileName, fullFileName, bucket}) -> upload result
 *   textract.startAnalysis({bucket, key, roleArn, snsTopicArn}) -> {JobId}
 *   textract.getAnalysis({jobId}) -> Textract response ({Blocks})
 *   waiter.waitForAnalysis({jobId, sqsQueueUrl, timeoutMs, pollDelayMs}) -> notification
 *   mailer.send(payload, context) -> result
 *
 * Production code calls `createAdapters()` with no arguments. Tests either
 * pass fakes straight to lib/stages.js, or point `ONIFY_ADAPTERS_MODULE` at a
 * module exporting a factory so the real CLI entry points can be driven
 * without credentials or network.
 */

const path = require('path');
const { createAwsAdapters, createOnifyMailer } = require('./aws');

/**
 * Require an adapter factory from disk.
 *
 * This is the injection seam used by the local test harness: point
 * `ONIFY_ADAPTERS_MODULE` at a module exporting a factory (or an adapter
 * object) and the CLI entry points will use it instead of real AWS/Onify.
 * It is deliberately opt-in and does not change the default production path.
 */
function loadAdapterModule(modulePath, options) {
    const resolved = path.resolve(process.cwd(), modulePath);
    // eslint-disable-next-line import/no-dynamic-require
    const loaded = require(resolved);
    return typeof loaded === 'function' ? loaded(options) : loaded;
}

/**
 * Build the lazy Onify mailer.
 *
 * It is lazy so that an agent doing only uploads (or a batch where every file
 * fails before the mail stage) never needs an Onify URL. The mail options are
 * captured at construction time, so the BPMN-provided `onifyURL` is used
 * rather than whatever happens to be in the environment.
 */
function lazyMailer(mailOptions) {
    let delegate = null;
    return {
        async send(payload, context) {
            if (!delegate) delegate = createOnifyMailer(mailOptions);
            return delegate.send(payload, context);
        }
    };
}

/**
 * @param {object} [options]
 * @param {object} [options.aws]       passed to createAwsAdapters
 * @param {object} [options.mailer]    pre-built mailer adapter
 * @param {object} [options.mail]      {baseUrl, token, fetchImpl} for the Onify mailer
 * @param {object} [options.adapters]  complete adapter set (highest priority)
 * @param {string} [options.adapterModule]  module path overriding the defaults
 */
function createAdapters(options) {
    options = options || {};

    if (options.adapters) return options.adapters;

    const modulePath = options.adapterModule || process.env.ONIFY_ADAPTERS_MODULE;
    if (modulePath) {
        return loadAdapterModule(modulePath, options);
    }

    const aws = createAwsAdapters(options.aws);

    let mailer = options.mailer;
    if (!mailer) {
        mailer = lazyMailer(options.mail || {});
    }

    return {
        uploader: aws.uploader,
        textract: aws.textract,
        waiter: aws.waiter,
        mailer: mailer
    };
}

module.exports = { createAdapters, loadAdapterModule, lazyMailer };
