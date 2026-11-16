'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const { createDoubles } = require('./doubles');
const { uploadFile, analyseFile, runFile, processBatch, summariseOutcomes } = require('../lib/stages');

function file(name) {
    return { name: name, filename: '/inbox/' + name };
}

function options(overrides) {
    return Object.assign(
        {
            bucket: 'my-bucket',
            roleArn: 'arn:aws:iam::1:role/Textract',
            snsTopicArn: 'arn:aws:sns:eu-central-1:1:AmazonTextract',
            sqsQueueUrl: 'https://sqs.eu-central-1.amazonaws.com/1/test',
            mailTo: 'user@acme.com',
            onifyURL: 'http://localhost:3000'
        },
        overrides
    );
}

test('uploadFile reports a structured upload failure that names the file', async () => {
    const doubles = createDoubles({
        files: { 'broken.pdf': { uploadError: Object.assign(new Error('Access Denied'), { code: 'AccessDenied' }) } }
    });

    const result = await uploadFile(file('broken.pdf'), options(), doubles);

    assert.equal(result.ok, false);
    assert.equal(result.stage, 'upload');
    assert.equal(result.fileName, 'broken.pdf');
    assert.match(result.error.reason, /file=broken\.pdf/);
    assert.match(result.error.reason, /Access Denied/);
    assert.equal(result.error.code, 'AccessDenied');
});

test('analyseFile distinguishes start, wait and get failures and includes jobId', async () => {
    const names = ['a.pdf', 'b.pdf', 'c.pdf'];
    const doubles = createDoubles({
        files: {
            'a.pdf': { jobError: new Error('start rejected') },
            'b.pdf': { waitError: new Error('no notification') },
            'c.pdf': { getError: new Error('result expired') }
        }
    });

    const results = [];
    for (const name of names) {
        results.push(
            await analyseFile({ key: name, bucket: 'my-bucket' }, doubles)
        );
    }

    assert.equal(results[0].ok, false);
    assert.equal(results[0].operation, 'startAnalysis');
    assert.match(results[0].error.reason, /file=a\.pdf/);

    assert.equal(results[1].ok, false);
    assert.equal(results[1].operation, 'waitForAnalysis');
    assert.match(results[1].error.reason, /file=b\.pdf/);
    assert.match(results[1].error.reason, /job-?b\.pdf/);

    assert.equal(results[2].ok, false);
    assert.equal(results[2].operation, 'getAnalysis');
    assert.match(results[2].error.reason, /file=c\.pdf/);
    assert.match(results[2].error.reason, /job-?c\.pdf/);
});

test('analyseFile treats an empty Textract result as a failure', async () => {
    const doubles = createDoubles({ files: { 'empty.pdf': { blocks: [] } } });

    const result = await analyseFile({ key: 'empty.pdf', bucket: 'my-bucket' }, doubles);

    assert.equal(result.ok, false);
    assert.equal(result.operation, 'getAnalysis');
    assert.match(result.error.reason, /file=empty\.pdf/);
});

test('a recognition failure keeps the upload result but sends no mail', async () => {
    const doubles = createDoubles({
        files: { 'scan.pdf': { jobFailed: true, statusMessage: 'INVALID_DOCUMENT' } }
    });

    const outcome = await runFile(file('scan.pdf'), options(), doubles);

    assert.equal(outcome.status, 'failed');
    assert.ok(outcome.uploadResult, 'upload result must be preserved for troubleshooting');
    assert.equal(outcome.uploadResult.Key, 'scan.pdf');
    assert.equal(outcome.processResult, null);
    assert.equal(doubles.calls.mail.length, 0, 'no核对邮件 for a file that was not recognised');
    assert.equal(outcome.error.stage, 'textract');
    assert.match(outcome.error.reason, /file=scan\.pdf/);
    assert.match(outcome.error.reason, /INVALID_DOCUMENT/);
    assert.match(outcome.error.reason, /job-scan\.pdf/);
});

