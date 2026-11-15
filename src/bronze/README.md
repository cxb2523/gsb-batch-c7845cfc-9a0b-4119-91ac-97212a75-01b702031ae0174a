# Bronze Layer Ingestion Pipeline

Lakeflow Spark Declarative Pipeline that ingests data from Azure Event Hubs
and Azure SQL Server into the `restaurant_chain_db` Unity Catalog.

## Architecture

```
Azure Event Hubs (Kafka)          Azure SQL Server (JDBC)
        │                          │          │         │         │
        ▼                          ▼          ▼         ▼         ▼
┌──────────────┐          ┌────────────┐┌──────────┐┌──────┐┌───────┐
│ eventhub_raw │          │ historical ││customers ││ ...  ││reviews│
│     (ST)     │          │  _orders   ││  (MV)    ││ (MV) ││ (MV)  │
│  00_landing  │          │   (MV)     ││01_bronze ││      ││       │
└──────┬───────┘          │ 00_landing │└──────────┘└──────┘└───────┘
       │                  └─────┬──────┘
       ▼                        │
┌──────────────┐                │
│eventhub_parsed│               │
│ (temp view)  │                │
└──────┬───────┘                │
       │                        │
       ▼         once ──────────┘
┌──────────────┐
│    orders    │
│     (ST)     │
│  01_bronze   │
└──────────────┘

ST = Streaming Table    MV = Materialized View
```

## Data Flow

**Event Hubs path** (real-time orders):
`Event Hubs → eventhub_raw (ST) → eventhub_parsed (temp view) → orders (ST)`

**SQL Server path** (dimension snapshots):
`SQL Server → customers / restaurants / menu_items / reviews (MVs)`

**Historical backfill** (one-time):
`SQL Server → historical_orders (MV) → orders (ST, once=True append flow)`

## Why These Table Types?

| Table type | When to use | This pipeline |
|---|---|---|
| **Streaming Table** | Source is streaming or incrementally growing | `eventhub_raw`, `orders` |
| **Materialized View** | Source is batch (JDBC, federation) | Dimension tables, `historical_orders` |
| **Temporary View** | Intermediate logic, zero storage | `eventhub_parsed` |

Streaming tables **require** a streaming source (`spark.readStream`). JDBC reads
return batch DataFrames — making them MVs is the only correct option. A separate
silver pipeline can use `create_auto_cdc_from_snapshot_flow` on these bronze MVs
to build SCD Type 2 dimensions.

## File Structure

```
transformations/
  00_landing/
    eventhub_raw.py          # ST — raw Kafka messages from Event Hubs
    historical_orders.py     # MV — raw JDBC snapshot (self-contained)
  01_bronze/
    read_sql_server.py       # shared JDBC reader (utility, no datasets)
    customers.py             # MV — PII masked (SHA-256: name, email, phone)
    restaurants.py           # MV — quality checks
    menu_items.py            # MV — quality checks on PKs and pricing
    reviews.py               # MV — quality checks (review_text unmasked)
    eventhub_parsed.py       # temp view — JSON parsing of EventHub messages
    orders.py                # ST — unified orders (live + historical backfill)
```

**Shared module pattern**: `read_sql_server.py` uses `SparkSession.getActiveSession()`
so it works as a standard Python import (SDP-injected globals like `spark` are not
available in imported modules). Only `dbutils` is passed as a parameter for secret
access. Files in the same directory import it directly — no `sys.path` needed.

`historical_orders.py` (in `00_landing/`) has its own inline JDBC helper because
cross-directory imports in SDP require `sys.path` hacks.

## Pipeline Configuration

Set in the pipeline settings (Settings > Configuration):

| Key | Purpose | Example |
|---|---|---|
| `EH_NAMESPACE` | Event Hubs namespace | `res-orders-dev` |
| `EH_NAME` | Event Hubs topic name | `orders` |
| `startingOffsets` | Kafka starting position | `earliest` |
| `maxOffsetsPerTrigger` | Max events per micro-batch | `10000` |
| `SQL_HOST_NAME` | Azure SQL Server hostname | `res-chain-db-server.database.windows.net` |
| `SQL_DB_NAME` | Azure SQL database name | `res-chain-db` |

## Secrets

Stored in Databricks Secret Scope `res-chain-pipeline`:

| Key | Purpose |
|---|---|
| `eh-conn-str` | Event Hubs connection string |
| `db_admin_username` | SQL Server username |
| `db_admin_password` | SQL Server password |

## Data Quality

| Table | Hard constraints (fail) | Soft constraints (warn) |
|---|---|---|
| `eventhub_raw` | — | topic, value, event_time, ingestion_time NOT NULL |
| `eventhub_parsed` | — | order_id NOT NULL (valid parse) |
| `customers` | customer_id NOT NULL | — |
| `restaurants` | restaurant_id NOT NULL | — |
| `menu_items` | restaurant_id, item_id NOT NULL | price > 0 |
| `reviews` | review_id NOT NULL | rating BETWEEN 1 AND 5 |

## Downstream Dependencies

This pipeline's bronze outputs are designed to feed a **separate silver pipeline**
that applies SCD Type 2 tracking via `create_auto_cdc_from_snapshot_flow`. The
bronze MVs act as snapshot sources for change detection.

| Bronze dataset | Expected silver usage |
|---|---|
| `customers` | CDC → dim_customers (SCD2) |
| `restaurants` | CDC → dim_restaurants (SCD2) |
| `menu_items` | CDC → dim_menu_items (SCD2) |
| `reviews` | CDC → dim_reviews (SCD2) |
| `orders` | Direct read or streaming read |
