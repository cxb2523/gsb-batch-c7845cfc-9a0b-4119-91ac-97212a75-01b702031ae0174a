'use strict';

/**
 * Error helpers shared by the individual CLI scripts and the batch runner.
 *
 * Every failure that leaves the library is turned into a plain object that
 * contains enough context to locate the file it belongs to (file name, S3
 * key, bucket, Textract job id).  Callers must never have to guess which file
 * failed from a generic "something went wrong" string.
 */

class PipelineError extends Error {
    constructor(message, details) {
        super(message);
        this.name = 'PipelineError';
        this.stage = details && details.stage;
        if (details) {
            if (details.code) this.code = details.code;
            if (details.jobId) this.jobId = details.jobId;
            if (details.statusCode) this.statusCode = details.statusCode;
            if (details.status) this.status = details.status;
            if (details.statusMessage) this.statusMessage = details.statusMessage;
            if (details.cause) this.cause = details.cause;
            if (details.uploadResult) this.uploadResult = details.uploadResult;
        }
    }
}

class TextractJobFailedError extends PipelineError {
    constructor(message, details) {
        super(message, Object.assign({ stage: 'textract' }, details));
        this.name = 'TextractJobFailedError';
    }
}

class JobWaitTimeoutError extends PipelineError {
    constructor(message, details) {
        super(message, Object.assign({ stage: 'wait' }, details));
        this.name = 'JobWaitTimeoutError';
    }
}

class EmptyAnalysisError extends PipelineError {
    constructor(message, details) {
        super(message, Object.assign({ stage: 'textract' }, details));
        this.name = 'EmptyAnalysisError';
    }
}

/**
 * Make a raw error message safe to embed in a one-line `reason` field.
 *
 * Module resolution errors arrive with a multi-line stack ("Require stack:")
 * and can be long, which breaks the "one locatable line per file" contract.
 */
function cleanMessage(message, maxLength) {
    const limit = maxLength || 500;
    let text = message === undefined || message === null ? 'Unknown error' : String(message);
    text = text.replace(/\r\n|\r|\n/g, ' ').replace(/\s+/g, ' ').trim();
    return text.length > limit ? text.slice(0, limit) + '...' : text;
}

/**
 * Pick the useful, serialisable fields out of an Error (or arbitrary thrown
 * value) without leaking a stack trace into machine readable output.
 */
function errorDetails(err) {
    if (err === null || err === undefined) {
        return { name: 'Error', message: 'Unknown error' };
    }
    if (typeof err !== 'object') {
        return { name: 'Error', message: cleanMessage(err) };
    }

    var details = {
        name: err.name || 'Error',
        message: cleanMessage(err.message || String(err))
    };

    ['code', 'statusCode', 'requestId', 'jobId', 'status', 'statusMessage'].forEach(function (field) {
        if (err[field] !== undefined && err[field] !== null) {
            details[field] = err[field];
        }
    });

    return details;
}

function contextLabel(context) {
    if (!context) return '';
    var parts = [];
    if (context.fileName) parts.push('file=' + context.fileName);
    if (context.key && context.key !== context.fileName) parts.push('key=' + context.key);
    if (context.bucket) parts.push('bucket=' + context.bucket);
    if (context.jobId) parts.push('jobId=' + context.jobId);
    return parts.join(', ');
}

/**
 * Build the error object stored on a failed per-file outcome.
 *
 * The `reason` string always starts with the stage and the file it refers
 * to, e.g.
 *   textract failed for file=job-application-002.pdf, bucket=my-bucket,
 *   jobId=abc: Job failed: INVALID_DOCUMENT
 */
function buildError(stage, err, context) {
    context = context || {};
    var details = errorDetails(err);
    var jobId = context.jobId || details.jobId;

    var reasonContext = {
        fileName: context.fileName,
        key: context.key,
        bucket: context.bucket,
        jobId: jobId
    };
    var label = contextLabel(reasonContext);
    var reason = stage + ' failed' + (label ? ' for ' + label : '') + ': ' + details.message;

    var result = {
        stage: stage,
        message: details.message,
        reason: reason,
        fileName: context.fileName || null,
        key: context.key || null,
        bucket: context.bucket || null
    };

    if (context.operation) result.operation = context.operation;
    if (jobId) result.jobId = jobId;
    ['name', 'code', 'statusCode', 'requestId', 'status', 'statusMessage'].forEach(function (field) {
        if (details[field] !== undefined) result[field] = details[field];
    });

    return result;
}

module.exports = {
    PipelineError,
    TextractJobFailedError,
    JobWaitTimeoutError,
    EmptyAnalysisError,
    errorDetails,
    buildError,
    cleanMessage
};
