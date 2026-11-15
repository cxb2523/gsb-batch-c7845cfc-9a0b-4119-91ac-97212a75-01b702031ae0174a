from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pyarrow as pa
import pyarrow.parquet as pq

from etl.application.services.ingestion_service import (
    BatchFailure,
    IngestionService,
    IngestionSummary,
    RejectedFile,
)
from etl.domain.trip.events import InvalidTripDetected, ProcessingStage
from etl.domain.trip.models import Distance, Duration, Money, Payment, Trip
from etl.infrastructure.storage.parquet_reader import (
    REQUIRED_COLUMNS,
    MissingRequiredColumnsError,
    ParquetReader,
)
from etl.runtime.shutdown import ShutdownHandler

_PICKUP = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)
_DROPOFF = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)


def _make_trip(trip_id: str = "abc123") -> Trip:
    return Trip(
        trip_id=trip_id,
        vendor_id="Creative Mobile Technologies",
        pickup_datetime=_PICKUP,
        dropoff_datetime=_DROPOFF,
        passenger_count=2,
        distance=Distance.of(3.5),
        duration=Duration.between(_PICKUP, _DROPOFF),
        pickup_location_id=161,
        dropoff_location_id=236,
        payment=Payment(
            payment_type="Credit card",
            fare_amount=Money.of(10.0, "fare_amount"),
            extra=Money.zero(),
            mta_tax=Money.of(0.5, "mta_tax"),
            tip_amount=Money.of(2.0, "tip_amount"),
            tolls_amount=Money.zero(),
            improvement_surcharge=Money.of(0.3, "improvement_surcharge"),
            congestion_surcharge=Money.of(2.5, "congestion_surcharge"),
            airport_fee=Money.zero(),
            total_amount=Money.of(15.3, "total_amount"),
        ),
        rate_code="Standard",
        store_and_fwd_flag="No",
        batch_id="b1",
        source_file="test.parquet",
    )


def _make_invalid_event(trip_id: str = "bad") -> InvalidTripDetected:
    return InvalidTripDetected(
        stage=ProcessingStage.VALIDATION,
        error_message="Duration 0s is invalid",
        error_type="InvalidTripDurationError",
        original_record={"pickup_location_id": 161},
        batch_id="b1",
        source_file="test.parquet",
        trip_id=trip_id,
    )


def _make_reader(batches: list[list[dict]]) -> MagicMock:
    reader = MagicMock()
    reader.source_file = "test.parquet"
    reader.missing_columns = []
    reader.iter_batches.return_value = iter(batches)
    return reader


def _make_domain_service(
    valid_trips: list[Trip] | None = None,
    invalid_events: list[InvalidTripDetected] | None = None,
) -> MagicMock:
    svc = MagicMock()
    svc.process_batch.return_value = (valid_trips or [], invalid_events or [])
    return svc


def _make_service(
    batches: list[list[dict]] | None = None,
    valid_trips: list[Trip] | None = None,
    invalid_events: list[InvalidTripDetected] | None = None,
    shutdown_requested: bool = False,
    schema_valid: list[dict] | None = None,
    schema_invalid: list[tuple] | None = None,
) -> tuple[IngestionService, MagicMock, MagicMock, MagicMock]:
    reader = _make_reader(batches or [[{"row": 1}]])
    domain_service = _make_domain_service(valid_trips, invalid_events)
    publisher = MagicMock()
    dl_service = MagicMock()

    handler = MagicMock(spec=ShutdownHandler)
    handler.is_shutdown_requested = shutdown_requested

    validation_service = MagicMock()
    validation_service.validate_batch.return_value = (
        schema_valid if schema_valid is not None else [{"row": 1}],
        schema_invalid or [],
    )

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=publisher,
        dead_letter_service=dl_service,
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=validation_service,
    )
    return service, publisher, dl_service, domain_service


