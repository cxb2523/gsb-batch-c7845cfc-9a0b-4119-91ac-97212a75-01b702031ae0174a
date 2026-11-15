/*
 * End-to-end acceptance test of the full "KPI Estimation" chain:
 *
 *   simulated InfluxDB query results
 *     -> real manual_join function code (extracted from the shipped flow)
 *     -> JSON message on MQTT topic "Mean"
 *     -> real Python DataAnalyzer.on_message / power_mean
 *     -> MeanResult (or a logged skip with no publication)
 *
 * The Python half runs in a child process using the machine's python.exe,
 * matching the "locally constructed InfluxDB results and MQTT messages"
 * verification described in the task.
 *
 * Run:  node tests/test_e2e_chain.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..');
const FLOW_FILE = path.join(
    REPO_ROOT,
    'src/solution/HandsOn_1/node_red/node-red/flows.json'
);
const RUNNER = path.join(REPO_ROOT, 'tests/e2e_analyzer_runner.py');

const BASE_TIME = new Date('2026-03-01T08:00:00.000Z').getTime();
const STEP_MS = 10000;

function ts(index) {
    return new Date(BASE_TIME + index * STEP_MS);
}

function influxRows(measurement, indexes, values) {
    return indexes.map((i, pos) => ({
        _time: ts(i),
        _value: values[pos],
        _field: 'value',
        _measurement: measurement,
    }));
}

function createSandbox(initialContext) {
    const store = new Map(Object.entries(initialContext || {}));
    const warnings = [];
    const sandbox = {
        Date, JSON, Object, Array, Math, String, Number, Boolean,
        isNaN, parseInt, parseFloat,
        context: {
            get: (key) => (store.has(key) ? store.get(key) : undefined),
            set: (key, value) => store.set(key, value),
        },
        node: {
            warn: (m) => warnings.push(String(m)),
            status: () => {},
        },
    };
    sandbox.global = sandbox;
    vm.createContext(sandbox);
    return {
        store,
        warnings,
        run(funcCode, msg) {
            sandbox.msg = msg;
            const wrapped = '(function () {\n' + funcCode + '\n})';
            return vm.runInContext(wrapped, sandbox, { filename: 'manual_join.js' })();
        },
    };
}

function loadManualJoin() {
    const flow = JSON.parse(fs.readFileSync(FLOW_FILE, 'utf8'));
    const node = flow.find((item) => item.id === '67b35a2b.c82f54');
    assert.ok(node, 'manual_join node not found');
    return node.func;
}

function runPythonSide(messages) {
    const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'kpi-e2e-'));
    const inputFile = path.join(tmpDir, 'messages.json');
    fs.writeFileSync(inputFile, JSON.stringify(messages), 'utf8');

    const candidates = ['python', 'python3', 'py'];
    let result = null;
    for (const python of candidates) {
        result = spawnSync(python, ['-I', RUNNER, inputFile], { encoding: 'utf8' });
        if (!result.error) {
            break;
        }
    }
    assert.ok(result && !result.error, 'could not start a Python interpreter: ' + (result && result.error));
    if (result.status !== 0) {
        throw new Error('Python runner failed (' + result.status + '):\n' + result.stdout + '\n' + result.stderr);
    }
    fs.unlinkSync(inputFile);
    fs.rmdirSync(tmpDir);
    return JSON.parse(result.stdout);
}

let passed = 0;
let failed = 0;
function scenario(name, fn) {
    try {
        fn();
        passed += 1;
        console.log('  PASS  ' + name);
    } catch (err) {
        failed += 1;
        console.log('  FAIL  ' + name);
        console.log('        ' + (err.stack || err).split('\n').join('\n        '));
    }
}

const manualJoin = loadManualJoin();

console.log('=== End-to-end: InfluxDB rows -> Node-RED manual_join -> MQTT Mean -> Python power_mean ===');

scenario('full aligned batch flows through and yields the correct MeanResult', () => {
    const env = createSandbox(); // n defaults to 50
    const indexes = Array.from({ length: 50 }, (_, i) => i);
    const currents = indexes.map((i) => 18 + (i % 3));      // 18..20 A
    const voltages = indexes.map(() => 49);                // 49 V

    env.run(manualJoin, {
        measurement: 'currentdrive3',
        payload: influxRows('currentdrive3', indexes, currents),
    });
    const joined = env.run(manualJoin, {
        measurement: 'voltagedrive3',
        payload: influxRows('voltagedrive3', indexes, voltages),
    });

    assert.ok(joined && joined.payload, 'manual_join must emit a batch once both sides reach n');
    assert.strictEqual(joined.payload.sample_number, 50);

    const mqttPayload = JSON.stringify(joined.payload);
    const py = runPythonSide([{ topic: 'Mean', payload: mqttPayload }]);

    assert.strictEqual(py.published.length, 1, 'exactly one MeanResult expected');
    assert.strictEqual(py.published[0][0], 'MeanResult');
    const result = JSON.parse(py.published[0][1]);
    const expectedMean = Math.round(
        (currents.reduce((acc, c) => acc + c * 49, 0) / 50) * 100
    ) / 100;
    assert.strictEqual(result.power_mean_result, expectedMean);
    assert.strictEqual(result.name, 'powerdrive3_mean');
});

scenario('single-side data never reaches MQTT Mean, so no MeanResult is published', () => {
    const env = createSandbox();
    const indexes = Array.from({ length: 20 }, (_, i) => i);
    const joined = env.run(manualJoin, {
        measurement: 'voltagedrive3',
        payload: influxRows('voltagedrive3', indexes, indexes.map(() => 49)),
    });

    assert.strictEqual(joined, null, 'no half batch may be produced');
    // Nothing is published to Mean, so the Python side receives no messages.
    const py = runPythonSide([]);
    assert.strictEqual(py.published.length, 0);
    assert.ok(env.warnings.some((w) => w.includes('waiting for complete batch')));
});

scenario('timestamp-misaligned full sides are trimmed to common pairs end to end', () => {
    const env = createSandbox({ sample_n: 4 });
    env.run(manualJoin, {
        measurement: 'currentdrive3',
        payload: influxRows('currentdrive3', [0, 1, 2, 3], [10, 10, 10, 10]),
    });
    const joined = env.run(manualJoin, {
        measurement: 'voltagedrive3',
        // only t1 and t2 overlap
        payload: influxRows('voltagedrive3', [1, 2, 9, 10], [5, 5, 5, 5]),
    });

    assert.ok(joined && joined.payload);
    assert.strictEqual(joined.payload.sample_number, 2);
    assert.deepStrictEqual(
        joined.payload.current_drive3_batch.map((r) => r._time.getTime()),
        joined.payload.voltage_drive3_batch.map((r) => r._time.getTime())
    );

    const py = runPythonSide([{ topic: 'Mean', payload: JSON.stringify(joined.payload) }]);
    assert.strictEqual(py.published.length, 1);
    const result = JSON.parse(py.published[0][1]);
    assert.strictEqual(result.power_mean_result, 50); // (10*5 + 10*5) / 2
});

scenario('Python side skips an empty pairing with a logged reason and no MeanResult', () => {
    // Simulate a malformed/empty batch arriving directly on MQTT (e.g. an
    // older producer). Node-RED no longer emits these, but the service must
    // defend itself instead of crashing.
    const emptyBatch = JSON.stringify({
        sample_number: 0,
        current_drive3_batch: [],
        voltage_drive3_batch: [],
    });
    const py = runPythonSide([{ topic: 'Mean', payload: emptyBatch }]);
    assert.strictEqual(py.published.length, 0);
    assert.ok(py.log.includes('empty pairing'), 'log was:\n' + py.log);
});

scenario('Python side skips a structurally invalid message and keeps serving later batches', () => {
    const validIndexes = Array.from({ length: 50 }, (_, i) => i);
    const validRows = influxRows('currentdrive3', validIndexes, validIndexes.map(() => 10));
    const validVoltage = influxRows('voltagedrive3', validIndexes, validIndexes.map(() => 4));

    const env = createSandbox();
    env.run(manualJoin, { measurement: 'currentdrive3', payload: validRows });
    const joined = env.run(manualJoin, { measurement: 'voltagedrive3', payload: validVoltage });

    const py = runPythonSide([
        { topic: 'Mean', payload: JSON.stringify({ current_drive3_batch: [], voltage_drive3_batch: [] }) },
        { topic: 'Mean', payload: 'not-json-at-all' },
        { topic: 'Mean', payload: JSON.stringify(joined.payload) },
    ]);

    assert.strictEqual(py.published.length, 1, 'only the valid batch is published');
    assert.strictEqual(py.published[0][0], 'MeanResult');
    assert.ok(py.log.includes('missing field(s)'));
    assert.ok(py.log.includes('not valid JSON'));
    const result = JSON.parse(py.published[0][1]);
    assert.strictEqual(result.power_mean_result, 40); // 10 * 4
});

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed === 0 ? 0 : 1);
