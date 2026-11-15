# Acceptance tests for the hardened "KPI Estimation" chain

These tests verify the fix for the KPI collection chain without requiring a
running InfluxDB or MQTT broker: InfluxDB query results are simulated as the
row arrays the Node-RED `influxdb-in` node would emit, and MQTT messages are
simulated in memory (Node) / with a fake paho client (Python).

## What is covered

1. **Node-RED side** (`test_manual_join.js`)
   - A batch is only built when **both** `currentdrive3` and `voltagedrive3`
     hold at least `n` samples, where `n = context.get('sample_n')` with a
     default of `50`.
   - If one side is short, the already fetched samples stay in node context
     and **no half batch is published** to the `Mean` topic.
   - The two sides are aligned by their InfluxDB timestamp (`_time`): only
     samples sharing a common timestamp are paired, and `sample_number` equals
     the number of actual pairs.
   - `GET_LAST_ENTRIES` tolerates empty count results and uses the dynamic `n`
     in its Flux `limit(n: ...)` instead of a hardcoded 50.
   - The function code under test is extracted directly from the shipped flow
     JSON files, so the tests cannot drift from what is deployed.

2. **Python analytics side** (`test_data_analytics.py`)
   - `power_mean` validates the batch structure: non-object payloads, missing
     fields, non-list/empty batches, length mismatches, invalid or inconsistent
     `sample_number`, and rows missing `_value` are all logged and skipped
     without publishing `MeanResult` and without raising.
   - Valid batches publish the correct power mean on `MeanResult`.
   - Malformed JSON in `on_message` is logged and no longer kills the MQTT
     callback thread.

3. **End-to-end** (`test_e2e_chain.js` + `e2e_analyzer_runner.py`)
   - Simulated InfluxDB rows -> the real `manual_join` code -> a JSON MQTT
     `Mean` message -> the real Python `DataAnalyzer` -> `MeanResult`
     (or a logged skip).

## Running

From the repository root, with Node.js and Python on `PATH`:

```bash
node tests/test_manual_join.js
python -I tests/test_data_analytics.py
node tests/test_e2e_chain.js
```

`-I` (isolated mode) ensures the Python test imports only standard library
modules plus the analytics module under test. The paho MQTT client is stubbed
inside the tests, so no broker and no third-party package are needed.
