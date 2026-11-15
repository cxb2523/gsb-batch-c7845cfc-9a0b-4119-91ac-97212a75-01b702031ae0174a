from __future__ import annotations

import logging
from collections.abc import Generator, Iterator, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from etl.config.settings import get_settings

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS: list[str] = [
    "VendorID",
    "tpep_pickup_datetime",
    "tpep_dropoff_datetime",
    "passenger_count",
    "trip_distance",
    "RatecodeID",
    "store_and_fwd_flag",
    "PULocationID",
    "DOLocationID",
    "payment_type",
    "fare_amount",
    "extra",
    "mta_tax",
    "tip_amount",
    "tolls_amount",
    "improvement_surcharge",
    "congestion_surcharge",
    "airport_fee",
    "total_amount",
]

COLUMN_RENAME_MAP: dict[str, str] = {
    "VendorID": "vendor_id",
    "tpep_pickup_datetime": "pickup_datetime",
    "tpep_dropoff_datetime": "dropoff_datetime",
    "RatecodeID": "rate_code_id",
    "PULocationID": "pickup_location_id",
    "DOLocationID": "dropoff_location_id",
}


class MissingRequiredColumnsError(ValueError):
    """
    Raised when a Parquet file does not contain every required column.

    Carries the offending file name and the exact list of missing column
    names so callers can log a precise, actionable failure reason instead
    of discovering the problem much later during normalisation or
    validation of individual rows.
    """

    def __init__(self, source_file: str, missing_columns: Sequence[str]) -> None:
        self.source_file = source_file
        self.missing_columns = list(missing_columns)
        columns = ", ".join(self.missing_columns)
        super().__init__(f"Parquet file '{source_file}' is missing required columns: {columns}")


