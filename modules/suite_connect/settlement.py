"""Per-subquery settlement for the NetSuite extraction group.

Every NetSuite subquery is settled on its own so that one empty result set or
one failed S3 export cannot hide the outcome of the other subqueries in the
same extraction group.  A subquery ends in exactly one of three states:

``success``   records were extracted and exported to the S3 landing bucket
``no_data``   NetSuite returned an empty list; the export was skipped on purpose
``failed``    extraction or the S3 export raised an error that was recorded

The per-subquery record is logged and pushed to XCom by
:class:`custom_operators.NetSuiteToS3Operator`.  Once every subquery has
finished, :class:`custom_operators.NetSuiteExtractionSettlementOperator`
collects those records, reports the three categories, and fails the enclosing
task group when any subquery failed (or never reported a status).  That keeps
an incomplete extraction from being mistaken for a complete backup while still
letting the healthy subqueries finish their own exports.

The decision logic lives here as plain functions so it can be tested without an
Airflow installation.
"""

SUCCESS = 'success'
NO_DATA = 'no_data'
FAILED = 'failed'

VALID_STATUSES = (SUCCESS, NO_DATA, FAILED)

STATUS_LABELS = {
    SUCCESS: 'SUCCESS',
    NO_DATA: 'NO DATA',
    FAILED: 'FAILED',
}

#: XCom key used to hand a single subquery's settlement to the group settlement.
STATUS_XCOM_KEY = 'netsuite_subquery_status'

#: XCom key used by the group settlement to expose its three-way summary.
SUMMARY_XCOM_KEY = 'netsuite_extraction_settlement'


def build_status(
    task_id,
    search_type,
    search_id,
    filename,
    bucket_name,
    status,
    records=None,
    error=None,
):
    """Build the settlement record for one subquery.

    The record stays JSON-serialisable so it can be pushed to XCom without a
    custom serializer.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f'Unknown subquery status: {status!r}')

    return {
        'task_id': task_id,
        'search_type': search_type,
        'search_id': search_id,
        'filename': filename,
        'bucket_name': bucket_name,
        'status': status,
        'status_label': STATUS_LABELS[status],
        'records': records,
        'error': error,
    }


def settle_subquery(extract, export, field_values, logger=None):
    """Extract and export one subquery, returning ``(record, error)``.

    ``extract`` is a callable that returns the NetSuite result list.
    ``export`` is a callable that takes the non-empty result list and writes it
    to S3.  Empty results are recorded as ``no_data`` and the export is skipped
    *before* it can read a header row from an empty list.  An error in either
    callable is recorded as ``failed`` together with its reason and returned as
    the second element, so the caller can finish the other subqueries before
    the group decides whether the extraction is complete.
    """
    def record(status, records=None, error=None, cause=None):
        settled = build_status(
            status=status,
            records=records,
            error=error,
            **field_values
        )
        if logger is not None:
            log_settlement(logger, settled)
        return settled, cause

    try:
        results = extract()
    except Exception as err:
        return record(FAILED, error=describe_error(err), cause=err)

    # SuiteQuery swallows request errors and returns None, so None means the
    # extraction did not complete rather than "the search has no rows".
    if results is None:
        error = (
            'extraction returned None; the NetSuite query did not return '
            'a valid result set'
        )
        return record(FAILED, error=error, cause=ValueError(error))

    if not results:
        return record(NO_DATA, records=0)

    try:
        export(results)
    except Exception as err:
        return record(FAILED, error=describe_error(err), cause=err)

    return record(SUCCESS, records=len(results))


def describe_error(err):
    """Render an exception as ``TypeName: message`` for the settlement record."""
    message = str(err).strip()
    return f'{type(err).__name__}: {message}' if message else type(err).__name__


def log_settlement(logger, status):
    """Log one subquery settlement with its category and reason."""
    label = status['status_label']
    location = f"{status['bucket_name']}/{status['filename']}.csv"

    if status['status'] == SUCCESS:
        logger.info(
            f"{label}: search_id {status['search_id']} exported "
            f"{status['records']} records to {location}"
        )
    elif status['status'] == NO_DATA:
        logger.info(
            f"{label}: search_id {status['search_id']} returned an empty result; "
            f"skipping S3 export for {status['filename']}.csv"
        )
    else:
        logger.error(
            f"{label}: search_id {status['search_id']} could not be exported to "
            f"{location}: {status['error']}"
        )


def get_status_name(status, index=None, task_ids=None):
    """Return a readable identifier for an individual subquery status."""
    if isinstance(status, dict):
        name = status.get('search_id') or status.get('task_id')
        if name:
            return name

    if task_ids and index is not None and index < len(task_ids):
        return task_ids[index]

    return f'<subquery {index}>'


def summarize_subquery_statuses(statuses, task_ids=None):
    """Group subquery statuses into the three settlement categories.

    ``statuses`` is the list of records pushed by each
    :class:`custom_operators.NetSuiteToS3Operator` (``None`` when a subquery
    never reported one, for example because it crashed before settling).
    ``task_ids`` labels entries whose status is missing.
    """
    statuses = list(statuses or [])
    task_ids = list(task_ids or [])

    summary = {
        SUCCESS: [],
        NO_DATA: [],
        FAILED: [],
        'missing': [],
        'records': {SUCCESS: 0, NO_DATA: 0, FAILED: 0},
        'errors': {},
        'counts': {SUCCESS: 0, NO_DATA: 0, FAILED: 0, 'missing': 0},
    }

    for index, status in enumerate(statuses):
        name = get_status_name(status, index=index, task_ids=task_ids)
        category = status.get('status') if isinstance(status, dict) else None

        if category in VALID_STATUSES:
            summary[category].append(name)
            summary['counts'][category] += 1
            if category == FAILED:
                summary['errors'][name] = status.get('error') or 'unknown error'
            else:
                summary['records'][category] += int(status.get('records') or 0)
        else:
            summary['missing'].append(name)
            summary['counts']['missing'] += 1
            summary['errors'][name] = 'no settlement status was reported'

    return summary


def has_failures(summary):
    """A group is incomplete when a subquery failed or never reported a status."""
    counts = summary['counts']
    return bool(counts[FAILED] or counts['missing'])


def format_summary(summary):
    """Render the three-way settlement result as a readable log/exception message."""
    counts = summary['counts']
    lines = [
        'NetSuite subquery settlement: '
        f"{counts[SUCCESS]} succeeded, "
        f"{counts[NO_DATA]} with no data, "
        f"{counts[FAILED]} failed, "
        f"{counts['missing']} missing status."
    ]

    if summary[SUCCESS]:
        lines.append(f"SUCCESS: {', '.join(sorted(summary[SUCCESS]))}")

    if summary[NO_DATA]:
        lines.append(f"NO DATA: {', '.join(sorted(summary[NO_DATA]))}")

    for category, heading in ((FAILED, 'FAILED'), ('missing', 'MISSING STATUS')):
        names = summary[category]
        if not names:
            continue
        details = '; '.join(
            f"{name}: {summary['errors'].get(name, 'unknown error')}"
            for name in sorted(names)
        )
        lines.append(f'{heading}: {details}')

    return '\n'.join(lines)
