'use strict';

/**
 * Agent task: process the whole inbox as one fault-isolated batch.
 *
 * Invocation:
 *   node processBatch.js <inboxPath> <bucket> <roleArn> <snsTopicArn> \
 *                        <sqsQueueUrl> <mailTo> <onifyURL>
 *
 * Behaviour:
 *   - lists the inbox (same `{ name, filename }` entries getInboxFiles uses)
 *   - for every file: upload -> Textract -> mail
 *   - a failure in one file is recorded and processing continues
 *   - output always ends with a summary whose `status` is one of
 *       "success"         every file succeeded (including an empty inbox)
 *       "partial_failure" some succeeded, some failed
 *       "failure"         nothing succeeded
 *   - each failed file carries `error.reason` with its file name / S3 key, so
 *     it can be located without guessing
 *   - an uploaded file whose recognition failed keeps `uploadResult`
 *   - mail is only sent for files that completed every stage
 *
 * The process exits 0 even when some files failed: the flow must be able to
 * read the summary and continue, and a non-zero exit would make the agent
 * task itself look broken. Batch-level failures (unreadable inbox, missing
 * configuration) are still reported with exit code 1.
 */

const { listInboxFiles } = require('./lib/inbox');
const { processBatch } = require('./lib/stages');
const { createAdapters } = require('./lib/adapters');
const { PipelineError, buildError } = require('./lib/errors');

const inboxPath = process.argv[2];
const bucket = process.argv[3];
const roleArn = process.argv[4];
const snsTopicArn = process.argv[5];
const sqsQueueUrl = process.argv[6];
const mailTo = process.argv[7];
const onifyURL = process.argv[8];

const waitTimeoutMs = process.env.BATCH_WAIT_TIMEOUT_MS
    ? parseInt(process.env.BATCH_WAIT_TIMEOUT_MS, 10)
    : undefined;
const pollDelayMs = process.env.BATCH_POLL_DELAY_MS
    ? parseInt(process.env.BATCH_POLL_DELAY_MS, 10)
    : undefined;

async function main() {
    let files;
    try {
        files = listInboxFiles(inboxPath);
    } catch (err) {
        const error = buildError('inbox', err, { key: inboxPath });
        console.log(JSON.stringify({
            ok: false,
            status: 'failure',
            stage: 'inbox',
            inboxPath: inboxPath,
            total: 0,
            succeeded: 0,
            failed: 0,
            error: error,
            outcomes: []
        }));
        process.exitCode = 1;
        return;
    }

    const missing = [
        ['bucket', bucket],
        ['roleArn', roleArn],
        ['snsTopicArn', snsTopicArn],
        ['sqsQueueUrl', sqsQueueUrl],
        ['mailTo', mailTo],
        ['onifyURL', onifyURL]
    ].filter((entry) => !entry[1]);

    if (missing.length) {
        const error = buildError(
            'config',
            new PipelineError('Missing required parameters: ' + missing.map(e => e[0]).join(', '), {
                stage: 'config'
            }),
            {}
        );
        console.log(JSON.stringify({
            ok: false,
            status: 'failure',
            stage: 'config',
            total: files.length,
            succeeded: 0,
            failed: 0,
            error: error,
            outcomes: []
        }));
        process.exitCode = 1;
        return;
    }

    const summary = await processBatch(
        files,
        {
            bucket: bucket,
            roleArn: roleArn,
            snsTopicArn: snsTopicArn,
            sqsQueueUrl: sqsQueueUrl,
            mailTo: mailTo,
            onifyURL: onifyURL,
            waitTimeoutMs: waitTimeoutMs,
            pollDelayMs: pollDelayMs
        },
        createAdapters({
            // Send mail through the same Onify Hub endpoint the flow used.
            mail: { baseUrl: onifyURL, token: process.env.ONIFY_TOKEN }
        })
    );

    console.log(JSON.stringify(Object.assign({ ok: summary.allSucceeded, empty: summary.total === 0 }, summary)));
}

main().catch(function (err) {
    // This should be unreachable: processBatch isolates per-file failures.
    console.log(JSON.stringify({
        ok: false,
        status: 'failure',
        stage: 'batch',
        inboxPath: inboxPath,
        total: 0,
        succeeded: 0,
        failed: 0,
        error: {
            stage: 'batch',
            message: err && err.message ? err.message : String(err)
        },
        outcomes: []
    }));
    process.exitCode = 1;
});
