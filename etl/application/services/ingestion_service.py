from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from etl.application import EventPublisher
from etl.application.services.validation_service import ValidationService
from etl.domain.dead_letter.models import DeadLetterRecord, DeadLetterStage
from etl.domain.dead_letter.services import DeadLetterService
from etl.domain.trip.services import TripDomainService
from etl.infrastructure.monitoring.metrics import trips_processed_total
from etl.infrastructure.storage.parquet_reader import (
    MissingRequiredColumnsError,
    ParquetReader,
)
from etl.observability.correlation import BatchCorrelationContext
from etl.runtime.shutdown import ShutdownHandler

logger = logging.getLogger(__name__)


@dataclass
class BatchFailure:
    """
    A batch that could not be processed because an exception escaped the
    domain pipeline or the publish/dead-letter steps.

    batch_id, error_type and error_message are retained so the run summary
    makes the failure diagnosable without trawling logs.
    """

    batch_id: str
    error_type: str
    error_message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "batch_id": self.batch_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass
class RejectedFile:
    """
    A source file that was refused before processing because its schema is
    incomplete (one or more required columns are absent).

    Keeping the missing column names next to the file name means the failure
    reason is explicit and actionable instead of surfacing later as opaque
    row-level normalisation or validation errors.
    """

    source_file: str
    missing_columns: list[str]
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "missing_columns": list(self.missing_columns),
            "reason": self.reason,
        }


