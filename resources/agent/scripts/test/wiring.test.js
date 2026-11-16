'use strict';

/**
 * Tests for adapter wiring: the default AWS path is never touched, the
 * `ONIFY_ADAPTERS_MODULE` injection seam works, and the lazy mailer receives
 * the BPMN-provided Onify URL rather than whatever is in the environment.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('path');

const { createAdapters, lazyMailer, loadAdapterModule } = require('../lib/adapters');
const { processBatch } = require('../lib/stages');

const ADAPTER_MODULE = path.join(__dirname, 'adapter-module.js');

test('lazyMailer defers construction until the first send and uses the given options', async () => {
    const posted = [];
    const mailer = lazyMailer({
        baseUrl: 'http://from-flow:3000',
        fetchImpl: async (url, init) => {
            posted.push({ url: url, init: init });
            return { ok: true, status: 200 };
        }
    });

    // Constructing the mailer must not require fetch/URL.
    assert.equal(posted.length, 0);

    await mailer.send({ to: 'a@b.c', subject: 's', html: 'h' });
    assert.equal(posted.length, 1);
    assert.equal(posted[0].url, 'http://from-flow:3000/tools/sendmail');
});

test('loadAdapterModule resolves a factory relative to the working directory', () => {
    const previous = process.env.ONIFY_TEST_SCRIPT;
    process.env.ONIFY_TEST_SCRIPT = '';
    try {
        const adapters = loadAdapterModule(path.relative(process.cwd(), ADAPTER_MODULE));
        assert.equal(typeof adapters.uploader.upload, 'function');
        assert.equal(typeof adapters.textract.startAnalysis, 'function');
        assert.equal(typeof adapters.waiter.waitForAnalysis, 'function');
        assert.equal(typeof adapters.mailer.send, 'function');
    } finally {
        if (previous === undefined) delete process.env.ONIFY_TEST_SCRIPT;
        else process.env.ONIFY_TEST_SCRIPT = previous;
    }
});

test('createAdapters honours the injected adapter module and never touches AWS', () => {
    const previous = process.env.ONIFY_ADAPTERS_MODULE;
    process.env.ONIFY_ADAPTERS_MODULE = ADAPTER_MODULE;
    try {
        const adapters = createAdapters();
        assert.equal(typeof adapters.uploader.upload, 'function');
        assert.equal(typeof adapters.mailer.send, 'function');
    } finally {
        if (previous === undefined) delete process.env.ONIFY_ADAPTERS_MODULE;
        else process.env.ONIFY_ADAPTERS_MODULE = previous;
    }
});

test('a batch driven through injected adapters reports partial_failure with locatable reasons', async () => {
    const previousModule = process.env.ONIFY_ADAPTERS_MODULE;
    const previousScript = process.env.ONIFY_TEST_SCRIPT;
    const fs = require('fs');
    const os = require('os');

    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wiring-'));
    const scriptPath = path.join(dir, 'script.json');
    fs.writeFileSync(scriptPath, JSON.stringify({
        files: { 'bad.pdf': { uploadError: { name: 'Error', message: 'Denied', code: 'AccessDenied' } } }
    }));
    process.env.ONIFY_ADAPTERS_MODULE = ADAPTER_MODULE;
    process.env.ONIFY_TEST_SCRIPT = scriptPath;

    try {
        const summary = await processBatch(
            [
                { name: 'good.pdf', filename: path.join(dir, 'good.pdf') },
                { name: 'bad.pdf', filename: path.join(dir, 'bad.pdf') }
            ],
            {
                bucket: 'b',
                roleArn: 'r',
                snsTopicArn: 't',
                sqsQueueUrl: 'q',
                mailTo: 'a@b.c',
                onifyURL: 'http://hub:3000'
            },
            createAdapters()
        );

        assert.equal(summary.status, 'partial_failure');
        assert.deepEqual(summary.succeededFiles, ['good.pdf']);
        assert.deepEqual(summary.failedFiles, ['bad.pdf']);
        assert.match(summary.failures[0].reason, /file=bad\.pdf/);
        assert.equal(summary.outcomes[0].mail.payload.subject, 'good.pdf');
    } finally {
        if (previousModule === undefined) delete process.env.ONIFY_ADAPTERS_MODULE;
        else process.env.ONIFY_ADAPTERS_MODULE = previousModule;
        if (previousScript === undefined) delete process.env.ONIFY_TEST_SCRIPT;
        else process.env.ONIFY_TEST_SCRIPT = previousScript;
    }
});