# IngestionSummary
def test_summary_reject_rate_zero_when_no_rows():
    s = IngestionSummary(source_file="test.parquet")
    assert s.reject_rate == 0.0


def test_summary_reject_rate_includes_schema_rejected():
    s = IngestionSummary(source_file="test.parquet")
    s.total_rows = 10
    s.total_invalid = 2
    s.total_schema_rejected = 3
    assert abs(s.reject_rate - 0.5) < 1e-9


def test_summary_reject_rate_all_rejected():
    s = IngestionSummary(source_file="test.parquet")
    s.total_rows = 5
    s.total_invalid = 5
    assert s.reject_rate == 1.0


def test_summary_complete_sets_finished_at():
    s = IngestionSummary(source_file="test.parquet")
    assert s.finished_at is None
    s.complete()
    assert s.finished_at is not None


def test_summary_as_dict_keys():
    s = IngestionSummary(source_file="test.parquet")
    s.complete()
    d = s.as_dict()
    assert "source_file" in d
    assert "total_batches" in d
    assert "total_valid" in d
    assert "total_invalid" in d
    assert "total_schema_rejected" in d
    assert "reject_rate" in d
    assert "finished_at" in d


# IngestionService.run: basic flow
def test_run_returns_summary():
    service, _, _, _ = _make_service()
    summary = service.run()
    assert isinstance(summary, IngestionSummary)


def test_run_sets_finished_at_on_summary():
    service, _, _, _ = _make_service()
    summary = service.run()
    assert summary.finished_at is not None


def test_run_calls_publisher_flush():
    service, publisher, _, _ = _make_service()
    service.run()
    publisher.flush.assert_called_once()


def test_run_counts_batches():
    service, _, _, _ = _make_service(batches=[[{"r": 1}], [{"r": 2}], [{"r": 3}]])
    summary = service.run()
    assert summary.total_batches == 3


def test_run_counts_total_rows():
    service, _, _, _ = _make_service(
        batches=[[{"r": 1}, {"r": 2}], [{"r": 3}]],
        schema_valid=[{"r": 1}, {"r": 2}],
    )
    summary = service.run()
    assert summary.total_rows == 3


# IngestionService.run: valid trips
def test_run_publishes_valid_trips():
    trips = [_make_trip("id-1"), _make_trip("id-2")]
    service, publisher, _, _ = _make_service(valid_trips=trips)
    service.run()
    publisher.publish_batch.assert_called_once()


def test_run_counts_valid_trips_in_summary():
    trips = [_make_trip("id-1"), _make_trip("id-2")]
    service, _, _, _ = _make_service(valid_trips=trips)
    summary = service.run()
    assert summary.total_valid == 2


def test_run_does_not_publish_when_no_valid_trips():
    service, publisher, _, _ = _make_service(valid_trips=[], invalid_events=[])
    service.run()
    publisher.publish_batch.assert_not_called()


# IngestionService.run: invalid records
def test_run_dead_letters_invalid_domain_records():
    events = [_make_invalid_event("bad-1"), _make_invalid_event("bad-2")]
    service, _, dl_service, _ = _make_service(invalid_events=events)
    service.run()
    dl_service.send_batch.assert_called_once()


def test_run_counts_invalid_in_summary():
    events = [_make_invalid_event()]
    service, _, _, _ = _make_service(invalid_events=events)
    summary = service.run()
    assert summary.total_invalid == 1


def test_run_does_not_dead_letter_when_no_invalid():
    trips = [_make_trip()]
    service, _, dl_service, _ = _make_service(valid_trips=trips, invalid_events=[])
    service.run()
    dl_service.send_batch.assert_not_called()


# IngestionService.run: schema validation
def test_run_dead_letters_schema_rejected_rows():
    bad_row = {"vendor_id": 1}
    service, _, dl_service, _ = _make_service(
        schema_valid=[],
        schema_invalid=[(bad_row, "missing pickup_datetime")],
    )
    service.run()
    dl_service.send.assert_called_once()


