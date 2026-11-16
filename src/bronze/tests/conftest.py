"""Test harness for the bronze Event Hub ingestion.

These tests run the *production* transformation logic (the modules under
``src/bronze/transformations``) against a local Spark session with Delta Lake.
No Event Hub connection string, Databricks workspace or secret scope is needed:

* ``pyspark.pipelines`` is replaced by a tiny recording stub (SDP decorators
  only register datasets; the logic under test lives in ``_shared``).
* the Event Hub input is injected as an :class:`InMemoryEventHubSource`,
  i.e. in-memory records materialised to local files and read as a stream.
* the SDP ``merge_flow`` is emulated with Delta MERGE in ``foreachBatch``
  (``_shared.order_ingest.merge_orders_batch``), giving the same idempotent
  one-row-per-order_id semantics.

Run:  ``bash src/bronze/tests/run_local_tests.sh``
"""
import os
import sys
import types
from pathlib import Path

import pytest

# ── Repository paths ──────────────────────────────────────────────
REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
TRANSFORMATIONS_DIR = os.path.join(
    REPO_ROOT, "src", "bronze", "transformations"
)
SHARED_DIR = os.path.join(TRANSFORMATIONS_DIR, "_shared")
# SDP puts every transformation directory on the import path; mirror that so
# the production modules (00_landing/, 01_bronze/) import the same way locally.
for path in (
    TRANSFORMATIONS_DIR,
    SHARED_DIR,
    os.path.join(TRANSFORMATIONS_DIR, "00_landing"),
    os.path.join(TRANSFORMATIONS_DIR, "01_bronze"),
):
    if path not in sys.path:
        sys.path.insert(0, path)


# ── Minimal pyspark.pipelines stub ────────────────────────────────
# Records every SDP dataset/flow declaration so wiring tests can assert on it
# without a Databricks runtime. Decorators simply return the function intact.
class _RecordingDecorator:
    def __init__(self, kind, **kwargs):
        self.kind = kind
        self.kwargs = kwargs

    def __call__(self, func):
        REGISTERED[func.__name__] = {
            "kind": self.kind,
            "name": func.__name__,
            "kwargs": self.kwargs,
            "func": func,
        }
        return func


REGISTERED = {}


def _decorator(kind):
    def factory(*args, **kwargs):
        # Support both @dp.table(...) and bare usage styles.
        if args and callable(args[0]) and not kwargs:
            return _RecordingDecorator(kind)(args[0])
        return _RecordingDecorator(kind, **kwargs)

    return factory


dp_stub = types.ModuleType("pyspark.pipelines")
dp_stub.table = _decorator("table")
dp_stub.temporary_view = _decorator("temporary_view")
dp_stub.materialized_view = _decorator("materialized_view")
dp_stub.append_flow = _decorator("append_flow")
dp_stub.merge_flow = _decorator("merge_flow")
dp_stub.expect = _decorator("expect")
dp_stub.expect_or_fail = _decorator("expect_or_fail")
dp_stub.expect_all_or_fail = _decorator("expect_all_or_fail")
dp_stub.create_streaming_table = lambda *a, **k: REGISTERED.setdefault(
    "_create_streaming_table_%s" % (a[0] if a else k.get("name", "?")),
    {"kind": "create_streaming_table", "kwargs": {"args": a, **k}},
)
sys.modules["pyspark.pipelines"] = dp_stub


# ── Spark session with Delta Lake ─────────────────────────────────
@pytest.fixture(scope="session")
def spark_session(tmp_path_factory):
    from pyspark.sql import SparkSession

    warehouse = str(tmp_path_factory.mktemp("warehouse"))
    builder = (
        SparkSession.builder.master("local[2]")
        .appName("bronze-eventhub-tests")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.sql.warehouse.dir", warehouse)
        .config("spark.sql.shuffle.partitions", "2")
        .config("spark.ui.enabled", "false")
        .config("spark.driver.host", "127.0.0.1")
        # Kept harmless: the portable Windows toolchain ships a minimal
        # hadoop.dll (access0/initNative only). Heavy native paths stay off
        # either way and fall back to the winutils shell commands.
        .config("spark.hadoop.io.native.lib.available", "false")
    )

    # Two ways to put Delta on the classpath:
    #  * LOCAL_DELTA_JARS env var (semicolon/comma-separated JAR paths) for
    #    air-gapped / portable runs — no Maven/Ivy resolution.
    #  * otherwise the standard delta-spark helper, which fetches the Delta
    #    JARs from Maven via spark.jars.packages.
    local_jars = os.environ.get("LOCAL_DELTA_JARS")
    if local_jars:
        # Hadoop needs proper file: URIs on Windows (bare D:/... is parsed as
        # scheme "D"); accept ; or , separated paths in either separator style.
        jar_uris = []
        for jar in local_jars.replace(";", ",").split(","):
            jar = jar.strip()
            if jar and "://" not in jar:
                jar = Path(jar).resolve().as_uri()
            if jar:
                jar_uris.append(jar)
        builder = builder.config("spark.jars", ",".join(jar_uris))
        spark = builder.getOrCreate()
    else:
        from delta import configure_spark_with_delta_pip

        spark = configure_spark_with_delta_pip(builder).getOrCreate()

    spark.sparkContext.setLogLevel("WARN")
    yield spark
    spark.stop()


@pytest.fixture()
def env(spark_session, tmp_path):
    """Fresh catalog/database + checkpoint + input dirs per test."""
    db_name = f"bronze_test_{os.path.basename(str(tmp_path)).replace('-', '_')}"
    spark_session.sql(f"CREATE DATABASE IF NOT EXISTS {db_name}")
    spark_session.sql(f"USE {db_name}")

    input_dir = tmp_path / "eventhub_input"
    checkpoint_dir = tmp_path / "checkpoints"
    input_dir.mkdir()
    checkpoint_dir.mkdir()

    from eventhub_sources import InMemoryEventHubSource

    source = InMemoryEventHubSource(str(input_dir), spark_session)
    yield {
        "spark": spark_session,
        "db": db_name,
        "source": source,
        "checkpoint_dir": str(checkpoint_dir),
    }
    spark_session.sql(f"DROP DATABASE IF EXISTS {db_name} CASCADE")
