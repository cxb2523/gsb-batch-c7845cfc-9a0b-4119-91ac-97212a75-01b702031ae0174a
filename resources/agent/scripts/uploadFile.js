'use strict';

/**
 * Agent task: upload one inbox file to S3 and return a signed URL.
 *
 * Invocation contract is unchanged:
 *   node uploadFile.js <fileName> <fullFileName> <bucket>
 *
 * Success output is unchanged as well: the raw S3 upload result object,
 * including `Bucket`, `Key`, `ETag`, `Location` and `SignedUrl`, so existing
 * flow expressions keep working.
 *
 * Failures no longer throw (which used to abort the whole batch). They are
 * printed as a structured envelope and the process exits 0 so the caller can
 * record the outcome and continue with the remaining files:
 *   { "ok": false, "stage": "upload", "fileName": ..., "key": ...,
 *     "bucket": ..., "error": { "reason": "...file=..." } }
 */

const { createAdapters } = require('./lib/adapters');
const { uploadFile } = require('./lib/stages');

const fileName = process.argv[2];
const fullFileName = process.argv[3];
const bucket = process.argv[4];

async function main() {
    const envelope = await uploadFile(
        { name: fileName, filename: fullFileName },
        { bucket: bucket },
        createAdapters()
    );

    if (envelope.ok) {
        // Keep the original wire format byte-for-byte.
        console.log(JSON.stringify(envelope.uploadResult));
        return;
    }

    console.log(JSON.stringify({
        ok: false,
        stage: envelope.stage,
        fileName: envelope.fileName,
        key: envelope.key,
        bucket: envelope.bucket,
        error: envelope.error
    }));
}

main().catch(function (err) {
    // Last-resort guard: never crash the agent task without an identifiable
    // failure record.
    console.log(JSON.stringify({
        ok: false,
        stage: 'upload',
        fileName: fileName || null,
        key: fileName || null,
        bucket: bucket || null,
        error: {
            stage: 'upload',
            message: err && err.message ? err.message : String(err),
            reason: 'upload failed for file=' + (fileName || '<unknown>') + ': ' +
                (err && err.message ? err.message : String(err))
        }
    }));
});