class ParquetReader:
    """
    Reads NYC Yellow Taxi Parquet files in memory-efficient chunks.

    Uses PyArrow iter_batches() so the full file is never loaded into
    memory at once. Only the columns required by the domain pipeline
    are read -- PyArrow skips unneeded column groups at the I/O level,
    significantly reducing read time and memory pressure.

    Yields batches of raw dicts to the application layer. Column names
    are normalised to snake_case before yielding so the domain layer
    never sees raw TLC field names like "VendorID" or "PULocationID".

    Schema completeness is checked once, when the file is opened. The
    result is exposed through `missing_columns` (and returned by `open()`)
    so the application layer can reject an unusable file before any row
    enters the domain pipeline. `iter_batches()` also enforces this and
    raises `MissingRequiredColumnsError` rather than silently yielding
    rows with absent fields.

    Usage:
        reader = ParquetReader(path=Path("data/raw/yellow_tripdata_2024-01.parquet"))
        if reader.missing_columns:
            raise MissingRequiredColumnsError(reader.source_file, reader.missing_columns)
        for batch in reader.iter_batches():
            process(batch)
    """

    def __init__(
        self,
        path: Path | None = None,
        batch_size: int | None = None,
        source_file: str | None = None,
    ) -> None:
        settings = get_settings()
        self._path = path or settings.etl.parquet_file_path
        self._batch_size = batch_size or settings.etl.parquet_batch_size
        self._source_file = source_file or self._path.name
        self._parquet_file: Any | None = None
        self._missing_columns: list[str] | None = None
        self._available_columns: list[str] | None = None
        self._validate_path()

    def _validate_path(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"Parquet file not found: {self._path}")
        if not self._path.suffix == ".parquet":
            raise ValueError(f"Expected .parquet file, got: {self._path.suffix}")

    def _detect_missing_columns(self, schema: pa.Schema) -> list[str]:
        """Return required columns absent from the file schema, in required order."""
        file_columns = set(schema.names)
        return [col for col in REQUIRED_COLUMNS if col not in file_columns]

    def open(self) -> list[str]:
        """
        Open the Parquet file and inspect its schema.

        Idempotent -- the file is opened and inspected only once. Returns
        the list of required columns that are missing from the file; an
        empty list means the schema is complete. The same list is exposed
        as the `missing_columns` property.

        Raises:
            FileNotFoundError: if the Parquet file does not exist.
            pa.lib.ArrowInvalid: if the file is corrupt or not valid Parquet.
        """
        if self._parquet_file is not None:
            return list(self._missing_columns or [])

        parquet_file: Any = pq.ParquetFile(self._path)  # type: ignore[no-untyped-call]
        self._parquet_file = parquet_file

        missing = self._detect_missing_columns(parquet_file.schema_arrow)
        self._missing_columns = missing

        file_columns = set(parquet_file.schema_arrow.names)
        self._available_columns = [col for col in REQUIRED_COLUMNS if col in file_columns]

        if missing:
            logger.error(
                "Parquet file is missing required columns; file cannot be ingested",
                extra={
                    "file": self._source_file,
                    "path": str(self._path),
                    "missing_columns": missing,
                    "missing_column_count": len(missing),
                },
            )
        else:
            logger.debug(
                "Parquet schema complete",
                extra={
                    "file": self._source_file,
                    "columns": len(self._available_columns),
                },
            )

        return list(missing)

    @property
    def missing_columns(self) -> list[str]:
        """
        Required column names absent from this file (empty when complete).

        Accessing this property opens the file on first use, so callers can
        decide whether the file is ingestable before iterating batches.
        """
        if self._missing_columns is None:
            self.open()
        return list(self._missing_columns or [])

    @property
    def is_schema_complete(self) -> bool:
        """True when every required column is present in the file."""
        return not self.missing_columns

    def _available_required_columns(self) -> list[str]:
        """Required columns present in the file, preserving REQUIRED_COLUMNS order."""
        self.open()
        return list(self._available_columns or [])

    def _rename_columns(self, row: dict[str, object]) -> dict[str, object]:
        return {COLUMN_RENAME_MAP.get(k, k): v for k, v in row.items()}

    def _batch_to_dicts(self, batch: pa.RecordBatch) -> list[dict[str, object]]:
        """
        Convert a PyArrow RecordBatch to a list of plain Python dicts.

        pa.RecordBatch.to_pydict() returns {col: [values]} (columnar).
        We transpose to [{col: value}, ...] (row-oriented) because the
        domain pipeline operates record by record.
        """
        columnar = batch.to_pydict()
        num_rows = batch.num_rows
        rows = []
        for i in range(num_rows):
            row = {col: columnar[col][i] for col in columnar}
            rows.append(self._rename_columns(row))
        return rows

    def iter_batches(self) -> Generator[list[dict[str, object]], None, None]:
        """
        Yield successive batches of raw dicts from the Parquet file.

        Each yielded list contains at most batch_size dicts. The final
        batch may be smaller.

        Raises:
            FileNotFoundError: if the Parquet file does not exist.
            MissingRequiredColumnsError: if one or more required columns
                are absent. The error exposes `source_file` and
                `missing_columns`; no rows are yielded in this case.
            pa.lib.ArrowInvalid: if the file is corrupt or not valid Parquet.
        """
        missing = self.open()
        if missing:
            raise MissingRequiredColumnsError(self._source_file, missing)

        parquet_file: Any = self._parquet_file
        columns = self._available_required_columns()

        logger.info(
            "Starting Parquet read",
            extra={
                "file": self._source_file,
                "batch_size": self._batch_size,
                "columns": len(columns),
                "total_row_groups": parquet_file.metadata.num_row_groups,
            },
        )

        total_rows = 0
        batch_num = 0

        for record_batch in parquet_file.iter_batches(
            batch_size=self._batch_size,
            columns=columns,
        ):
            rows = self._batch_to_dicts(record_batch)
            total_rows += len(rows)
            batch_num += 1

            logger.debug(
                "Parquet batch ready",
                extra={
                    "batch_num": batch_num,
                    "batch_size": len(rows),
                    "total_rows_so_far": total_rows,
                    "source_file": self._source_file,
                },
            )

            yield rows

        logger.info(
            "Parquet read complete",
            extra={
                "file": self._source_file,
                "total_rows": total_rows,
                "total_batches": batch_num,
            },
        )

    def __iter__(self) -> Iterator[list[dict[str, object]]]:
        return self.iter_batches()

    @property
    def source_file(self) -> str:
        return self._source_file

    @property
    def path(self) -> Path:
        return self._path