def test_run_counts_schema_rejected_in_summary():
    bad_row = {"vendor_id": 1}
    service, _, _, _ = _make_service(
        schema_valid=[],
        schema_invalid=[(bad_row, "missing required field")],
    )
    summary = service.run()
    assert summary.total_schema_rejected == 1


def test_run_schema_rejected_not_passed_to_domain_service():
    bad_row = {"vendor_id": 1}
    service, _, _, domain_service = _make_service(
        schema_valid=[],
        schema_invalid=[(bad_row, "error")],
    )
    service.run()
    domain_service.process_batch.assert_called_once()
    called_rows = domain_service.process_batch.call_args.args[0]
    assert called_rows == []


# IngestionService.run: shutdown
def test_run_stops_when_shutdown_requested_before_batch():
    reader = _make_reader([[{"r": 1}], [{"r": 2}], [{"r": 3}]])
    domain_service = _make_domain_service()
    publisher = MagicMock()
    dl_service = MagicMock()
    validation_service = MagicMock()
    validation_service.validate_batch.return_value = ([], [])

    handler = MagicMock(spec=ShutdownHandler)
    handler.is_shutdown_requested = True  # shutdown already set

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=publisher,
        dead_letter_service=dl_service,
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=validation_service,
    )
    summary = service.run()

    assert summary.total_batches == 0
    domain_service.process_batch.assert_not_called()


def test_run_flush_called_even_after_shutdown():
    handler = MagicMock(spec=ShutdownHandler)
    handler.is_shutdown_requested = True

    reader = _make_reader([[]])
    publisher = MagicMock()

    service = IngestionService(
        reader=reader,
        domain_service=_make_domain_service(),
        publisher=publisher,
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=MagicMock(validate_batch=MagicMock(return_value=([], []))),
    )
    service.run()
    publisher.flush.assert_called_once()


# IngestionService.run: multiple batches accumulate correctly
def test_run_accumulates_counts_across_batches():
    reader = _make_reader([[{"r": 1}, {"r": 2}], [{"r": 3}, {"r": 4}, {"r": 5}]])
    trips_b1 = [_make_trip("t1")]
    trips_b2 = [_make_trip("t2"), _make_trip("t3")]
    invalid_b1 = [_make_invalid_event("bad1")]

    domain_service = MagicMock()
    domain_service.process_batch.side_effect = [
        (trips_b1, invalid_b1),
        (trips_b2, []),
    ]

    validation_service = MagicMock()
    validation_service.validate_batch.side_effect = [
        ([{"r": 1}, {"r": 2}], []),
        ([{"r": 3}, {"r": 4}, {"r": 5}], []),
    ]

    handler = MagicMock(spec=ShutdownHandler)
    handler.is_shutdown_requested = False

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=MagicMock(),
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=validation_service,
    )
    summary = service.run()

    assert summary.total_batches == 2
    assert summary.total_rows == 5
    assert summary.total_valid == 3  # 1 + 2
    assert summary.total_invalid == 1


# IngestionService.run: trips_processed_total metric
@patch("etl.application.services.ingestion_service.trips_processed_total")
def test_run_increments_valid_metric_by_valid_trip_count(mock_metric):
    trips = [_make_trip("id-1"), _make_trip("id-2")]
    service, _, _, _ = _make_service(valid_trips=trips)
    service.run()
    mock_metric.labels.assert_any_call(status="valid")
    mock_metric.labels(status="valid").inc.assert_any_call(2)


@patch("etl.application.services.ingestion_service.trips_processed_total")
def test_run_increments_invalid_metric_for_domain_invalid_events(mock_metric):
    events = [_make_invalid_event("bad-1"), _make_invalid_event("bad-2")]
    service, _, _, _ = _make_service(invalid_events=events)
    service.run()
    mock_metric.labels.assert_any_call(status="invalid")
    mock_metric.labels(status="invalid").inc.assert_any_call(2)


