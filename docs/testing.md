# Testing

What the test suite covers, and what it deliberately does not.

## What runs

The suite has two layers, both offline.

### DAG integrity (`test_dag_integrity.py`)

These tests load the DagBag and assert that no file in the dags folder raised
on import, that the three expected task IDs (`load_locations`,
`fetch_weather_data`, `insert_weather_data`) exist and that their dependency
order is unchanged. A syntax error or a missing provider package otherwise
shows up in the UI as nothing at all rather than as an error. They need an
Airflow install, matching the CI environment.

### ETL unit tests (`test_weather_pipeline.py`)

The fetch and insert logic lives in `dags/weather_pipeline.py`, which imports
no Airflow code. These tests run without Airflow, an API key or a database:

- the OpenWeather HTTP layer is replaced with a fake returning canned
  responses, including timeouts, non-200 status codes and bodies missing
  `main` / `wind` / `weather`;
- the database is replaced with an in-memory fake hook that enforces the
  unique `(city, date)` index and emulates `INSERT ... ON CONFLICT DO UPDATE`.

They assert that:

1. with some cities failing, the other cities are still written and the
   failure list (city plus reason) is complete and visible in the logs/XCom;
2. when every city fails, the fetch raises so the task is marked failed;
3. running the same batch twice leaves exactly one row per city and date,
   with the latest values, both across runs and for duplicates inside a batch.

Run everything with `pytest tests -q` from the repository root, with the same
environment the DAGs expect. The ETL unit tests alone also run in any
environment that has `pytest` and `requests` installed.

## What CI adds

The workflow runs three jobs in parallel. Ruff lints the dags and tests folders. A byte-compilation pass over the dags folder acts as a fast syntax gate, so an obvious typo fails in seconds rather than after a full Airflow install. The integrity tests then run against a constrained Airflow install.

The ordering matters: the cheap checks fail fast, so the slow job only runs on code that is at least syntactically sound.

## What is not covered

There are no tests that call the weather API or write to a real Postgres
instance. Tests that need a live API key and a live database are not tests a
contributor can run, and a tutorial repository is the wrong place to teach
people to depend on network access in CI.

If you want that coverage, mock the hook rather than the HTTP layer. The fake
hook in `test_weather_pipeline.py` is the pattern: assert that the task runs
the upsert with the rows you expect rather than asserting that Postgres
accepted them.

## Adding a test

Keep new tests import-safe and offline. If a test needs a connection, it belongs in a separate integration suite that is opt-in rather than part of the default run.
