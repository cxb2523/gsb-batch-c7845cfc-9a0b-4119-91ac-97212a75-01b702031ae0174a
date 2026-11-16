'use strict';

const fs = require('fs');
const { PipelineError } = require('./errors');

/**
 * Normalise the inbox path so that joining behaves the same on Windows and
 * POSIX. The original script always appended "/" which works on Windows too,
 * but keeping the user supplied separator is friendlier.
 */
function normaliseInboxPath(inboxPath) {
    if (!inboxPath || typeof inboxPath !== 'string') {
        throw new PipelineError('inbox path is required', { stage: 'inbox' });
    }
    return inboxPath.endsWith('/') || inboxPath.endsWith('\\') ? inboxPath : inboxPath + '/';
}

/**
 * List the inbox entries exactly like the original script did (same
 * `{ name, filename }` objects, same order) so the agent side contract is
 * unchanged. A failure to read the directory is raised as a PipelineError
 * instead of an uncaught exception so the flow can report it.
 */
function listInboxFiles(inboxPath) {
    const basePath = normaliseInboxPath(inboxPath);

    let entries;
    try {
        entries = fs.readdirSync(basePath);
    } catch (err) {
        throw new PipelineError('Unable to read inbox directory ' + basePath + ': ' + err.message, {
            stage: 'inbox',
            code: err.code,
            cause: err
        });
    }

    return entries.map(function (file) {
        return {
            name: file,
            filename: basePath + file
        };
    });
}

module.exports = { listInboxFiles, normaliseInboxPath };