@patch("etl.application.services.ingestion_service.trips_processed_total")
def test_run_increments_invalid_metric_for_schema_rejected_rows(mock_metric):
    bad_row = {"vendor_id": 1}
    service, _, _, _ = _make_service(
        schema_valid=[],
        schema_invalid=[(bad_row, "missing required field")],
    )
    service.run()
    mock_metric.labels.assert_any_call(status="invalid")
    mock_metric.labels(status="invalid").inc.assert_any_call(1)


@patch("etl.application.services.ingestion_service.trips_processed_total")
def test_run_does_not_increment_valid_metric_when_no_valid_trips(mock_metric):
    service, _, _, _ = _make_service(valid_trips=[], invalid_events=[])
    service.run()
    assert mock_metric.labels(status="valid").inc.call_count == 0


# IngestionService.run: files rejected for missing required columns
def _make_service_with_reader(
    reader: MagicMock,
    domain_service: MagicMock | None = None,
    validation_service: MagicMock | None = None,
) -> tuple[IngestionService, MagicMock, MagicMock, MagicMock]:
    publisher = MagicMock()
    dl_service = MagicMock()
    domain_service = domain_service or _make_domain_service()
    validation_service = validation_service or MagicMock(
        validate_batch=MagicMock(return_value=([], []))
    )
    handler = MagicMock(spec=ShutdownHandler)
    handler.is_shutdown_requested = False

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=publisher,
        dead_letter_service=dl_service,
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=validation_service,
    )
    return service, publisher, dl_service, domain_service


def test_run_rejects_file_with_missing_required_columns():
    reader = _make_reader([[{"r": 1}]])
    reader.missing_columns = ["airport_fee", "total_amount"]
    service, _, _, domain_service = _make_service_with_reader(reader)

    summary = service.run()

    assert summary.rejected_file_count == 1
    assert summary.rejected_files[0].source_file == "test.parquet"
    assert summary.rejected_files[0].missing_columns == ["airport_fee", "total_amount"]
    domain_service.process_batch.assert_not_called()
    reader.iter_batches.assert_not_called()


def test_run_rejected_file_reason_names_file_and_missing_columns():
    reader = _make_reader([[{"r": 1}]])
    reader.missing_columns = ["total_amount"]
    service, _, _, _ = _make_service_with_reader(reader)

    rejection = service.run().rejected_files[0]

    assert rejection.reason.startswith("file 'test.parquet'")
    assert "total_amount" in rejection.reason


def test_run_rejected_file_no_rows_or_batches_counted():
    reader = _make_reader([[{"r": 1}, {"r": 2}]])
    reader.missing_columns = ["total_amount"]
    service, _, _, _ = _make_service_with_reader(reader)

    summary = service.run()

    assert summary.total_batches == 0
    assert summary.total_rows == 0
    assert summary.successful_batches == 0
    assert summary.failed_batches == 0
    assert summary.reject_rate == 0.0


def test_run_rejected_file_still_flushes_publisher():
    reader = _make_reader([[{"r": 1}]])
    reader.missing_columns = ["total_amount"]
    service, publisher, _, _ = _make_service_with_reader(reader)

    service.run()

    publisher.flush.assert_called_once()


def test_run_rejected_file_still_completes_summary():
    reader = _make_reader([[{"r": 1}]])
    reader.missing_columns = ["total_amount"]
    service, _, _, _ = _make_service_with_reader(reader)

    summary = service.run()

    assert summary.finished_at is not None


def test_run_rejected_file_not_sent_to_validation_or_dead_letter():
    reader = _make_reader([[{"r": 1}]])
    reader.missing_columns = ["total_amount"]
    validation_service = MagicMock()
    service, _, dl_service, _ = _make_service_with_reader(
        reader, validation_service=validation_service
    )

    service.run()

    validation_service.validate_batch.assert_not_called()
    dl_service.send.assert_not_called()
    dl_service.send_batch.assert_not_called()


