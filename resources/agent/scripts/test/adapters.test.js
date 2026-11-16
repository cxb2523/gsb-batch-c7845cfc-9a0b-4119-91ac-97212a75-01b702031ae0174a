'use strict';

/**
 * Tests for the production adapters, with the AWS SDK and Onify HTTP layer
 * replaced by tiny fakes. This exercises the real adapter code (pagination,
 * SNS envelope parsing, failure detection, signed URL handling, mail HTTP
 * call) without credentials or network.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');

const { createAwsAdapters, createOnifyMailer, parseNotification } = require('../lib/adapters/aws');
const { processBatch } = require('../lib/stages');

function tmpFile(name, contents) {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'adapter-'));
    const filePath = path.join(dir, name);
    fs.writeFileSync(filePath, contents || 'pdf-bytes');
    return filePath;
}

function fakeAws(config) {
    return {
        config: {
            update(options) {
                config.updated = options;
            }
        }
    };
}

function service(handlers) {
    return function () {
        return handlers;
    };
}

function promiseOf(value) {
    return { promise: async () => value };
}

test('aws uploader uploads and returns a signed URL', async () => {
    const fullFileName = tmpFile('doc.pdf');
    const uploaded = [];
    const captured = {};
    const AWS = fakeAws(captured);

    const adapters = createAwsAdapters({
        AWS: AWS,
        config: { region: 'eu-central-1' },
        s3: {
            upload(params) {
                uploaded.push(params);
                return promiseOf({ Bucket: params.Bucket, Key: params.Key, ETag: '"e"' });
            },
            async getSignedUrlPromise(op, params) {
                return 'https://signed/' + params.Key + '?op=' + op + '&expires=' + params.Expires;
            }
        }
    });

    const result = await adapters.uploader.upload({
        fileName: 'doc.pdf',
        fullFileName: fullFileName,
        bucket: 'my-bucket'
    });

    assert.equal(uploaded.length, 1);
    assert.equal(uploaded[0].Key, 'doc.pdf');
    assert.ok(Buffer.isBuffer(uploaded[0].Body));
    assert.equal(result.Key, 'doc.pdf');
    assert.equal(result.SignedUrl, 'https://signed/doc.pdf?op=getObject&expires=3600');
    assert.deepEqual(captured.updated, { region: 'eu-central-1' });
});

test('aws textract adapter paginates blocks and detects FAILED jobs', async () => {
    const calls = [];
    const adapters = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {},
        textract: {
            startDocumentAnalysis(params) {
                calls.push(params);
                return promiseOf({ JobId: 'job-1' });
            },
            getDocumentAnalysis(params) {
                if (params.NextToken === undefined) {
                    return promiseOf({ Blocks: [{ Id: 'a' }], NextToken: 'next' });
                }
                return promiseOf({ Blocks: [{ Id: 'b' }] });
            }
        }
    });

    const started = await adapters.textract.startAnalysis({
        bucket: 'b',
        key: 'k.pdf',
        roleArn: 'role',
        snsTopicArn: 'topic'
    });
    assert.equal(started.JobId, 'job-1');
    assert.equal(calls[0].DocumentLocation.S3Object.Name, 'k.pdf');
    assert.deepEqual(calls[0].FeatureTypes, ['FORMS']);

    const analysis = await adapters.textract.getAnalysis({ jobId: 'job-1' });
    assert.deepEqual(analysis.Blocks, [{ Id: 'a' }, { Id: 'b' }]);

    const failing = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {},
        textract: {
            getDocumentAnalysis() {
                return promiseOf({ JobStatus: 'FAILED', StatusMessage: 'INVALID_DOCUMENT' });
            }
        }
    });
    await assert.rejects(
        () => failing.textract.getAnalysis({ jobId: 'job-2' }),
        (err) => err.name === 'TextractJobFailedError' && /INVALID_DOCUMENT/.test(err.message)
    );
});

test('aws waiter ignores unrelated messages and reports job failure', async () => {
    const deleted = [];
    const messages = [
        { ReceiptHandle: 'h1', Body: JSON.stringify({ Message: JSON.stringify({ JobId: 'other' }) }) },
        { ReceiptHandle: 'h2', Body: JSON.stringify({ Message: JSON.stringify({ JobId: 'target', Status: 'SUCCEEDED' }) }) }
    ];

    const adapters = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {},
        textract: {},
        sqs: {
            receiveMessage() {
                return promiseOf({ Messages: messages });
            },
            deleteMessage(params) {
                deleted.push(params.ReceiptHandle);
                return promiseOf({});
            }
        }
    });

    const result = await adapters.waiter.waitForAnalysis({ jobId: 'target', sqsQueueUrl: 'q', timeoutMs: 5000 });
    assert.equal(result.JobId, 'target');
    assert.deepEqual(deleted, ['h2'], 'only the matching notification is consumed');
});

test('aws waiter surfaces a failed Textract job', async () => {
    const adapters = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {},
        textract: {},
        sqs: {
            receiveMessage() {
                return promiseOf({
                    Messages: [{
                        ReceiptHandle: 'h',
                        Body: JSON.stringify({
                            Message: JSON.stringify({ JobId: 'j', Status: 'FAILED', StatusMessage: 'BAD_DOC' })
                        })
                    }]
                });
            },
            deleteMessage() {
                return promiseOf({});
            }
        }
    });

    await assert.rejects(
        () => adapters.waiter.waitForAnalysis({ jobId: 'j', sqsQueueUrl: 'q', timeoutMs: 1000 }),
        (err) => err.name === 'TextractJobFailedError' && /BAD_DOC/.test(err.message)
    );
});

test('aws waiter times out instead of spinning forever', async () => {
    const adapters = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {},
        textract: {},
        sqs: {
            receiveMessage() {
                return promiseOf({});
            }
        }
    });

    await assert.rejects(
        () => adapters.waiter.waitForAnalysis({ jobId: 'j', sqsQueueUrl: 'q', timeoutMs: 0 }),
        (err) => err.name === 'JobWaitTimeoutError' && /jobId=j/.test(err.message)
    );
});

test('parseNotification tolerates unrelated and malformed bodies', () => {
    assert.equal(parseNotification('not json'), null);
    assert.equal(parseNotification(JSON.stringify({ Message: JSON.stringify({ NoJob: true }) })), null);
    assert.deepEqual(
        parseNotification(JSON.stringify({ Message: JSON.stringify({ JobId: 'x', Status: 'SUCCEEDED' }) })),
        { JobId: 'x', Status: 'SUCCEEDED' }
    );
});

test('onify mailer posts the payload and reports HTTP failures', async () => {
    const posted = [];
    const okMailer = createOnifyMailer({
        baseUrl: 'http://hub:3000/',
        fetchImpl: async (url, init) => {
            posted.push({ url, init });
            return { ok: true, status: 200 };
        }
    });

    const result = await okMailer.send({ to: 'a@b.c', subject: 's', html: 'h' });
    assert.equal(result.sent, true);
    assert.equal(posted[0].url, 'http://hub:3000/tools/sendmail');
    assert.equal(posted[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(posted[0].init.body), { to: 'a@b.c', subject: 's', html: 'h' });

    const badMailer = createOnifyMailer({
        baseUrl: 'http://hub:3000',
        fetchImpl: async () => ({ ok: false, status: 502 })
    });
    await assert.rejects(
        () => badMailer.send({ subject: 's' }),
        (err) => /502/.test(err.message)
    );
});

test('production adapters drive a full mixed batch', async () => {
    const okPath = tmpFile('ok.pdf');
    const badPath = tmpFile('bad.pdf');
    const mailPayloads = [];

    const adapters = createAwsAdapters({
        AWS: fakeAws({}),
        config: {},
        s3: {
            upload(params) {
                return promiseOf({ Bucket: params.Bucket, Key: params.Key });
            },
            async getSignedUrlPromise(op, params) {
                return 'https://signed/' + params.Key;
            }
        },
        textract: {
            startDocumentAnalysis(params) {
                if (params.DocumentLocation.S3Object.Name === 'bad.pdf') {
                    const err = new Error('AccessDeniedException');
                    err.code = 'AccessDeniedException';
                    throw err;
                }
                return promiseOf({ JobId: 'job-' + params.DocumentLocation.S3Object.Name });
            },
            getDocumentAnalysis() {
                return promiseOf({
                    Blocks: [
                        { Id: 'k', BlockType: 'KEY_VALUE_SET', EntityTypes: ['KEY'], Relationships: [{ Type: 'CHILD', Ids: ['w1'] }] },
                        { Id: 'w1', BlockType: 'WORD', Text: 'Name' }
                    ]
                });
            }
        },
        sqs: {
            receiveMessage(params) {
                return promiseOf({
                    Messages: [{
                        ReceiptHandle: 'h-ok',
                        Body: JSON.stringify({
                            Message: JSON.stringify({ JobId: 'job-ok.pdf', Status: 'SUCCEEDED' })
                        })
                    }]
                });
            },
            deleteMessage() {
                return promiseOf({});
            }
        }
    });

    adapters.mailer = {
        async send(payload) {
            mailPayloads.push(payload);
            return { sent: true };
        }
    };

    const summary = await processBatch(
        [
            { name: 'bad.pdf', filename: badPath },
            { name: 'ok.pdf', filename: okPath }
        ],
        {
            bucket: 'b',
            roleArn: 'r',
            snsTopicArn: 't',
            sqsQueueUrl: 'q',
            mailTo: 'a@b.c',
            onifyURL: 'http://hub:3000',
            // Keep the test fast even if the fake returned unrelated messages.
            waitTimeoutMs: 1000,
            pollDelayMs: 1
        },
        adapters
    );

    assert.equal(summary.status, 'partial_failure');
    const bad = summary.outcomes.find(o => o.fileName === 'bad.pdf');
    assert.equal(bad.error.stage, 'textract');
    assert.match(bad.error.reason, /file=bad\.pdf/);

    const ok = summary.outcomes.find(o => o.fileName === 'ok.pdf');
    assert.equal(ok.status, 'success');
    assert.equal(mailPayloads.length, 1);
    assert.equal(mailPayloads[0].subject, 'ok.pdf');
    assert.ok(mailPayloads[0].html.includes(Buffer.from('https://signed/ok.pdf').toString('base64')));
});
