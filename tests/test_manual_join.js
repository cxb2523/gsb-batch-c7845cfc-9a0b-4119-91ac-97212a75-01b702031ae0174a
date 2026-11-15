/*
 * Acceptance tests for the hardened "KPI Estimation" Node-RED chain.
 *
 * The function code under test is extracted directly from the shipped flow
 * files and executed in a VM sandbox that mimics the Node-RED function-node
 * environment (context.get/set, node.warn, node.status). InfluxDB query
 * results are simulated as the row arrays the influxdb-in node would emit
 * ({_time: Date, _value: number, ...}).
 *
 * Run:  node tests/test_manual_join.js
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = path.resolve(__dirname, '..');

const FLOW_SOURCES = [
    {
        label: 'HandsOn_1 node_red/flows.json',
        file: path.join(REPO_ROOT, 'src/solution/HandsOn_1/node_red/node-red/flows.json'),
        manualJoinId: '67b35a2b.c82f54',
        voltageQueryId: 'e0680050.bd51a',
        currentQueryId: 'e483dac2.b82e5',
        flavor: 'handson1',
    },
    {
        label: 'HandsOn_1 IE Flow Creator/flow_data_gen.json',
        file: path.join(REPO_ROOT, 'src/solution/HandsOn_1/IE Flow Creator Flows/flow_data_gen.json'),
        manualJoinId: '67b35a2b.c82f54',
        voltageQueryId: 'e0680050.bd51a',
        currentQueryId: 'e483dac2.b82e5',
        flavor: 'handson1',
    },
    {
        label: 'HandsOn_2 flow_data_S7_Connector.json',
        file: path.join(REPO_ROOT, 'src/solution/HandsOn_2/IE Flow Creator Flows/flow_data_S7_Connector.json'),
        manualJoinId: 'da5a71e0e50b6a2d',
        voltageQueryId: '50952f79f64eb614',
        currentQueryId: '46b6e48d1b12352a',
        flavor: 'handson2',
    },
];

const BASE_TIME = new Date('2026-01-01T00:00:00.000Z').getTime();
const STEP_MS = 10000;

function timestamp(index) {
    return new Date(BASE_TIME + index * STEP_MS);
}

// Simulated InfluxDB rows, as produced by the influxdb-in node.
function makeRows(measurement, indexes, valueFn) {
    return indexes.map((i) => ({
        _time: timestamp(i),
        _value: valueFn(i),
        _field: 'value',
        _measurement: measurement,
    }));
}

// Minimal re-creation of a Node-RED function-node sandbox.
function createFunctionSandbox(initialContext) {
    const store = new Map(Object.entries(initialContext || {}));
    const warnings = [];
    const statuses = [];

    const sandbox = {
        Date,
        JSON,
        Object,
        Array,
        Math,
        String,
        Number,
        Boolean,
        isNaN,
        parseInt,
        parseFloat,
        context: {
            get: (key) => (store.has(key) ? store.get(key) : undefined),
            set: (key, value) => {
                store.set(key, value);
            },
        },
        node: {
            warn: (message) => warnings.push(String(message)),
            status: (status) => statuses.push(status),
        },
    };
    sandbox.global = sandbox;
    vm.createContext(sandbox);

    function run(funcCode, msg) {
        sandbox.msg = msg;
        // Wrapping in a function allows the top-level `return` used by
        // Node-RED function nodes.
        const wrapped = '(function () {\n' + funcCode + '\n})';
        return vm.runInContext(wrapped, sandbox, { filename: 'node-red-function.js' })();
    }

    return { store, warnings, statuses, run };
}

function loadNode(flowFile, nodeId) {
    const flow = JSON.parse(fs.readFileSync(flowFile, 'utf8'));
    const node = flow.find((item) => item.id === nodeId);
    assert.ok(node, 'node ' + nodeId + ' not found in ' + flowFile);
    assert.strictEqual(node.type, 'function');
    return node;
}

let passed = 0;
let failed = 0;

function test(name, fn) {
    try {
        fn();
        passed += 1;
        console.log('  PASS  ' + name);
    } catch (err) {
        failed += 1;
        console.log('  FAIL  ' + name);
        console.log('        ' + (err && err.stack ? err.stack.split('\n').join('\n        ') : err));
    }
}

for (const source of FLOW_SOURCES) {
    console.log('\n=== ' + source.label + ' ===');
    const manualJoinCode = loadNode(source.file, source.manualJoinId).func;

    test('default n is 50: single side below n sends nothing and keeps data', () => {
        const env = createFunctionSandbox();
        const voltageRows = makeRows('voltagedrive3', [0, 1, 2], (i) => 48 + i);

        const result = env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: voltageRows,
        });

        assert.strictEqual(result, null);
        assert.ok(Array.isArray(env.store.get('vd3_batch')));
        assert.strictEqual(env.store.get('vd3_batch').length, voltageRows.length);
        assert.strictEqual(env.store.get('cd3_batch'), undefined);
        assert.strictEqual(env.warnings.length, 1);
        assert.ok(env.warnings[0].includes('waiting for complete batch'));
    });

    test('half batch is never sent: current alone then voltage alone both wait', () => {
        const env = createFunctionSandbox({ sample_n: 5 });

        const r1 = env.run(manualJoinCode, {
            measurement: 'currentdrive3',
            payload: makeRows('currentdrive3', [0, 1, 2, 3, 4], (i) => 18 + i),
        });
        assert.strictEqual(r1, null);
        assert.strictEqual(env.store.get('cd3_batch').length, 5);

        const r2 = env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: makeRows('voltagedrive3', [0, 1, 2], (i) => 48 + i),
        });
        assert.strictEqual(r2, null);
        assert.strictEqual(env.store.get('vd3_batch').length, 3);
        // previously fetched current samples are retained
        assert.strictEqual(env.store.get('cd3_batch').length, 5);
    });

    test('complete sides are aligned by timestamp; sample_number = paired count', () => {
        const env = createFunctionSandbox();
        // current: t0..t49 (50 samples), voltage: t10..t59 (50 samples)
        // common timestamps: t10..t49 -> 40 pairs
        const currentRows = makeRows('currentdrive3', Array.from({ length: 50 }, (_, i) => i), (i) => 18 + i);
        const voltageRows = makeRows('voltagedrive3', Array.from({ length: 50 }, (_, i) => i + 10), (i) => 48 + (i % 3));

        env.run(manualJoinCode, { measurement: 'currentdrive3', payload: currentRows });
        const out = env.run(manualJoinCode, { measurement: 'voltagedrive3', payload: voltageRows });

        assert.ok(out && out.payload, 'a batch message must be returned once both sides reach n');
        const batch = out.payload;
        assert.strictEqual(batch.sample_number, 40);
        assert.strictEqual(batch.current_drive3_batch.length, 40);
        assert.strictEqual(batch.voltage_drive3_batch.length, 40);

        // every emitted pair shares the same timestamp and order is consistent
        for (let i = 0; i < 40; i += 1) {
            assert.strictEqual(
                batch.current_drive3_batch[i]._time.getTime(),
                batch.voltage_drive3_batch[i]._time.getTime(),
                'pair ' + i + ' is not timestamp aligned'
            );
            assert.strictEqual(batch.current_drive3_batch[i]._time.getTime(), timestamp(i + 10).getTime());
        }

        // buffers are consumed after a successful send. Use length checks
        // because arrays created inside the VM sandbox have a different realm
        // and therefore fail deepStrictEqual against outer-realm literals.
        assert.strictEqual(env.store.get('cd3_batch').length, 0);
        assert.strictEqual(env.store.get('vd3_batch').length, 0);
        assert.strictEqual(env.statuses.length, 1);
        assert.ok(env.statuses[0].text.includes('40 paired samples'));
    });

    test('emitted batch yields the correct power mean for the Python service', () => {
        const env = createFunctionSandbox({ sample_n: 3 });
        const currentRows = makeRows('currentdrive3', [0, 1, 2], () => 0).map((r, i) => {
            r._value = [2, 3, 4][i];
            return r;
        });
        const voltageRows = makeRows('voltagedrive3', [0, 1, 2], () => 10);

        env.run(manualJoinCode, { measurement: 'currentdrive3', payload: currentRows });
        const out = env.run(manualJoinCode, { measurement: 'voltagedrive3', payload: voltageRows });

        assert.strictEqual(out.payload.sample_number, 3);
        const currents = out.payload.current_drive3_batch.map((r) => r._value);
        const voltages = out.payload.voltage_drive3_batch.map((r) => r._value);
        const powerSum = currents.reduce((acc, c, i) => acc + c * voltages[i], 0);
        assert.strictEqual(Math.round((powerSum / out.payload.sample_number) * 100) / 100, 30);
    });

    test('no common timestamps: nothing sent and both buffers are retained', () => {
        const env = createFunctionSandbox({ sample_n: 2 });
        env.run(manualJoinCode, {
            measurement: 'currentdrive3',
            payload: makeRows('currentdrive3', [0, 1], (i) => 18 + i),
        });
        const out = env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: makeRows('voltagedrive3', [10, 11], (i) => 48 + i),
        });

        assert.strictEqual(out, null);
        assert.strictEqual(env.store.get('cd3_batch').length, 2);
        assert.strictEqual(env.store.get('vd3_batch').length, 2);
        assert.ok(env.warnings.some((w) => w.includes('no common timestamps')));

        // next round brings overlapping timestamps -> batch goes out
        const next = env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: makeRows('voltagedrive3', [0, 1], (i) => 48 + i),
        });
        assert.ok(next && next.payload);
        assert.strictEqual(next.payload.sample_number, 2);
    });

    test('sample_n from context is honored (n=3)', () => {
        const env = createFunctionSandbox({ sample_n: 3 });

        const r1 = env.run(manualJoinCode, {
            measurement: 'currentdrive3',
            payload: makeRows('currentdrive3', [0, 1], (i) => 18 + i),
        });
        assert.strictEqual(r1, null);

        env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: makeRows('voltagedrive3', [0, 1], (i) => 48 + i),
        });
        const r2 = env.run(manualJoinCode, {
            measurement: 'currentdrive3',
            payload: makeRows('currentdrive3', [0, 1, 2], (i) => 18 + i),
        });
        // current now has 3, voltage still 2
        assert.strictEqual(r2, null);

        const out = env.run(manualJoinCode, {
            measurement: 'voltagedrive3',
            payload: makeRows('voltagedrive3', [0, 1, 2], (i) => 48 + i),
        });
        assert.ok(out && out.payload);
        assert.strictEqual(out.payload.sample_number, 3);
    });

    test('unrelated measurements are ignored', () => {
        const env = createFunctionSandbox();
        const result = env.run(manualJoinCode, {
            measurement: 'powerdrive1',
            payload: makeRows('powerdrive1', [0], (i) => 900 + i),
        });
        assert.strictEqual(result, null);
        assert.strictEqual(env.store.get('cd3_batch'), undefined);
        assert.strictEqual(env.store.get('vd3_batch'), undefined);
    });

    test('string _time values are also aligned correctly', () => {
        const env = createFunctionSandbox({ sample_n: 2 });
        const currentRows = [
            { _time: '2026-01-01T00:00:00Z', _value: 2, _measurement: 'currentdrive3' },
            { _time: '2026-01-01T00:00:10Z', _value: 3, _measurement: 'currentdrive3' },
        ];
        const voltageRows = [
            { _time: '2026-01-01T00:00:00Z', _value: 10, _measurement: 'voltagedrive3' },
            { _time: '2026-01-01T00:00:10Z', _value: 10, _measurement: 'voltagedrive3' },
        ];
        env.run(manualJoinCode, { measurement: 'currentdrive3', payload: currentRows });
        const out = env.run(manualJoinCode, { measurement: 'voltagedrive3', payload: voltageRows });
        assert.strictEqual(out.payload.sample_number, 2);
    });

    if (source.flavor === 'handson1') {
        const voltageQueryCode = loadNode(source.file, source.voltageQueryId).func;
        const currentQueryCode = loadNode(source.file, source.currentQueryId).func;

        test('GET_LAST_ENTRIES survives an empty count result and fetches all rows', () => {
            const env = createFunctionSandbox();
            const out = env.run(voltageQueryCode, { payload: [] });
            assert.strictEqual(out.measurement, 'voltagedrive3');
            assert.strictEqual(out.SampleNumber, 50);
            assert.ok(!out.query.includes('limit('), 'no limit when fewer than n samples exist');
        });

        test('GET_LAST_ENTRIES applies dynamic n from sample_n context', () => {
            const env = createFunctionSandbox({ sample_n: 10 });
            const out = env.run(currentQueryCode, { payload: [{ _value: 10 }] });
            assert.strictEqual(out.measurement, 'currentdrive3');
            assert.strictEqual(out.SampleNumber, 10);
            assert.ok(out.query.includes('limit(n: 10)'), 'query was: ' + out.query);
            assert.ok(!out.query.includes('limit(n: 50)'));
        });

        test('GET_LAST_ENTRIES defaults to 50 and limits when enough rows exist', () => {
            const env = createFunctionSandbox();
            const out = env.run(voltageQueryCode, { payload: [{ _value: 73 }] });
            assert.ok(out.query.includes('limit(n: 50)'));
        });
    } else {
        const queryCode = loadNode(source.file, source.voltageQueryId).func;

        test('GET_LAST_ENTRIES uses dynamic n from sample_n context (HandsOn_2)', () => {
            const env = createFunctionSandbox({ sample_n: 7 });
            const out = env.run(queryCode, {
                payload: { measurement: 'voltagedrive3' },
            });
            assert.strictEqual(out.measurement, 'voltagedrive3');
            assert.ok(out.query.includes('limit(n: 7)'), 'query was: ' + out.query);
        });

        test('GET_LAST_ENTRIES defaults n to 50 (HandsOn_2)', () => {
            const env = createFunctionSandbox();
            const out = env.run(queryCode, {
                payload: { measurement: 'currentdrive3' },
            });
            assert.ok(out.query.includes('limit(n: 50)'));
        });
    }
}

console.log('\n' + passed + ' passed, ' + failed + ' failed');
process.exit(failed === 0 ? 0 : 1);