test('a mail failure keeps the extraction result and marks only that file failed', async () => {
    const doubles = createDoubles({ mailDown: ['m.pdf'] });

    const outcome = await runFile(file('m.pdf'), options(), doubles);

    assert.equal(outcome.status, 'failed');
    assert.ok(outcome.uploadResult);
    assert.deepEqual(outcome.processResult, { Name: 'Ada' });
    assert.equal(outcome.error.stage, 'mail');
    assert.match(outcome.error.reason, /file=m\.pdf/);
});

test('a successful file sends the unchanged mail payload', async () => {
    const doubles = createDoubles();

    const outcome = await runFile(file('job-application-001.pdf'), options(), doubles);

    assert.equal(outcome.status, 'success');
    assert.equal(doubles.calls.mail.length, 1);

    const payload = doubles.calls.mail[0].payload;
    assert.equal(payload.to, 'user@acme.com');
    assert.equal(payload.subject, 'job-application-001.pdf');

    // Exact signed URL must be embedded base64-encoded, not rebuilt.
    const signedUrl = outcome.uploadResult.SignedUrl;
    const expectedFormData = Buffer.from(JSON.stringify({ Name: 'Ada' })).toString('base64');
    const expectedFileUrl = Buffer.from(signedUrl).toString('base64');

    assert.ok(payload.html.includes('<a href="http://localhost:3000/form/processapplication?formdata=' +
        expectedFormData + '&fileurl=' + expectedFileUrl +
        '&filename=job-application-001.pdf">Process application</a>'));
    assert.ok(payload.html.includes('job-application-001.pdf<br>'));
});

test('a batch with mixed outcomes continues past failures and reports partial_failure', async () => {
    const doubles = createDoubles({
        files: {
            'fail-upload.pdf': { uploadError: new Error('connection reset') },
            'fail-textract.pdf': { jobFailed: true },
            'fail-mail.pdf': {}
        },
        mailDown: ['fail-mail.pdf']
    });

    const files = [
        file('ok-1.pdf'),
        file('fail-upload.pdf'),
        file('ok-2.pdf'),
        file('fail-textract.pdf'),
        file('fail-mail.pdf')
    ];

    const summary = await processBatch(files, options(), doubles);

    assert.equal(summary.status, 'partial_failure');
    assert.equal(summary.allSucceeded, false);
    assert.equal(summary.total, 5);
    assert.equal(summary.succeeded, 2);
    assert.equal(summary.failed, 3);
    assert.deepEqual(summary.succeededFiles, ['ok-1.pdf', 'ok-2.pdf']);
    assert.deepEqual(summary.failedFiles, ['fail-upload.pdf', 'fail-textract.pdf', 'fail-mail.pdf']);

    // Every failure must be locatable by file name.
    for (const failure of summary.failures) {
        assert.ok(failure.reason.includes(failure.fileName), 'reason must name ' + failure.fileName);
    }

    // Mail is attempted only for the two fully recognised files.
    assert.deepEqual(doubles.calls.mail.map(c => c.context.fileName), ['ok-1.pdf', 'ok-2.pdf', 'fail-mail.pdf']);
    assert.deepEqual(doubles.calls.mailSent.map(c => c.context.fileName), ['ok-1.pdf', 'ok-2.pdf']);

    // All five files were attempted (no early abort).
    assert.deepEqual(doubles.calls.upload.map(c => c.fileName), files.map(f => f.name));
});

test('outcome for a textract failure still exposes uploadResult in the summary', async () => {
    const doubles = createDoubles({ files: { 'x.pdf': { jobFailed: true } } });
    const summary = await processBatch([file('x.pdf')], options(), doubles);

    assert.equal(summary.status, 'failure');
    const outcome = summary.outcomes[0];
    assert.ok(outcome.uploadResult);
    assert.equal(outcome.uploadResult.Key, 'x.pdf');
    assert.equal(outcome.mail, null);
});

test('all-success and all-failure batches are distinguishable', async () => {
    const okSummary = await processBatch([file('a.pdf'), file('b.pdf')], options(), createDoubles());
    assert.equal(okSummary.status, 'success');
    assert.equal(okSummary.allSucceeded, true);
    assert.equal(okSummary.failed, 0);

    const badSummary = await processBatch(
        [file('a.pdf'), file('b.pdf')],
        options(),
        createDoubles({ files: { 'a.pdf': { jobFailed: true }, 'b.pdf': { jobFailed: true } } })
    );
    assert.equal(badSummary.status, 'failure');
    assert.equal(badSummary.succeeded, 0);
});

