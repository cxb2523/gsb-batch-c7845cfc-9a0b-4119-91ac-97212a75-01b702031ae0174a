'use strict';

/**
 * In-memory test doubles for every injected port.
 *
 * They implement the exact adapter interfaces used by lib/stages.js:
 *   uploader.upload / textract.startAnalysis / textract.getAnalysis
 *   waiter.waitForAnalysis / mailer.send
 *
 * No credentials, no network, no AWS SDK. Behaviour can be scripted per file
 * so a single batch can mix success, upload failure, recognition failure and
 * mail failure.
 */

const { TextractJobFailedError, JobWaitTimeoutError } = require('../lib/errors');

/**
 * @param {object} [script]
 * @param {Record<string, object>} [script.files] keyed by file name:
 *   { uploadError, jobError, waitError, getError, blocks, jobId }
 * @param {object[]} [script.mailFailures] file names whose mail send fails
 * @param {string[]} [script.mailDown]     file names whose mail send fails
 */
function createDoubles(script) {
    script = script || {};
    const files = script.files || {};
    const calls = { upload: [], start: [], wait: [], get: [], mail: [], mailSent: [] };
    const store = {};

    function entryFor(key) {
        return files[key] || {};
    }

    const uploader = {
        async upload(input) {
            calls.upload.push(input);
            const entry = entryFor(input.fileName);
            if (entry.uploadError) throw entry.uploadError;

            const result = {
                Bucket: input.bucket,
                Key: input.fileName,
                ETag: '"etag-' + input.fileName + '"',
                Location: 'https://' + input.bucket + '.s3.amazonaws.com/' + input.fileName,
                SignedUrl: 'https://signed.example/' + input.bucket + '/' + input.fileName +
                    '?X-Amz-Expires=3600'
            };
            store[input.fileName] = result;
            return result;
        }
    };

    const textract = {
        async startAnalysis(input) {
            calls.start.push(input);
            const entry = entryFor(input.key);
            if (entry.jobError) throw entry.jobError;
            return { JobId: entry.jobId || 'job-' + input.key };
        },

        async getAnalysis(input) {
            calls.get.push(input);
            const entry = entryFor(entryFileName(input.jobId));
            if (entry.getError) throw entry.getError;
            if (entry.blocks !== undefined) return { Blocks: entry.blocks };
            return { Blocks: defaultBlocks() };
        }
    };

    const waiter = {
        async waitForAnalysis(input) {
            calls.wait.push(input);
            const entry = entryFor(entryFileName(input.jobId));
            if (entry.waitError) throw entry.waitError;
            if (entry.jobFailed) {
                const statusMessage = entry.statusMessage || 'FAILED';
                throw new TextractJobFailedError(
                    'Test double: job failed for jobId=' + input.jobId + ': ' + statusMessage,
                    { jobId: input.jobId, status: 'FAILED', statusMessage: statusMessage }
                );
            }
            if (entry.waitTimeout) {
                throw new JobWaitTimeoutError('Test double: timed out for jobId=' + input.jobId, {
                    jobId: input.jobId
                });
            }
            return { JobId: input.jobId, Status: 'SUCCEEDED' };
        }
    };

    const mailDown = new Set(script.mailDown || []);
    const mailer = {
        async send(payload, context) {
            calls.mail.push({ payload: payload, context: context });
            const fileName = context && context.fileName;
            if (mailDown.has(fileName)) {
                const err = new Error('Test double: sendmail unavailable for ' + fileName);
                err.statusCode = 503;
                throw err;
            }
            calls.mailSent.push({ payload: payload, context: context });
            return { sent: true, subject: payload.subject };
        }
    };

    function entryFileName(jobId) {
        // Map jobId back to a file name so per-file scripts can be looked up.
        const match = Object.keys(files).find(function (key) {
            const entry = files[key];
            return (entry.jobId || 'job-' + key) === jobId;
        });
        return match || jobId.replace(/^job-/, '');
    }

    return { uploader, textract, waiter, mailer, calls, store };
}

/**
 * Minimal but realistic FORMS response: one key/value pair ("Name: Ada").
 */
function defaultBlocks() {
    return [
        {
            Id: 'k1',
            BlockType: 'KEY_VALUE_SET',
            EntityTypes: ['KEY'],
            Relationships: [
                { Type: 'CHILD', Ids: ['w1'] },
                { Type: 'VALUE', Ids: ['v1'] }
            ]
        },
        {
            Id: 'v1',
            BlockType: 'KEY_VALUE_SET',
            EntityTypes: ['VALUE'],
            Relationships: [{ Type: 'CHILD', Ids: ['w2'] }]
        },
        { Id: 'w1', BlockType: 'WORD', Text: 'Name' },
        { Id: 'w2', BlockType: 'WORD', Text: 'Ada' }
    ];
}

module.exports = { createDoubles, defaultBlocks };
