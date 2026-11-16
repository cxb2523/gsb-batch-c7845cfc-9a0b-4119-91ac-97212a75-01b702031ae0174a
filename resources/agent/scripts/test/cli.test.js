'use strict';

/**
 * End-to-end CLI tests: they spawn the real agent scripts exactly the way the
 * BPMN flow does (same argv order) but point `ONIFY_ADAPTERS_MODULE` at the
 * in-memory doubles. No AWS credentials, no network, no Onify Hub.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile } = require('child_process');

const SCRIPTS_DIR = path.join(__dirname, '..');
const DOUBLES_MODULE = path.join(__dirname, 'adapter-module.js');
const NODE = process.execPath;

function runScript(script, args, env) {
    return new Promise(function (resolve) {
        execFile(
            NODE,
            [path.join(SCRIPTS_DIR, script)].concat(args),
            {
                cwd: SCRIPTS_DIR,
                env: Object.assign({}, process.env, env || {}),
                timeout: 20000
            },
            function (err, stdout, stderr) {
                resolve({ code: err ? err.code : 0, stdout: stdout, stderr: stderr });
            }
        );
    });
}

function tmpDir() {
    return fs.mkdtempSync(path.join(os.tmpdir(), 'textract-batch-'));
}

test('getInboxFiles keeps its original output contract', async () => {
    const dir = tmpDir();
    fs.writeFileSync(path.join(dir, 'a.pdf'), 'x');
    fs.writeFileSync(path.join(dir, 'b.pdf'), 'y');

    const result = await runScript('getInboxFiles.js', [dir + '/']);
    assert.equal(result.code, 0);

    const files = JSON.parse(result.stdout);
    assert.deepEqual(files.map(f => f.name), ['a.pdf', 'b.pdf']);
    for (const entry of files) {
        assert.equal(entry.filename, dir + '/' + entry.name);
    }
});

test('uploadFile prints the raw upload result on success', async () => {
    const dir = tmpDir();
    const scriptPath = path.join(dir, 'script.json');
    fs.writeFileSync(scriptPath, JSON.stringify({}));
    fs.writeFileSync(path.join(dir, 'a.pdf'), 'x');

    const result = await runScript(
        'uploadFile.js',
        ['a.pdf', path.join(dir, 'a.pdf'), 'my-bucket'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: scriptPath }
    );

    assert.equal(result.code, 0);
    const data = JSON.parse(result.stdout);
    assert.equal(data.Key, 'a.pdf');
    assert.equal(data.Bucket, 'my-bucket');
    assert.ok(data.SignedUrl.startsWith('https://signed.example/my-bucket/a.pdf'));
});

test('uploadFile prints a locatable failure envelope instead of crashing', async () => {
    const dir = tmpDir();
    const scriptPath = path.join(dir, 'script.json');
    fs.writeFileSync(scriptPath, JSON.stringify({
        files: { 'a.pdf': { uploadError: { name: 'Error', message: 'Access Denied', code: 'AccessDenied' } } }
    }));

    const result = await runScript(
        'uploadFile.js',
        ['a.pdf', path.join(dir, 'a.pdf'), 'my-bucket'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: scriptPath }
    );

    // Must not crash the agent task.
    assert.equal(result.code, 0);
    const data = JSON.parse(result.stdout);
    assert.equal(data.ok, false);
    assert.equal(data.stage, 'upload');
    assert.equal(data.fileName, 'a.pdf');
    assert.match(data.error.reason, /file=a\.pdf/);
    assert.match(data.error.reason, /Access Denied/);
});

test('processFile prints key/values on success and a failure envelope otherwise', async () => {
    const dir = tmpDir();
    const okScript = path.join(dir, 'ok.json');
    fs.writeFileSync(okScript, JSON.stringify({}));

    const ok = await runScript(
        'processFile.js',
        ['a.pdf', 'my-bucket', 'role', 'topic', 'queue'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: okScript }
    );
    assert.equal(ok.code, 0);
    assert.deepEqual(JSON.parse(ok.stdout), { Name: 'Ada' });

    const badScript = path.join(dir, 'bad.json');
    fs.writeFileSync(badScript, JSON.stringify({ files: { 'b.pdf': { jobFailed: true } } }));

    const bad = await runScript(
        'processFile.js',
        ['b.pdf', 'my-bucket', 'role', 'topic', 'queue'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: badScript }
    );
    assert.equal(bad.code, 0);
    const data = JSON.parse(bad.stdout);
    assert.equal(data.ok, false);
    assert.equal(data.stage, 'textract');
    assert.equal(data.operation, 'waitForAnalysis');
    assert.match(data.error.reason, /file=b\.pdf/);
    assert.match(data.error.reason, /jobId=job-b\.pdf/);
});

test('processBatch drives a full mixed batch from the inbox files', async () => {
    const root = tmpDir();
    const dir = path.join(root, 'inbox');
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, 'ok.pdf'), 'x');
    fs.writeFileSync(path.join(dir, 'fail-textract.pdf'), 'y');
    fs.writeFileSync(path.join(dir, 'fail-upload.pdf'), 'z');

    const scriptPath = path.join(root, 'script.json');
    fs.writeFileSync(scriptPath, JSON.stringify({
        files: {
            'fail-textract.pdf': { jobFailed: true, statusMessage: 'INVALID_DOCUMENT' },
            'fail-upload.pdf': { uploadError: { name: 'Error', message: 'S3 down', code: 'SlowDown' } }
        }
    }));

    const result = await runScript(
        'processBatch.js',
        [dir + '/', 'my-bucket', 'role', 'topic', 'queue', 'user@acme.com', 'http://localhost:3000'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: scriptPath }
    );

    // Partial failures are a normal batch outcome, not a task crash.
    assert.equal(result.code, 0, result.stderr);
    const summary = JSON.parse(result.stdout);

    assert.equal(summary.status, 'partial_failure');
    assert.equal(summary.total, 3);
    assert.equal(summary.succeeded, 1);
    assert.equal(summary.failed, 2);
    assert.deepEqual(summary.succeededFiles, ['ok.pdf']);
    assert.deepEqual(summary.failedFiles.sort(), ['fail-textract.pdf', 'fail-upload.pdf']);

    const textractFailure = summary.failures.find(f => f.fileName === 'fail-textract.pdf');
    assert.match(textractFailure.reason, /file=fail-textract\.pdf/);
    assert.match(textractFailure.reason, /INVALID_DOCUMENT/);

    const uploadFailure = summary.failures.find(f => f.fileName === 'fail-upload.pdf');
    assert.match(uploadFailure.reason, /file=fail-upload\.pdf/);
    assert.match(uploadFailure.reason, /S3 down/);

    // Upload result retained for the file whose recognition failed.
    const failedOutcome = summary.outcomes.find(o => o.fileName === 'fail-textract.pdf');
    assert.ok(failedOutcome.uploadResult);
    assert.equal(failedOutcome.mail, null);
});

test('processBatch exits non-zero only for batch-level configuration errors', async () => {
    const dir = tmpDir();
    const scriptPath = path.join(dir, 'script.json');
    fs.writeFileSync(scriptPath, JSON.stringify({}));

    const result = await runScript(
        'processBatch.js',
        [dir + '/', 'my-bucket', 'role', 'topic', 'queue', 'user@acme.com'],
        { ONIFY_ADAPTERS_MODULE: DOUBLES_MODULE, ONIFY_TEST_SCRIPT: scriptPath }
    );

    assert.equal(result.code, 1);
    const summary = JSON.parse(result.stdout);
    assert.equal(summary.status, 'failure');
    assert.equal(summary.stage, 'config');
    assert.match(summary.error.message, /onifyURL/);
});
