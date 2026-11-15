from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from etl.infrastructure.storage.parquet_reader import (
    COLUMN_RENAME_MAP,
    REQUIRED_COLUMNS,
    MissingRequiredColumnsError,
    ParquetReader,
)


def _write_parquet(path: Path, columns: list[str], rows: list[dict]) -> Path:
    table = pa.Table.from_pylist(rows, schema=pa.schema([(col, pa.int64()) for col in columns]))
    pq.write_table(table, path)
    return path


def _complete_columns() -> list[str]:
    return list(REQUIRED_COLUMNS)


def test_complete_file_reports_no_missing_columns(tmp_path):
    path = _write_parquet(
        tmp_path / "complete.parquet",
        _complete_columns(),
        [{col: 1 for col in _complete_columns()}],
    )
    reader = ParquetReader(path=path)
    assert reader.missing_columns == []
    assert reader.is_schema_complete is True


def test_missing_columns_property_lists_absent_required_columns(tmp_path):
    columns = [c for c in REQUIRED_COLUMNS if c not in {"airport_fee", "total_amount"}]
    path = _write_parquet(
        tmp_path / "partial.parquet",
        columns,
        [{col: 1 for col in columns}],
    )
    reader = ParquetReader(path=path)

    assert reader.missing_columns == ["airport_fee", "total_amount"]
    assert reader.is_schema_complete is False


def test_missing_columns_property_preserves_required_column_order(tmp_path):
    present = ["VendorID", "tpep_pickup_datetime"]
    path = _write_parquet(
        tmp_path / "partial.parquet",
        present,
        [{col: 1 for col in present}],
    )
    reader = ParquetReader(path=path)
    assert reader.missing_columns == [col for col in REQUIRED_COLUMNS if col not in present]


def test_open_returns_missing_columns(tmp_path):
    present = ["VendorID"]
    path = _write_parquet(tmp_path / "partial.parquet", present, [{"VendorID": 1}])
    reader = ParquetReader(path=path)
    missing = reader.open()
    assert "tpep_pickup_datetime" in missing
    assert len(missing) == len(REQUIRED_COLUMNS) - 1


def test_iter_batches_raises_when_required_columns_missing(tmp_path):
    present = ["VendorID", "tpep_pickup_datetime"]
    path = _write_parquet(
        tmp_path / "partial.parquet",
        present,
        [{"VendorID": 1, "tpep_pickup_datetime": 1}],
    )
    reader = ParquetReader(path=path, source_file="partial.parquet")

    with pytest.raises(MissingRequiredColumnsError) as exc_info:
        list(reader.iter_batches())

    exc = exc_info.value
    assert exc.source_file == "partial.parquet"
    assert "tpep_dropoff_datetime" in exc.missing_columns
    assert "partial.parquet" in str(exc)
    assert "tpep_dropoff_datetime" in str(exc)


def test_iter_batches_yields_no_rows_when_columns_missing(tmp_path):
    present = ["VendorID"]
    path = _write_parquet(tmp_path / "partial.parquet", present, [{"VendorID": 1}])
    reader = ParquetReader(path=path)

    yielded = []
    with pytest.raises(MissingRequiredColumnsError):
        for batch in reader.iter_batches():
            yielded.extend(batch)

    assert yielded == []


def test_complete_file_iterates_rows_with_renamed_columns(tmp_path):
    columns = _complete_columns()
    row = {col: 1 for col in columns}
    row["VendorID"] = 7
    path = _write_parquet(tmp_path / "complete.parquet", columns, [row])
    reader = ParquetReader(path=path, batch_size=10)

    batches = list(reader.iter_batches())
    assert len(batches) == 1
    assert len(batches[0]) == 1

    renamed = batches[0][0]
    for original, normalised in COLUMN_RENAME_MAP.items():
        assert normalised in renamed
        assert original not in renamed


def test_parquet_reader_still_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        ParquetReader(path=tmp_path / "missing.parquet")
