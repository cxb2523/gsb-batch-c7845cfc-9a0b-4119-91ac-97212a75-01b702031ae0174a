# Local Testing — Bronze Event Hub Ingestion

The live ingestion (`eventhub_raw → eventhub_parsed → orders`) is testable on a
laptop with **no Event Hub connection string and no Databricks secrets**. The
Event Hub read is wrapped behind an injectable input source.

## Injection entry

`src/bronze/transformations/_shared/eventhub_sources.py` defines the
`EventHubSource` interface (`read_stream(spark) -> DataFrame`) and the factory
`build_eventhub_source(spark, dbutils, source=None)`. The implementation is
chosen by the Spark configuration key **`bronze.eventhub.source`**:

| Value | Implementation | Secret access? |
|---|---|---|
| `kafka` (default) | `KafkaEventHubSource` — real Event Hub via the Kafka connector | Yes (`eh-conn-str`) |
| `local-file` | `LocalFileEventHubSource` — streams newline-delimited JSON files from `bronze.eventhub.source.path` | **No** |
| programmatic | `InMemoryEventHubSource` — in-memory records written to a scratch dir and read as a stream (used by pytest) | **No** |

Every source returns a streaming DataFrame normalized identically
(`value` cast to string, `event_time`, `ingestion_time`), so the parse / reject
/ dedup logic is source-agnostic. In SDP the key is set under
**Pipeline settings → Configuration**; locally the tests set it on the
`SparkSession`.

## What the tests prove

`tests/test_eventhub_ingestion.py` runs the production logic in `_shared/` on a
local Spark + Delta session (`availableNow` trigger, one input file per
micro-batch):

1. **Routing** — valid messages land in `orders`; malformed JSON and
   missing/blank `order_id` messages land in `01_bronze.order_rejections` with
   `raw_value`, `event_time`, `ingestion_time`, `rejection_reason`, and never
   enter `orders`.
2. **Incremental dedup** — the same `order_id` delivered twice inside one
   micro-batch and again redelivered in a later micro-batch leaves exactly one
   row. Dedup is the watermarked stateful `dropDuplicates` plus an idempotent
   Delta MERGE (`merge_flow` in SDP), both incremental — no full recompute.
3. **Wiring** — the actual transformation modules declare the
   `order_rejections` append flow, the `orders` MERGE flow, and the unchanged
   historical `once=True` append flow.

The SDP `@dp.merge_flow` is emulated locally by `foreachBatch` + Delta MERGE
(`_shared.order_ingest.merge_orders_batch`); `pyspark.pipelines` is replaced by
a recording stub in `conftest.py` because the business logic lives in `_shared`,
not in the decorators.

## Running

```bash
# Python 3.11 + Java 17 required; all pip deps go into a local venv
bash src/bronze/tests/run_local_tests.sh

# Or manually:
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r src/bronze/tests/requirements.txt
pytest src/bronze/tests -v
```

### Portable / air-gapped Windows toolchain

On a machine with no Python or Java on `PATH`, the runner also auto-detects a
self-contained toolchain laid out under `../tools` (relative to the repo
root): a NuGet-distributed CPython, a Temurin JDK 17, and a minimal
`hadoop/bin` containing `winutils.exe` plus a small JNI `hadoop.dll` (source
in `../tools/hadoop/src`, built with the VS 2022 C++ toolchain via
`build_natives.bat`). The DLL implements only the unguarded Windows entry
points — `NativeIO$Windows.access0`, the JNI group-mapping anchor, and
`NativeCrc32` chunked CRC32/CRC32C (delegated to the JDK's own
`java.util.zip`) — while every guarded native method stays on the pure-Java /
`winutils` fallback. Delta JARs are read from `wheels/jars` via
`LOCAL_DELTA_JARS`, so no Maven/Ivy access is needed. This tooling exists
only for local testing; the Databricks runtime ships its own complete
Hadoop/Java stack and never uses these files.

### Simulating messages by hand (local-file source)

```bash
mkdir -p /tmp/eh-input
cat > /tmp/eh-input/batch-0001.json <<'EOF'
{"timestamp":"2026-04-09T10:00:00.000Z","value":"{\"order_id\":\"O-1\",\"timestamp\":\"2026-04-09T10:00:00.000Z\",\"restaurant_id\":\"R-1\",\"customer_id\":\"C-1\",\"order_type\":\"delivery\",\"items\":[],\"total_amount\":12.5,\"payment_method\":\"card\",\"order_status\":\"placed\",\"created_at\":\"2026-04-09T10:00:00.000Z\"}"}
{"timestamp":"2026-04-09T10:01:00.000Z","value":"this is not json"}
{"timestamp":"2026-04-09T10:02:00.000Z","value":"{\"order_id\":\"O-1\",\"timestamp\":\"2026-04-09T10:00:00.000Z\"}"}
EOF
```

Configure the pipeline (or a local `SparkSession`) with:

```
bronze.eventhub.source=local-file
bronze.eventhub.source.path=/tmp/eh-input
```

The first record inserts `O-1`, the second is rejected as `malformed_json`, and
the third (a redelivery of `O-1`) is absorbed by the MERGE.

## Production behavior preserved

* Default `bronze.eventhub.source=kafka`, same Kafka options and
  `failOnDataLoss=true` as before.
* The historical backfill flow remains `@dp.append_flow(once=True)` with the
  same projection — it is not deduped and not part of the MERGE.
* `eventhub_parsed` keeps its original output columns; `eventhub_rejected` and
  `order_rejections` are additive.