test('an empty inbox is a successful batch', async () => {
    const summary = await processBatch([], options(), createDoubles());
    assert.equal(summary.status, 'success');
    assert.equal(summary.total, 0);
});

test('success mail payload matches the original BPMN expression for the resolved key', async () => {
    // Independent reimplementation of the expression that used to live inline
    // in the BPMN `sendMail` task (with XML entities already resolved).
    //
    // The original referenced `uploadFileResult.key`; the Onify agent may
    // return either `key` or the AWS SDK's `Key`, so the resolver feeds this
    // expression the same value it would have received there.
    function originalPayload(processFileResult, uploadFileResult, resolvedKey, inboxFileName, mailTo, onifyURL) {
        let formDataEncoded = Buffer.from(JSON.stringify(processFileResult)).toString('base64');
        let fileSignedUrlEncoded = Buffer.from(uploadFileResult.SignedUrl).toString('base64');
        let URL = onifyURL + '/form/processapplication?formdata=' + formDataEncoded +
            '&fileurl=' + fileSignedUrlEncoded + '&filename=' + inboxFileName;

        var emailBody = `

Hello!<br>
<br>
${resolvedKey}<br>
<br>
<a href="${URL}">Process application</a>

`;

        return { to: mailTo, subject: resolvedKey, html: emailBody };
    }

    const doubles = createDoubles();
    const name = 'job-application-001.pdf';
    const opts = options();
    const outcome = await runFile(file(name), opts, doubles);

    assert.equal(outcome.status, 'success');
    const expected = originalPayload(
        outcome.processResult,
        outcome.uploadResult,
        outcome.key,
        name,
        opts.mailTo,
        opts.onifyURL
    );

    assert.deepEqual(doubles.calls.mailSent[0].payload, expected);
});

test('upload key resolution prefers lowercase key, then Key, then the file name', () => {
    const { resolveUploadKey } = require('../lib/mail');

    // Onify agent may expose the key in either casing.
    assert.equal(resolveUploadKey({ key: 'lower.pdf', Key: 'upper.pdf' }, 'fallback.pdf'), 'lower.pdf');
    assert.equal(resolveUploadKey({ Key: 'upper.pdf' }, 'fallback.pdf'), 'upper.pdf');
    assert.equal(resolveUploadKey({}, 'fallback.pdf'), 'fallback.pdf');
    assert.equal(resolveUploadKey(null, 'fallback.pdf'), 'fallback.pdf');
});

test('summariseOutcomes tolerates unknown outcome shapes', () => {
    const summary = summariseOutcomes([{ status: 'success', fileName: 'a' }, {}]);
    assert.equal(summary.status, 'partial_failure');
    assert.equal(summary.failed, 1);
    assert.match(summary.failures[0].reason, /unknown error/);
});

test('failure reasons are single-line and bounded, even for noisy errors', async () => {
    const noisy = new Error('Cannot find module\nRequire stack:\n- a\n- b\n' + 'x'.repeat(2000));
    const doubles = createDoubles({ files: { 'noisy.pdf': { uploadError: noisy } } });

    const summary = await processBatch([file('noisy.pdf')], options(), doubles);
    const reason = summary.failures[0].reason;

    assert.equal(/\r|\n/.test(reason), false, 'reason must stay on one line');
    assert.ok(reason.length < 700, 'reason must be bounded');
    assert.match(reason, /file=noisy\.pdf/);
});

test('no mail is attempted when the upload itself fails', async () => {
    const doubles = createDoubles({ files: { 'u.pdf': { uploadError: new Error('boom') } } });

    const outcome = await runFile(file('u.pdf'), options(), doubles);

    assert.equal(outcome.status, 'failed');
    assert.equal(outcome.uploadResult, null);
    assert.equal(doubles.calls.mail.length, 0);
    assert.equal(outcome.error.stage, 'upload');
});
