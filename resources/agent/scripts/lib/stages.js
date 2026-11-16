'use strict';

/**
 * Per-file stages shared by the CLI scripts (uploadFile.js / processFile.js),
 * the batch runner (processBatch.js) and the BPMN-facing envelope contract.
 *
 * Every function in this module is total: a failing stage returns an
 * `{ ok: false, ... }` envelope instead of throwing, so one bad file can never
 * abort the surrounding batch. The raw error is converted with `buildError`
 * which always embeds the file name / S3 key so the failure can be located.
 */

const { PipelineError, EmptyAnalysisError, buildError, errorDetails } = require('./errors');
const { extractKeyValues } = require('./textract');
const { buildMailPayload, resolveUploadKey } = require('./mail');

/**
 * Upload a single file to S3 and produce a signed URL.
 *
 * @param {{name:string, filename:string}} file
 * @param {{bucket:string}} options
 * @param {{uploader:{upload:Function}}} adapters
 * @returns {Promise<object>} envelope:
 *   { ok:true, fileName, key, bucket, uploadResult }
 *   { ok:false, stage:'upload', fileName, key, bucket, error }
 */
async function uploadFile(file, options, adapters) {
    const fileName = file && file.name;
    const fullFileName = file && file.filename;
    const bucket = options && options.bucket;

    try {
        if (!fileName) throw new PipelineError('file name is required', { stage: 'upload' });
        if (!fullFileName) throw new PipelineError('file path is required for file=' + fileName, { stage: 'upload' });
        if (!adapters || !adapters.uploader) throw new PipelineError('uploader adapter is not configured', { stage: 'upload' });

        const uploadResult = await adapters.uploader.upload({
            fileName: fileName,
            fullFileName: fullFileName,
            bucket: bucket
        });

        if (!uploadResult) {
            throw new PipelineError('S3 upload returned no result for file=' + fileName, { stage: 'upload' });
        }

        return {
            ok: true,
            fileName: fileName,
            key: resolveUploadKey(uploadResult, fileName),
            bucket: uploadResult.Bucket || bucket,
            uploadResult: uploadResult
        };
    } catch (err) {
        return {
            ok: false,
            stage: 'upload',
            fileName: fileName || null,
            key: fileName || null,
            bucket: bucket || null,
            error: buildError('upload', err, { fileName: fileName, key: fileName, bucket: bucket })
        };
    }
}

/**
 * Run the asynchronous Textract analysis for one already uploaded object.
 *
 * The three AWS interactions (start job, wait for the SNS/SQS notification,
 * fetch the result) are separate injectable adapters. A failure in any of them
 * is reported under stage `textract` with an `operation` so callers can tell
 * "start failed" from "timed out waiting" from "result retrieval failed".
 *
 * @returns {Promise<object>} envelope:
 *   { ok:true, key, fileName, jobId, processResult }
 *   { ok:false, stage:'textract', operation, key, fileName, jobId, error }
 */
async function analyseFile(input, adapters) {
    const key = input && input.key;
    // The processFile CLI only receives the S3 key; the key doubles as the
    // file locator in that context.
    const fileName = (input && input.fileName) || key;
    const bucket = input && input.bucket;

    try {
        if (!key) throw new PipelineError('S3 key is required', { stage: 'textract', operation: 'startAnalysis' });
        if (!adapters || !adapters.textract || !adapters.waiter) {
            throw new PipelineError('textract/waiter adapters are not configured', {
                stage: 'textract',
                operation: 'startAnalysis'
            });
        }

        let started;
        try {
            started = await adapters.textract.startAnalysis({
                bucket: bucket,
                key: key,
                roleArn: input.roleArn,
                snsTopicArn: input.snsTopicArn
            });
        } catch (err) {
            return analysisFailure('startAnalysis', err, { fileName, key, bucket });
        }

        const startedJobId = started && (started.JobId || started.jobId);
        if (!startedJobId) {
            return analysisFailure(
                'startAnalysis',
                new PipelineError('Textract did not return a JobId for file=' + fileName, { stage: 'textract' }),
                { fileName, key, bucket }
            );
        }

        try {
            await adapters.waiter.waitForAnalysis({
                jobId: startedJobId,
                sqsQueueUrl: input.sqsQueueUrl,
                timeoutMs: input.waitTimeoutMs,
                pollDelayMs: input.pollDelayMs
            });
        } catch (err) {
            return analysisFailure('waitForAnalysis', err, { fileName, key, bucket, jobId: startedJobId });
        }

        let analysis;
        try {
            analysis = await adapters.textract.getAnalysis({ jobId: startedJobId });
        } catch (err) {
            return analysisFailure('getAnalysis', err, { fileName, key, bucket, jobId: startedJobId });
        }

        const keyValues = extractKeyValues(analysis);
        if (keyValues === null) {
            const err = new EmptyAnalysisError(
                'Textract returned no blocks for file=' + fileName + ', jobId=' + startedJobId,
                { jobId: startedJobId }
            );
            return analysisFailure('getAnalysis', err, { fileName, key, bucket, jobId: startedJobId });
        }

        return {
            ok: true,
            key: key,
            fileName: fileName,
            bucket: bucket,
            jobId: startedJobId,
            processResult: keyValues
        };
    } catch (err) {
        // Safety net: never let an unexpected shape abort the batch.
        return analysisFailure('startAnalysis', err, { fileName, key, bucket });
    }
}