def test_run_records_rejection_when_reader_raises_on_iteration():
    reader = _make_reader([])

    def _raise():
        raise MissingRequiredColumnsError("test.parquet", ["airport_fee"])
        yield  # pragma: no cover - unreachable, marks this a generator

    reader.iter_batches.side_effect = _raise
    service, publisher, _, domain_service = _make_service_with_reader(reader)

    summary = service.run()

    assert summary.rejected_file_count == 1
    assert summary.rejected_files[0].missing_columns == ["airport_fee"]
    assert summary.total_batches == 0
    domain_service.process_batch.assert_not_called()
    publisher.flush.assert_called_once()


# IngestionService.run: batch failures are isolated
def test_run_continues_after_batch_failure():
    reader = _make_reader([[{"r": 1}], [{"r": 2}], [{"r": 3}]])
    domain_service = MagicMock()
    domain_service.process_batch.side_effect = [
        ([_make_trip("t1")], []),
        RuntimeError("boom in domain"),
        ([_make_trip("t3")], []),
    ]
    validation_service = MagicMock()
    validation_service.validate_batch.side_effect = [
        ([{"r": 1}], []),
        ([{"r": 2}], []),
        ([{"r": 3}], []),
    ]
    service, publisher, _, _ = _make_service_with_reader(
        reader, domain_service=domain_service, validation_service=validation_service
    )

    summary = service.run()

    assert domain_service.process_batch.call_count == 3
    assert summary.total_batches == 3
    assert summary.successful_batches == 2
    assert summary.failed_batches == 1
    assert summary.total_valid == 2
    assert publisher.publish_batch.call_count == 2


def test_run_records_batch_failure_details():
    reader = _make_reader([[{"r": 1}], [{"r": 2}]])
    domain_service = MagicMock()
    domain_service.process_batch.side_effect = [RuntimeError("kafka exploded"), ([], [])]
    service, _, _, _ = _make_service_with_reader(reader, domain_service=domain_service)

    summary = service.run()

    failure = summary.batch_failures[0]
    failing_batch_id = domain_service.process_batch.call_args_list[0].args[1]
    assert failure.batch_id == failing_batch_id
    assert failure.error_type == "RuntimeError"
    assert failure.error_message == "kafka exploded"


def test_run_records_failure_when_publish_raises():
    reader = _make_reader([[{"r": 1}], [{"r": 2}]])
    trips_b1 = [_make_trip("t1")]
    trips_b2 = [_make_trip("t2")]
    domain_service = _make_domain_service()
    domain_service.process_batch.side_effect = [(trips_b1, []), (trips_b2, [])]
    service, publisher, _, _ = _make_service_with_reader(reader, domain_service=domain_service)
    publisher.publish_batch.side_effect = [RuntimeError("broker down"), None]

    summary = service.run()

    assert summary.failed_batches == 1
    assert summary.successful_batches == 1
    assert summary.batch_failures[0].error_type == "RuntimeError"
    assert publisher.publish_batch.call_count == 2


def test_run_flushes_publisher_after_batch_failure():
    reader = _make_reader([[{"r": 1}]])
    domain_service = MagicMock()
    domain_service.process_batch.side_effect = RuntimeError("boom")
    service, publisher, _, _ = _make_service_with_reader(reader, domain_service=domain_service)

    service.run()

    publisher.flush.assert_called_once()


def test_run_all_batches_failing_returns_summary_not_exception():
    reader = _make_reader([[{"r": 1}], [{"r": 2}]])
    domain_service = MagicMock()
    domain_service.process_batch.side_effect = RuntimeError("always fails")
    service, _, _, _ = _make_service_with_reader(reader, domain_service=domain_service)

    summary = service.run()

    assert summary.failed_batches == 2
    assert summary.successful_batches == 0
    assert summary.finished_at is not None


