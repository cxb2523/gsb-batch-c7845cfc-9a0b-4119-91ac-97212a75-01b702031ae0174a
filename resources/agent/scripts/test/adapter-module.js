'use strict';

/**
 * Adapter factory module used by the CLI tests via
 * `ONIFY_ADAPTERS_MODULE=<this file>`.
 *
 * It reads the per-file script from `ONIFY_TEST_SCRIPT` and returns the
 * in-memory doubles, so the real agent scripts can be executed end-to-end
 * with no AWS credentials and no network.
 */

const fs = require('fs');
const path = require('path');
const { createDoubles } = require('./doubles');

module.exports = function adapterFactory() {
    const scriptPath = process.env.ONIFY_TEST_SCRIPT;
    const script = scriptPath
        ? JSON.parse(fs.readFileSync(path.resolve(scriptPath), 'utf8'))
        : {};

    const doubles = createDoubles(script);

    // Optional call log so tests can assert what the batch actually did.
    const logPath = process.env.ONIFY_TEST_CALL_LOG;
    if (logPath) {
        const write = function () {
            fs.writeFileSync(path.resolve(logPath), JSON.stringify(doubles.calls, null, 2));
        };

        const origUpload = doubles.uploader.upload;
        doubles.uploader.upload = async function (input) {
            const result = await origUpload(input);
            write();
            return result;
        };

        const origSend = doubles.mailer.send;
        doubles.mailer.send = async function (payload, context) {
            const result = await origSend(payload, context);
            write();
            return result;
        };

        write();
    }

    return doubles;
};