@dataclass
class IngestionSummary:
    """Aggregated stats for a completed ingestion run."""

    source_file: str
    total_batches: int = 0
    total_rows: int = 0
    total_valid: int = 0
    total_invalid: int = 0
    total_schema_rejected: int = 0
    successful_batches: int = 0
    failed_batches: int = 0
    failed_rows: int = 0
    batch_failures: list[BatchFailure] = field(default_factory=list)
    rejected_files: list[RejectedFile] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def reject_rate(self) -> float:
        if self.total_rows == 0:
            return 0.0
        return (self.total_invalid + self.total_schema_rejected) / self.total_rows

    @property
    def rejected_file_count(self) -> int:
        """Number of source files refused because required columns were missing."""
        return len(self.rejected_files)

    def complete(self) -> None:
        self.finished_at = datetime.now(UTC)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_file": self.source_file,
            "total_batches": self.total_batches,
            "successful_batches": self.successful_batches,
            "failed_batches": self.failed_batches,
            "total_rows": self.total_rows,
            "failed_rows": self.failed_rows,
            "total_valid": self.total_valid,
            "total_invalid": self.total_invalid,
            "total_schema_rejected": self.total_schema_rejected,
            "rejected_file_count": self.rejected_file_count,
            "rejected_files": [f.as_dict() for f in self.rejected_files],
            "batch_failures": [f.as_dict() for f in self.batch_failures],
            "reject_rate": round(self.reject_rate, 4),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class IngestionService:
    """
    Coordinates the full producer pipeline loop.

    Sequence per Parquet batch:
      1. Schema validate (Pydantic) -- reject completely malformed rows early
      2. Domain process (normalise + parse + enrich + validate + deduplicate)
      3. Publish valid trips to Kafka
      4. Dead-letter invalid records to DLQ topic and data/rejected/

    Files whose Parquet schema is missing required columns are refused up
    front: a RejectedFile (file name + missing columns) is recorded and no
    row from that file enters the domain pipeline. A failure in a single
    batch (domain processing, publishing or dead-lettering) is caught,
    recorded on the summary with its batch_id and exception details, and
    the run continues with the next batch.

    Respects the ShutdownHandler flag -- finishes the current batch and
    then exits cleanly rather than stopping mid-batch.

    Usage:
        service = IngestionService(reader, domain_service, publisher,
                                   dead_letter_service, topic, shutdown_handler)
        summary = service.run()
    """

    def __init__(
        self,
        reader: ParquetReader,
        domain_service: TripDomainService,
        publisher: EventPublisher,
        dead_letter_service: DeadLetterService,
        topic: str,
        shutdown_handler: ShutdownHandler | None = None,
        validation_service: ValidationService | None = None,
    ) -> None:
        self._reader = reader
        self._domain_service = domain_service
        self._publisher = publisher
        self._dead_letter_service = dead_letter_service
        self._topic = topic
        self._shutdown_handler = shutdown_handler or ShutdownHandler()
        self._validation_service = validation_service or ValidationService()

    def run(self) -> IngestionSummary:
        """
        Iterate all Parquet batches and run the full ingestion pipeline.

        Returns IngestionSummary with counts for the completed run.
        Exits early (but cleanly) if shutdown is requested mid-file, and
        returns a summary recording the rejection if the source file is
        missing required columns.

        The publisher is flushed on every exit path: normal completion,
        early shutdown, rejected file, and batch failures.
        """
        summary = IngestionSummary(source_file=self._reader.source_file)

        logger.info(
            "Ingestion started",
            extra={"source_file": self._reader.source_file, "topic": self._topic},
        )

        try:
            missing_columns = self._missing_columns()
            if missing_columns:
                self._reject_file(summary, self._reader.source_file, missing_columns)
                summary.complete()
                logger.error("Ingestion rejected", extra=summary.as_dict())
                return summary

            try:
                for raw_batch in self._reader.iter_batches():
                    if self._shutdown_handler.is_shutdown_requested:
                        logger.info("Shutdown requested -- stopping after current batch")
                        break

                    with BatchCorrelationContext() as ctx:
                        self._process_batch_safely(raw_batch, ctx.correlation_id, summary)

            except MissingRequiredColumnsError as exc:
                # Defensive: a reader that only surfaces the problem on
                # iteration still produces an explicit rejection rather
                # than a half-ingested file.
                self._reject_file(summary, exc.source_file, exc.missing_columns)

            summary.complete()

            logger.info("Ingestion complete", extra=summary.as_dict())
            return summary

        finally:
            self._publisher.flush()

    def _missing_columns(self) -> list[str]:
        """
        Return the reader's missing required columns, if it exposes them.

        Readers that predate schema inspection (or test doubles without the
        property) simply report no missing columns; in that case
        `iter_batches()` raising MissingRequiredColumnsError is still
        handled by `run()`.
        """
        value = getattr(self._reader, "missing_columns", None)
        if isinstance(value, (list, tuple)):
            return [str(col) for col in value]
        return []

    def _reject_file(
        self,
        summary: IngestionSummary,
        source_file: str,
        missing_columns: list[str],
    ) -> None:
        reason = (
            f"file '{source_file}' is missing required columns: " + ", ".join(missing_columns)
            if missing_columns
            else f"file '{source_file}' is missing required columns"
        )
        summary.rejected_files.append(
            RejectedFile(
                source_file=source_file,
                missing_columns=list(missing_columns),
                reason=reason,
            )
        )
        logger.error(
            "Source file rejected: cannot ingest without required columns",
            extra={
                "source_file": source_file,
                "missing_columns": list(missing_columns),
                "missing_column_count": len(missing_columns),
                "reason": reason,
            },
        )

    def _process_batch_safely(
        self,
        raw_batch: list[dict[str, Any]],
        batch_id: str,
        summary: IngestionSummary,
    ) -> None:
        """
        Process one batch, recording -- rather than propagating -- failures.

        A failing batch is counted in `failed_batches` and described in
        `batch_failures`; subsequent batches are still processed. Its rows
        are additionally counted in `failed_rows`; `total_rows` keeps its
        original meaning of "rows read from the source file", so
        `reject_rate` remains a ratio over read rows.
        """
        summary.total_batches += 1
        summary.total_rows += len(raw_batch)
        try:
            self._process_batch(raw_batch, batch_id, summary)
        except Exception as exc:
            summary.failed_batches += 1
            summary.failed_rows += len(raw_batch)
            summary.batch_failures.append(
                BatchFailure(
                    batch_id=batch_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )
            logger.error(
                "Batch processing failed; continuing with next batch",
                extra={
                    "batch_id": batch_id,
                    "source_file": self._reader.source_file,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "raw_rows": len(raw_batch),
                },
                exc_info=True,
            )
        else:
            summary.successful_batches += 1

    def _process_batch(
        self,
        raw_batch: list[dict[str, Any]],
        batch_id: str,
        summary: IngestionSummary,
    ) -> None:
        # Step 1: application-level schema validation
        schema_valid, schema_invalid = self._validation_service.validate_batch(raw_batch)

        for row, error_msg in schema_invalid:
            record = DeadLetterRecord(
                original_record=row,
                error_message=error_msg,
                error_type="SchemaValidationError",
                stage=DeadLetterStage.VALIDATION,
                batch_id=batch_id,
                source_file=self._reader.source_file,
            )
            self._dead_letter_service.send(record)

        # Step 2: domain pipeline (normalise, enrich, validate, deduplicate)
        valid_trips, invalid_events = self._domain_service.process_batch(
            schema_valid, batch_id, self._reader.source_file
        )

        # Step 3: publish valid trips to Kafka
        if valid_trips:
            messages = [t.to_dict() for t in valid_trips]
            self._publisher.publish_batch(self._topic, messages)

        # Step 4: dead-letter invalid domain records
        if invalid_events:
            dl_records = [DeadLetterRecord.from_invalid_event(event) for event in invalid_events]
            self._dead_letter_service.send_batch(dl_records)

        # Outcome counters are committed only once every step succeeded, so a
        # failed batch never inflates the valid/invalid/schema-rejected totals
        # with work that was not actually delivered.
        summary.total_schema_rejected += len(schema_invalid)
        summary.total_valid += len(valid_trips)
        summary.total_invalid += len(invalid_events)
        if schema_invalid:
            trips_processed_total.labels(status="invalid").inc(len(schema_invalid))
        if valid_trips:
            trips_processed_total.labels(status="valid").inc(len(valid_trips))
        if invalid_events:
            trips_processed_total.labels(status="invalid").inc(len(invalid_events))

        logger.info(
            "Batch ingested",
            extra={
                "batch_id": batch_id,
                "raw": len(raw_batch),
                "schema_rejected": len(schema_invalid),
                "valid": len(valid_trips),
                "invalid": len(invalid_events),
            },
        )