function analysisFailure(operation, err, context) {
    const error = buildError('textract', err, Object.assign({ operation: operation }, context));
    if (error.operation === undefined) error.operation = operation;
    return {
        ok: false,
        stage: 'textract',
        operation: operation,
        fileName: context.fileName || null,
        key: context.key || null,
        bucket: context.bucket || null,
        jobId: context.jobId || null,
        error: error
    };
}

/**
 * End-to-end processing of one file: upload -> textract -> mail.
 *
 * Guarantees:
 *  - never throws; always returns an outcome with `status` 'success'/'failed'
 *  - a failure after the upload keeps `uploadResult` on the outcome so the
 *    object can still be inspected in S3
 *  - mail is only sent when every earlier stage succeeded
 */
async function runFile(file, options, adapters) {
    options = options || {};
    const outcome = {
        fileName: file && file.name,
        filename: file && file.filename,
        status: 'failed',
        key: file && file.name,
        bucket: options.bucket || null,
        uploadResult: null,
        processResult: null,
        mail: null,
        error: null
    };

    const upload = await uploadFile(file, options, adapters);
    if (!upload.ok) {
        outcome.error = upload.error;
        outcome.key = upload.key;
        outcome.bucket = upload.bucket;
        return outcome;
    }

    outcome.key = upload.key;
    outcome.bucket = upload.bucket;
    outcome.uploadResult = upload.uploadResult;

    const analysis = await analyseFile(
        {
            key: upload.key,
            fileName: file.name,
            bucket: outcome.bucket,
            roleArn: options.roleArn,
            snsTopicArn: options.snsTopicArn,
            sqsQueueUrl: options.sqsQueueUrl,
            waitTimeoutMs: options.waitTimeoutMs,
            pollDelayMs: options.pollDelayMs
        },
        adapters
    );

    if (!analysis.ok) {
        // Upload succeeded but recognition failed: keep the upload result for
        // troubleshooting and deliberately skip the mail.
        outcome.error = analysis.error;
        return outcome;
    }

    outcome.processResult = analysis.processResult;

    let payload;
    try {
        payload = buildMailPayload({
            processResult: analysis.processResult,
            uploadResult: outcome.uploadResult,
            fileName: file.name,
            mailTo: options.mailTo,
            onifyURL: options.onifyURL
        });
    } catch (err) {
        outcome.error = buildError('mail', err, {
            fileName: file.name,
            key: outcome.key,
            bucket: outcome.bucket,
            jobId: analysis.jobId
        });
        return outcome;
    }

    try {
        if (!adapters.mailer || typeof adapters.mailer.send !== 'function') {
            throw new PipelineError('mailer adapter is not configured', { stage: 'mail' });
        }
        const mailResult = await adapters.mailer.send(payload, {
            fileName: file.name,
            key: outcome.key
        });
        outcome.mail = { payload: payload, result: mailResult };
    } catch (err) {
        outcome.error = buildError('mail', err, {
            fileName: file.name,
            key: outcome.key,
            bucket: outcome.bucket,
            jobId: analysis.jobId
        });
        return outcome;
    }

    outcome.status = 'success';
    return outcome;
}

/**
 * Turn a list of per-file outcomes into the batch result.
 *
 * status:
 *   'success'         - every file succeeded (also for an empty inbox)
 *   'partial_failure' - at least one succeeded and at least one failed
 *   'failure'         - no file succeeded and at least one failed
 */
function summariseOutcomes(outcomes) {
    outcomes = Array.isArray(outcomes) ? outcomes : [];

    const succeeded = outcomes.filter(o => o.status === 'success');
    const failed = outcomes.filter(o => o.status !== 'success');

    let status;
    if (failed.length === 0) status = 'success';
    else if (succeeded.length > 0) status = 'partial_failure';
    else status = 'failure';

    return {
        status: status,
        allSucceeded: failed.length === 0,
        total: outcomes.length,
        succeeded: succeeded.length,
        failed: failed.length,
        succeededFiles: succeeded.map(o => o.fileName),
        failedFiles: failed.map(o => o.fileName),
        failures: failed.map(o => ({
            fileName: o.fileName,
            key: o.key,
            stage: o.error && o.error.stage,
            operation: o.error && o.error.operation,
            reason: o.error ? o.error.reason : 'unknown error'
        })),
        outcomes: outcomes
    };
}

/**
 * Process every file, isolating failures per file.
 *
 * Files are processed sequentially, matching the sequential multi-instance
 * subprocess in the BPMN flow.
 */
async function processBatch(files, options, adapters) {
    const list = Array.isArray(files) ? files : [];
    const outcomes = [];

    for (const file of list) {
        let outcome;
        try {
            outcome = await runFile(file, options, adapters);
        } catch (err) {
            // Defensive: an adapter bug must not abort the batch either.
            outcome = {
                fileName: file && file.name,
                filename: file && file.filename,
                status: 'failed',
                key: file && file.name,
                bucket: (options && options.bucket) || null,
                uploadResult: null,
                processResult: null,
                mail: null,
                error: buildError('batch', err, {
                    fileName: file && file.name,
                    key: file && file.name,
                    bucket: options && options.bucket
                })
            };
            outcome.error.reason = 'batch failed for file=' + (file && file.name) + ': ' + errorDetails(err).message;
        }
        outcomes.push(outcome);
    }

    return summariseOutcomes(outcomes);
}

module.exports = {
    uploadFile,
    analyseFile,
    runFile,
    processBatch,
    summariseOutcomes
};
