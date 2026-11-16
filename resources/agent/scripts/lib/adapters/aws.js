'use strict';

/**
 * Production adapters backed by the AWS SDK (v2, same as the original
 * scripts) and the Onify Hub mail endpoint.
 *
 * Nothing in this file touches the network or reads credentials at require
 * time: `aws-sdk` and `aws_config.json` are loaded inside the factory so the
 * rest of the code (and the tests) can be used without AWS credentials.
 */

const fs = require('fs');
const path = require('path');

const { PipelineError, TextractJobFailedError, JobWaitTimeoutError } = require('../errors');

const DEFAULT_WAIT_TIMEOUT_MS = 5 * 60 * 1000;
const DEFAULT_POLL_SECONDS = 10;
const DEFAULT_SIGNED_URL_EXPIRES = 3600;

function loadAwsConfig() {
    // Same file and same location the original scripts used.
    return require(path.join(__dirname, '..', '..', 'aws_config.json'));
}

function sleep(ms) {
    return new Promise(function (resolve) {
        setTimeout(resolve, ms);
    });
}

function pick(value, fallback) {
    return value === undefined || value === null ? fallback : value;
}

/**
 * Create a pre-signed GET URL, supporting both the promise helper and the
 * classic callback signature so older aws-sdk v2 builds keep working.
 */
function signGetObject(s3, params) {
    if (typeof s3.getSignedUrlPromise === 'function') {
        return s3.getSignedUrlPromise('getObject', params);
    }
    return new Promise(function (resolve, reject) {
        s3.getSignedUrl('getObject', params, function (err, url) {
            if (err) reject(err);
            else resolve(url);
        });
    });
}

/**
 * @param {object} [options]
 * @param {object} [options.AWS]        injected aws-sdk namespace (tests)
 * @param {object} [options.config]     injected credentials/region
 * @param {object} [options.s3]         injected S3 client
 * @param {object} [options.textract]   injected Textract client
 * @param {object} [options.sqs]        injected SQS client
 * @param {function} [options.sleep]    injected sleep (tests)
 */
