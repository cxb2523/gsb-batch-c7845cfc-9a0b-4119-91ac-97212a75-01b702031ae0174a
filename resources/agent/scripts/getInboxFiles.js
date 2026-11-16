'use strict';

/**
 * Agent task: list inbox files.
 *
 * Invocation contract is unchanged: node getInboxFiles.js <inboxPath>
 * Output is unchanged: JSON array of `{ name, filename }`.
 *
 * A missing/unreadable inbox is reported as a structured error instead of an
 * uncaught exception, so the flow can surface a batch-level failure.
 */

const { listInboxFiles } = require('./lib/inbox');

const inboxPath = process.argv[2];

try {
    const inboxFiles = listInboxFiles(inboxPath);
    console.log(JSON.stringify(inboxFiles));
} catch (err) {
    console.log(JSON.stringify({
        ok: false,
        stage: 'inbox',
        inboxPath: inboxPath,
        error: {
            stage: 'inbox',
            code: err.code,
            message: err.message,
            reason: 'inbox failed for path=' + inboxPath + ': ' + err.message
        }
    }));
    process.exitCode = 1;
}