def test_run_shutdown_still_stops_after_current_batch_with_failures():
    class _ShutdownHandler:
        def __init__(self) -> None:
            self.is_shutdown_requested = False

    class _Reader:
        def __init__(self) -> None:
            self.source_file = "test.parquet"
            self.missing_columns: list[str] = []

        def iter_batches(self):
            yield [{"r": 1}]
            yield [{"r": 2}]  # never processed

    handler = _ShutdownHandler()
    reader = _Reader()

    def _fail_first_batch(*_args, **_kwargs):
        handler.is_shutdown_requested = True  # signal arrives mid-batch
        raise RuntimeError("boom")

    domain_service = MagicMock()
    domain_service.process_batch.side_effect = _fail_first_batch
    publisher = MagicMock()
    validation_service = MagicMock(validate_batch=MagicMock(return_value=([], [])))

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=publisher,
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=handler,
        validation_service=validation_service,
    )

    summary = service.run()

    assert summary.total_batches == 1
    assert summary.failed_batches == 1
    assert domain_service.process_batch.call_count == 1
    publisher.flush.assert_called_once()


# IngestionSummary: aggregate counters across outcomes
def test_summary_distinguishes_successful_failed_and_rejected():
    reader = _make_reader([[{"r": 1}, {"r": 2}], [{"r": 3}]])
    domain_service = MagicMock()
    domain_service.process_batch.side_effect = [
        ([_make_trip("t1")], [_make_invalid_event("bad")]),
        RuntimeError("boom"),
    ]
    validation_service = MagicMock()
    validation_service.validate_batch.side_effect = [
        (
            [{"r": 1}, {"r": 2}],
            [
                (  # two schema-rejected rows
                    {"r": 0},
                    "missing pickup_datetime",
                )
            ],
        ),
        ([{"r": 3}], []),
    ]
    service, _, _, _ = _make_service_with_reader(
        reader, domain_service=domain_service, validation_service=validation_service
    )

    summary = service.run()

    assert summary.total_batches == 2
    assert summary.successful_batches == 1
    assert summary.failed_batches == 1
    assert summary.total_batches == (summary.successful_batches + summary.failed_batches)
    assert summary.rejected_file_count == 0
    assert summary.total_rows == 3
    assert summary.failed_rows == 1
    assert summary.total_valid == 1
    assert summary.total_invalid == 1
    assert summary.total_schema_rejected == 1
    assert abs(summary.reject_rate - (2 / 3)) < 1e-9


def test_summary_as_dict_contains_batch_and_file_outcome_fields():
    s = IngestionSummary(source_file="test.parquet")
    s.complete()

    d = s.as_dict()

    assert "successful_batches" in d
    assert "failed_batches" in d
    assert "failed_rows" in d
    assert "rejected_file_count" in d
    assert "rejected_files" in d
    assert "batch_failures" in d
    # Existing semantics preserved
    for key in (
        "total_batches",
        "total_rows",
        "total_valid",
        "total_invalid",
        "total_schema_rejected",
        "reject_rate",
    ):
        assert key in d


def test_summary_reject_rate_unaffected_by_failed_batches():
    s = IngestionSummary(source_file="test.parquet")
    s.total_rows = 10
    s.total_invalid = 2
    s.total_schema_rejected = 1
    s.failed_batches = 3

    assert abs(s.reject_rate - 0.3) < 1e-9


def test_failed_batch_does_not_inflate_valid_or_invalid_counts():
    """A batch that raises must not contribute deliveries it never made."""
    reader = _make_reader([[{"r": 1}]])
    domain_service = _make_domain_service(
        valid_trips=[_make_trip("t1")], invalid_events=[_make_invalid_event("bad")]
    )
    service, _, _, _ = _make_service_with_reader(reader, domain_service=domain_service)
    service._publisher.publish_batch.side_effect = RuntimeError("broker down")

    summary = service.run()

    assert summary.failed_batches == 1
    assert summary.successful_batches == 0
    assert summary.total_batches == 1
    assert summary.total_valid == 0
    assert summary.total_invalid == 0
    assert summary.total_rows == 1  # the rows were still read from the file
    assert summary.failed_rows == 1