function createAwsAdapters(options) {
    options = options || {};

    const AWS = options.AWS || require('aws-sdk');
    const AWS_Config = options.config || loadAwsConfig();
    AWS.config.update(AWS_Config);

    // Each client is created lazily and can be injected independently. This
    // lets a caller (or a test) supply only the service under test, and keeps
    // `require('aws-sdk')` / client construction out of module load time.
    const clients = {};
    function client(name) {
        if (clients[name]) return clients[name];
        const injected = options[name];
        if (injected) {
            clients[name] = injected;
            return injected;
        }
        const Constructor = AWS[name === 's3' ? 'S3' : name === 'sqs' ? 'SQS' : 'Textract'];
        clients[name] = new Constructor();
        return clients[name];
    }

    const wait = options.sleep || sleep;

    const uploader = {
        async upload(input) {
            const body = fs.readFileSync(input.fullFileName);
            const data = await client('s3')
                .upload({ Bucket: input.bucket, Key: input.fileName, Body: body })
                .promise();

            const signedUrl = await signGetObject(client('s3'), {
                Bucket: data.Bucket || input.bucket,
                Key: input.fileName,
                Expires: DEFAULT_SIGNED_URL_EXPIRES
            });

            data.SignedUrl = signedUrl;
            return data;
        }
    };

    const textractAdapter = {
        async startAnalysis(input) {
            const data = await client('textract')
                .startDocumentAnalysis({
                    DocumentLocation: {
                        S3Object: {
                            Bucket: input.bucket,
                            Name: input.key
                        }
                    },
                    FeatureTypes: ['FORMS'],
                    NotificationChannel: {
                        RoleArn: input.roleArn,
                        SNSTopicArn: input.snsTopicArn
                    }
                })
                .promise();

            return { JobId: data.JobId };
        },

        async getAnalysis(input) {
            // Textract paginates large documents; the original script only
            // read the first page of blocks.
            const blocks = [];
            let nextToken;
            let response;

            do {
                response = await client('textract')
                    .getDocumentAnalysis({ JobId: input.jobId, NextToken: nextToken })
                    .promise();

                if (response.JobStatus === 'FAILED') {
                    throw new TextractJobFailedError(
                        'Textract job failed for file=' + (input.fileName || input.jobId) +
                            ', jobId=' + input.jobId + ': ' +
                            (response.StatusMessage || 'JobStatus=FAILED'),
                        { jobId: input.jobId, status: 'FAILED', statusMessage: response.StatusMessage }
                    );
                }

                if (Array.isArray(response.Blocks)) blocks.push.apply(blocks, response.Blocks);
                nextToken = response.NextToken;
            } while (nextToken);

            return { Blocks: blocks };
        }
    };

    const waiter = {
        async waitForAnalysis(input) {
            const timeoutMs = pick(input.timeoutMs, DEFAULT_WAIT_TIMEOUT_MS);
            const pollSeconds = pick(input.pollSeconds, DEFAULT_POLL_SECONDS);
            // Real SQS long-polling already blocks for `WaitTimeSeconds`, so no
            // extra delay is needed by default. `pollDelayMs` exists so callers
            // (and tests whose SQS fake returns immediately) can avoid a busy
            // loop.
            const pollDelayMs = pick(input.pollDelayMs, 0);
            const startedAt = Date.now();

            while (Date.now() - startedAt < timeoutMs) {
                const response = await client('sqs')
                    .receiveMessage({
                        QueueUrl: input.sqsQueueUrl,
                        WaitTimeSeconds: pollSeconds,
                        MaxNumberOfMessages: 5
                    })
                    .promise();

                const messages = (response && response.Messages) || [];
                let matched = false;
                for (const message of messages) {
                    const job = parseNotification(message.Body);
                    if (!job || job.JobId !== input.jobId) continue;
                    matched = true;

                    // Always remove the notification we consumed, whether the
                    // job succeeded or failed.
                    await client('sqs')
                        .deleteMessage({
                            QueueUrl: input.sqsQueueUrl,
                            ReceiptHandle: message.ReceiptHandle
                        })
                        .promise();

                    if (job.Status === 'FAILED') {
                        throw new TextractJobFailedError(
                            'Textract job failed for file=' + (input.fileName || input.jobId) +
                                ', jobId=' + input.jobId + ': ' +
                                (job.StatusMessage || 'Status=FAILED'),
                            { jobId: input.jobId, status: job.Status, statusMessage: job.StatusMessage }
                        );
                    }

                    if (job.Status === 'SUCCEEDED' || job.Status === undefined) {
                        return job;
                    }
                }

                if (!matched && pollDelayMs > 0) {
                    await wait(pollDelayMs);
                }
            }

            throw new JobWaitTimeoutError(
                'Timed out after ' + timeoutMs + 'ms waiting for Textract jobId=' + input.jobId,
                { jobId: input.jobId }
            );
        }
    };

    return { uploader, textract: textractAdapter, waiter };
}

/**
 * Parse the SNS envelope delivered to SQS. Returns null for unrelated
 * messages so the waiter can skip them.
 */
function parseNotification(body) {
    try {
        const envelope = JSON.parse(body);
        const inner = envelope.Message ? JSON.parse(envelope.Message) : envelope;
        if (!inner || !inner.JobId) return null;
        return inner;
    } catch (err) {
        return null;
    }
}

/**
 * Mail adapter for the Onify Hub `/tools/sendmail` endpoint.
 *
 * The signature matches what the BPMN connector sends, so swapping between
 * the connector and this adapter does not change the mail a user receives.
 */
function createOnifyMailer(options) {
    options = options || {};
    const baseUrl = options.baseUrl || process.env.ONIFY_URL;
    const token = options.token || process.env.ONIFY_TOKEN;
    const fetchImpl = options.fetchImpl || global.fetch;

    if (!baseUrl) {
        throw new PipelineError('Onify mailer requires a base URL (onifyURL)', { stage: 'mail' });
    }
    if (typeof fetchImpl !== 'function') {
        throw new PipelineError('No fetch implementation available for the Onify mailer', { stage: 'mail' });
    }

    return {
        async send(payload) {
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers.Authorization = 'Bearer ' + token;

            const response = await fetchImpl(baseUrl.replace(/\/+$/, '') + '/tools/sendmail', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify(payload)
            });

            if (response && response.ok === false) {
                throw new PipelineError(
                    'Onify sendmail returned HTTP ' + response.status + ' for subject=' + payload.subject,
                    { stage: 'mail', statusCode: response.status }
                );
            }

            return { sent: true, subject: payload.subject };
        }
    };
}

module.exports = {
    createAwsAdapters,
    createOnifyMailer,
    parseNotification,
    DEFAULT_WAIT_TIMEOUT_MS
};
