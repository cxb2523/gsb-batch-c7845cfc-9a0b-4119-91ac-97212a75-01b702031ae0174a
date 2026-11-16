'use strict';

//Please see https://docs.aws.amazon.com/textract/latest/dg/api-async-roles.html on how to "Configuring Amazon Textract for Asynchronous Operations".

/**
 * Agent task: run asynchronous Textract analysis for one uploaded S3 object.
 *
 * Invocation contract is unchanged:
 *   node processFile.js <key> <bucket> <roleArn> <snsTopicArn> <sqsQueueUrl>
 *
 * Success output is unchanged: the extracted `{ "Key": "Value" }` object.
 *
 * Failures no longer throw (which used to abort the whole batch). They are
 * printed as a structured envelope and the process exits 0:
 *   { "ok": false, "stage": "textract", "operation": "waitForAnalysis",
 *     "fileName": ..., "key": ..., "bucket": ..., "jobId": ...,
 *     "error": { "reason": "...file=...jobId=..." } }
 */

const { createAdapters } = require('./lib/adapters');
const { analyseFile } = require('./lib/stages');

const key = process.argv[2];
const bucket = process.argv[3];
const roleArn = process.argv[4];
const snsTopicArn = process.argv[5];
const sqsQueueUrl = process.argv[6];

async function main() {
    const envelope = await analyseFile(
        {
            key: key,
            bucket: bucket,
            roleArn: roleArn,
            snsTopicArn: snsTopicArn,
            sqsQueueUrl: sqsQueueUrl
        },
        createAdapters()
    );

    if (envelope.ok) {
        // Keep the original wire format: just the key/value object.
        console.log(JSON.stringify(envelope.processResult));
        return;
    }

    console.log(JSON.stringify({
        ok: false,
        stage: envelope.stage,
        operation: envelope.operation,
        fileName: envelope.fileName,
        key: envelope.key,
        bucket: envelope.bucket,
        jobId: envelope.jobId,
        error: envelope.error
    }));
}

main().catch(function (err) {
    console.log(JSON.stringify({
        ok: false,
        stage: 'textract',
        operation: 'startAnalysis',
        fileName: key || null,
        key: key || null,
        bucket: bucket || null,
        jobId: null,
        error: {
            stage: 'textract',
            message: err && err.message ? err.message : String(err),
            reason: 'textract failed for file=' + (key || '<unknown>') + ': ' +
                (err && err.message ? err.message : String(err))
        }
    }));
});