# IngestionService + real ParquetReader: missing-column contract
def _write_partial_parquet(path: Path, omitted: list[str], rows: int = 2) -> Path:
    columns = [c for c in REQUIRED_COLUMNS if c not in omitted]
    table = pa.Table.from_pylist(
        [{col: 1 for col in columns} for _ in range(rows)],
        schema=pa.schema([(col, pa.int64()) for col in columns]),
    )
    pq.write_table(table, path)
    return path


def test_run_with_real_reader_rejects_file_missing_columns(tmp_path):
    path = _write_partial_parquet(tmp_path / "partial.parquet", ["airport_fee", "total_amount"])
    reader = ParquetReader(path=path)
    domain_service = MagicMock()

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=MagicMock(),
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=MagicMock(spec=ShutdownHandler, is_shutdown_requested=False),
    )

    summary = service.run()

    assert summary.source_file == "partial.parquet"
    assert summary.rejected_file_count == 1
    rejection = summary.rejected_files[0]
    assert rejection.source_file == "partial.parquet"
    assert rejection.missing_columns == ["airport_fee", "total_amount"]
    assert "partial.parquet" in rejection.reason
    assert "airport_fee" in rejection.reason
    domain_service.process_batch.assert_not_called()
    assert summary.total_batches == 0
    assert summary.total_rows == 0


def test_run_with_real_reader_flushes_on_rejected_file(tmp_path):
    path = _write_partial_parquet(tmp_path / "partial.parquet", ["total_amount"])
    reader = ParquetReader(path=path)
    publisher = MagicMock()

    service = IngestionService(
        reader=reader,
        domain_service=MagicMock(),
        publisher=publisher,
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=MagicMock(spec=ShutdownHandler, is_shutdown_requested=False),
    )

    service.run()

    publisher.flush.assert_called_once()


def test_run_with_real_reader_processes_complete_file(tmp_path):
    path = _write_partial_parquet(tmp_path / "complete.parquet", [], rows=3)
    reader = ParquetReader(path=path, batch_size=2)
    domain_service = MagicMock()
    domain_service.process_batch.return_value = ([], [])
    validation_service = MagicMock(validate_batch=MagicMock(return_value=([], [])))

    service = IngestionService(
        reader=reader,
        domain_service=domain_service,
        publisher=MagicMock(),
        dead_letter_service=MagicMock(),
        topic="nyc-taxi-trips",
        shutdown_handler=MagicMock(spec=ShutdownHandler, is_shutdown_requested=False),
        validation_service=validation_service,
    )

    summary = service.run()

    assert summary.rejected_file_count == 0
    assert summary.total_batches >= 1
    assert summary.successful_batches == summary.total_batches
    assert summary.failed_batches == 0
    assert summary.total_rows == 3


# Outcome records
def test_rejected_file_as_dict_includes_file_and_columns():
    r = RejectedFile(
        source_file="x.parquet",
        missing_columns=["a", "b"],
        reason="file 'x.parquet' is missing required columns: a, b",
    )
    d = r.as_dict()
    assert d["source_file"] == "x.parquet"
    assert d["missing_columns"] == ["a", "b"]
    assert "a, b" in str(d["reason"])


def test_batch_failure_as_dict_includes_id_type_and_message():
    f = BatchFailure(batch_id="b-1", error_type="RuntimeError", error_message="boom")
    assert f.as_dict() == {
        "batch_id": "b-1",
        "error_type": "RuntimeError",
        "error_message": "boom",
    }
