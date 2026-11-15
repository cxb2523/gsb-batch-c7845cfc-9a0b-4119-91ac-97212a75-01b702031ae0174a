"""
Reddit-specific JSON to CSV parsing.

This module contains Reddit-specific parsing logic including:
- Waterfall algorithm for deletion/removal detection
- Base-36 to base-10 ID conversion
- Old/new Reddit data format compatibility
- Mandatory fields (dataset, id; retrieved_utc is populated but kept nullable
  so pre-2010 Arctic Shift dumps without retrieval timestamps can ingest)
- Per-file settlement records (ParseSettlement) so every input file gets an
  independently verifiable result, including bad-line counts and whether the
  retrieved_on / retrieved_2nd_on timestamp fallbacks were used
"""

import os
import logging
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, List, Optional, Tuple

from ...core.config import ConfigurationError
from ...core.parser import (
    escape_string,
    escape_string_parquet,
    quote_field,
    get_nested_data,
    enforce_data_type,
    BatchedParquetWriter,
    ParseSettlement,
    ParseFailureError,
    iter_ndjson_records,
    write_settlement,
)


# Reddit-specific mandatory fields that are always included (not in YAML config).
# `retrieved_utc` is intentionally NOT here even though the parser populates it
# via fallback logic in transform_json — listing it would force NOT NULL on the
# DB column (via mandatory_fields → ingest.py NOT NULL rule), which breaks
# pre-2010 Arctic Shift dumps that genuinely lack any retrieval timestamp.
# It must stay in YAML `fields:` so the column is nullable.
MANDATORY_FIELDS = ['id']
MANDATORY_FIELD_TYPES = {
    'dataset': ['char', 7],
    'id': ['varchar', 7],
    'retrieved_utc': 'integer'
}


def base36_to_int(value: str) -> Optional[int]:
    """
    Convert a base 36 string to base 10 integer.

    Reddit IDs are base 36 (digits 0-9 and letters a-z).

    Args:
        value: Base 36 string (e.g., 'abc123')

    Returns:
        Base 10 integer, or None if conversion fails
    """
    if not value:
        return None
    try:
        return int(value, 36)
    except (ValueError, TypeError):
        return None


def determine_removal_status(data: Dict) -> Tuple[bool, str]:
    """
    Determine if content is deleted/removed and the removal type.

    Uses a waterfall algorithm (first match wins) checking sources in order
    of reliability. Outputs canonical Arctic Shift removal_type values.

    Priority order:
        1. _meta.removal_type (Arctic Shift 2023-11+, ground truth)
        2. _meta.was_deleted_later (marks deleted, continues to find type)
        3. removed_by_category (gold standard, 2018+)
        4. spam boolean flag (2020+ API)
        5. removed boolean flag (2020+ API)
        6. banned_by field (legacy, 2008-2018)
        7. Text content markers ([deleted], [removed])
        8. author == '[deleted]'

    Returns:
        Tuple of (is_deleted: bool, removal_type: str)

    Canonical removal_type values (from Arctic Shift _meta):
        - 'deleted' - User deleted their own content
        - 'author' - Author deleted (synonym for 'deleted')
        - 'moderator' - Removed by subreddit moderator
        - 'reddit' - Removed by Reddit admin/spam filter
        - 'automod_filtered' - Removed by AutoModerator
        - 'content_takedown' - Legal/DMCA takedown
        - 'copyright_takedown' - Copyright-specific takedown
        - 'community_ops' - Reddit Community Operations
        - '' - Not removed
    """
    meta = data.get('_meta', {})

    # Track if content was deleted (may be set early, type determined later)
    was_deleted_later = False

    # Priority 1: _meta.removal_type (Arctic Shift 2023-11+, ground truth)
    # Pass through as-is since it already uses canonical values
    if meta:
        meta_removal_type = meta.get('removal_type', '')
        if meta_removal_type:
            return True, meta_removal_type

        # Priority 2: _meta.was_deleted_later (mark deleted, continue to find type)
        if meta.get('was_deleted_later'):
            was_deleted_later = True

    # Priority 3: removed_by_category (gold standard, 2018+)
    removed_by_category = data.get('removed_by_category')
    if removed_by_category:
        category_lower = removed_by_category.lower()

        # Map to canonical values
        if category_lower in ('deleted', 'author'):
            return True, 'deleted'
        elif category_lower == 'moderator':
            return True, 'moderator'
        elif category_lower == 'reddit':
            return True, 'reddit'
        elif category_lower in ('anti_evil_ops', 'admin'):
            return True, 'reddit'
        elif category_lower == 'automod_filtered':
            return True, 'automod_filtered'
        elif category_lower == 'content_takedown':
            return True, 'content_takedown'
        elif category_lower == 'copyright_takedown':
            return True, 'copyright_takedown'
        elif category_lower == 'community_ops':
            return True, 'community_ops'
        else:
            # Unknown category, default to moderator
            return True, 'moderator'

    # Priority 4: spam boolean flag (2020+ API)
    if data.get('spam') is True:
        return True, 'reddit'

    # Priority 5: removed boolean flag (2020+ API)
    if data.get('removed') is True:
        return True, 'moderator'

    # Priority 6: banned_by field (legacy, 2008-2018)
    banned_by = data.get('banned_by')
    if banned_by is not None and banned_by != '' and banned_by is not False:
        # True (boolean) = Reddit spam filter (shadowban)
        if banned_by is True:
            return True, 'reddit'

        banned_by_str = str(banned_by).lower()
        if banned_by_str == 'true':
            return True, 'reddit'
        elif banned_by_str == 'automoderator':
            return True, 'automod_filtered'
        else:
            # Specific moderator username or other string
            return True, 'moderator'

    # Priority 7: Text content markers (body for comments, selftext for submissions)
    body = data.get('body', '')
    selftext = data.get('selftext', '')

    if body == '[removed]' or selftext == '[removed]':
        return True, 'moderator'

    if body == '[deleted]' or selftext == '[deleted]':
        return True, 'deleted'

    # Priority 8: author == '[deleted]'
    author = data.get('author', '')
    if author == '[deleted]':
        return True, 'deleted'

    # If _meta.was_deleted_later was set but we couldn't determine type
    if was_deleted_later:
        return True, 'deleted'

    # Not removed
    return False, ''


def get_all_columns(data_type: str, fields_to_extract: List[str]) -> List[str]:
    """
    Get the full column list for CSV header.

    Column order: [dataset, id, retrieved_utc, ...fields from YAML...]
    """
    return ['dataset'] + MANDATORY_FIELDS + fields_to_extract


def transform_json(
    data: Dict,
    dataset: str,
    data_type_config: Dict,
    fields_to_extract: List[str],
    file_format: str = 'csv',
    fallback_tracker=None,
) -> List:
    """
    Transform Reddit JSON data into a list of extracted values.

    CSV column order: [dataset, id, retrieved_utc, ...yaml fields...]

    Handles both old and new Reddit data formats:
    - Uses retrieved_on as retrieved_utc if retrieved_utc is not present (old format)
    - For new format (2023-11+), uses _meta.retrieved_2nd_on as retrieved_utc when available
    - Detects deletions/removals from multiple fields across different Reddit API eras

    Args:
        fallback_tracker: Optional object exposing
            ``record_timestamp_fallback('retrieved_on' | 'retrieved_2nd_on')``
            (e.g. a core.parser.ParseSettlement). When given, every use of a
            retrieval-timestamp fallback is recorded per row so the per-file
            settlement can report it. Output values are identical with or
            without a tracker.
    """
    meta = data.get('_meta', {})

    # Handle retrieved_utc field (mandatory, with fallback for old format).
    # Fallback order mirrors the historical behavior exactly:
    #   retrieved_utc -> _meta.retrieved_2nd_on (new format) -> retrieved_on (old)
    raw_retrieved_utc = data.get('retrieved_utc')
    data['retrieved_utc'] = raw_retrieved_utc if raw_retrieved_utc is not None \
        else data.get('retrieved_on', '')

    # The legacy fallback actually supplied a value only when retrieved_utc
    # was absent/null AND retrieved_on exists (a present-but-null retrieved_utc
    # with no retrieved_on still yields '' as before, just not a "fallback").
    if (raw_retrieved_utc is None and 'retrieved_on' in data
            and fallback_tracker is not None):
        # Legacy dump without retrieved_utc: retrieved_on backfilled it.
        fallback_tracker.record_timestamp_fallback('retrieved_on')

    # For new format (2023-11+), use second retrieval time if available
    if meta and meta.get('retrieved_2nd_on'):
        data['retrieved_utc'] = meta['retrieved_2nd_on']
        if fallback_tracker is not None:
            # New-format second retrieval always wins when present.
            fallback_tracker.record_timestamp_fallback('retrieved_2nd_on')

    # Determine deletion status and removal type
    is_deleted, removal_type = determine_removal_status(data)

    # Add deletion status to data
    data['is_deleted'] = is_deleted
    data['removal_type'] = removal_type

    # Compute id10 only if requested in field list (base 36 id to base 10)
    if 'id10' in fields_to_extract:
        data['id10'] = base36_to_int(data.get('id', ''))

    # Normalize text fields
    data['subreddit'] = (data.get('subreddit') or '').lower()
    data['author'] = (data.get('author') or '').lower()

    # Merge mandatory field types with config types
    all_types = {**MANDATORY_FIELD_TYPES, **data_type_config}

    # Select escape function based on format
    _escape = escape_string_parquet if file_format == 'parquet' else escape_string

    # Build extracted list: [dataset, id, retrieved_utc, ...yaml fields...]
    extracted = [dataset]

    # Add mandatory fields first (id, retrieved_utc)
    for field in MANDATORY_FIELDS:
        value = get_nested_data(data, field)
        if isinstance(value, str):
            value = _escape(value)
        value = enforce_data_type(field, value, all_types)
        extracted.append(value)

    # Add user-configured fields from YAML
    for field in fields_to_extract:
        value = get_nested_data(data, field)
        if isinstance(value, str):
            value = _escape(value)
        last_key = field.split('.')[-1]
        value = enforce_data_type(last_key, value, all_types)
        extracted.append(value)

    return extracted


def process_single_file(
    input_file: str,
    output_file: str,
    data_type: str,
    data_type_config: Dict,
    fields_to_extract: List[str],
    file_format: str = 'csv',
    parquet_row_group_size: int = 0,
) -> ParseSettlement:
    """
    Process a single Reddit NDJSON file and write to CSV or Parquet.

    Uses a .temp file during writing and renames to final name on success.
    This ensures partial files from interrupted runs are not mistaken as
    complete. Every call returns a :class:`ParseSettlement` describing the
    per-file outcome; whole-file failures are returned (not raised) with
    status ``failed`` after cleaning up any temp file.

    Bad lines (undecodable JSON, or transform failures) are skipped and
    counted on the settlement instead of aborting the file.

    Args:
        input_file: Path to decompressed JSON file
        output_file: Path for output file (.csv or .parquet)
        data_type: 'submissions' or 'comments'
        data_type_config: Field type configuration
        fields_to_extract: List of fields to extract
        file_format: 'csv' or 'parquet'
        parquet_row_group_size: Rows per row-group (0 = default from core.parser)

    Returns:
        ParseSettlement with success_rows / bad_rows / fallback usage /
        output path and overall status for this input file.
    """
    prefix = "RS_" if data_type == "submissions" else "RC_"
    dataset = Path(input_file).name.replace(prefix, "")

    output_path = Path(output_file)
    temp_path = output_path.with_suffix(output_path.suffix + '.temp')

    settlement = ParseSettlement(
        input_file=Path(input_file).name,
        data_type=data_type,
        output_path=str(output_path),
    )

    # Get column names for header
    columns = get_all_columns(data_type, fields_to_extract)

    try:
        # Clean up any leftover temp file from an interrupted run
        if temp_path.exists():
            print(f"[sdp] Removing incomplete temp file: {temp_path.name}")
            temp_path.unlink()

        if file_format == 'parquet':
            # Parquet: stream rows via BatchedParquetWriter (bounded memory)
            writer_kwargs = {}
            if parquet_row_group_size:
                writer_kwargs['batch_size'] = parquet_row_group_size
            writer = BatchedParquetWriter(
                columns, data_type_config, str(output_path), **writer_kwargs
            )
            try:
                with open(input_file, 'r', encoding='utf-8', errors='replace') as infile:
                    for _line_number, values in iter_ndjson_records(
                        infile,
                        settlement,
                        lambda data: transform_json(
                            data, dataset, data_type_config, fields_to_extract,
                            file_format='parquet', fallback_tracker=settlement,
                        ),
                        log_label=input_file,
                    ):
                        writer.append(dict(zip(columns, values)))
                writer.close()
            except Exception:
                writer.cleanup()
                raise

        else:
            # CSV: row-by-row write via temp file, atomic rename on success
            header_row = ','.join(columns)
            with open(input_file, 'r', encoding='utf-8', errors='replace') as infile, \
                 open(temp_path, 'w', newline='', encoding='utf-8') as outfile:

                outfile.write(header_row + '\n')

                for _line_number, csv_data in iter_ndjson_records(
                    infile,
                    settlement,
                    lambda data: transform_json(
                        data, dataset, data_type_config, fields_to_extract,
                        fallback_tracker=settlement,
                    ),
                    log_label=input_file,
                ):
                    csv_row = ','.join(map(quote_field, csv_data))
                    outfile.write(csv_row + '\n')

            temp_path.rename(output_path)

    except Exception as e:
        # Whole-file failure (cannot open input, cannot write, ...): keep the
        # existing temp-file cleanup behavior and never report a half-written
        # output as complete.
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError as cleanup_err:
            logging.error(
                "Failed to remove temp file %s: %s", temp_path, cleanup_err
            )
        settlement.mark_failed(e)
        try:
            settlement.input_size = os.path.getsize(input_file)
        except OSError:
            settlement.input_size = None
        settlement.output_size = None
        print(
            f"[sdp] FAILED {Path(input_file).name}: {e} "
            f"(good rows before failure: {settlement.success_rows}, "
            f"bad rows: {settlement.bad_rows})"
        )
        return settlement

    settlement.input_size = os.path.getsize(input_file)
    settlement.output_size = os.path.getsize(output_path)
    settlement.finalize_status()

    print(f"[sdp] {Path(input_file).name} -> {Path(output_path).name}")
    print(
        f"[sdp] Rows: {settlement.success_rows:,}, "
        f"Bad lines: {settlement.bad_rows}, "
        f"Output: {settlement.output_size / (1024**3):.2f} GB"
    )

    return settlement


def _configure_per_file_logging(output_dir: Path, input_path: Path) -> Tuple[Optional[Path], Optional[logging.Handler]]:
    """Route parsing error logs to a per-input-file log.

    The old fixed-name ``parsing_errors_<data_type>.log`` was shared by every
    file of a type, so parallel workers overwrote each other's logs. The log
    now embeds the input-file identifier, matching the settlement JSON naming.

    Returns ``(log_path, handler)``; on failure to create the handler returns
    ``(None, None)`` so parsing itself is never blocked by logging setup.
    """
    log_filename = output_dir / f"parsing_errors_{input_path.name}.log"
    try:
        log_handler = logging.FileHandler(str(log_filename), encoding='utf-8')
    except OSError as e:
        logging.error("Could not open per-file error log %s: %s", log_filename, e)
        return None, None
    log_handler.setLevel(logging.ERROR)
    log_handler.setFormatter(
        logging.Formatter('%(asctime)s:%(levelname)s:%(message)s')
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.ERROR)
    root_logger.addHandler(log_handler)
    return log_filename, log_handler


def parse_file_to_settlement(
    input_file: str,
    output_dir: str,
    data_type: str,
    platform_config: Dict,
    use_type_subdir: bool = True,
) -> ParseSettlement:
    """
    Parse one Reddit NDJSON file and return its settlement record.

    This is the result-bearing core used by both sequential parsing
    (:func:`parse_to_csv`) and parallel workers. The settlement JSON is
    written next to the output file with a per-input-file name, including
    for failed files, so every input file has a verifiable result.

    Args:
        input_file: Path to decompressed JSON file (e.g., RC_2023-01)
        output_dir: Directory for output file (or base dir if use_type_subdir=True)
        data_type: 'submissions' or 'comments'
        platform_config: Loaded platform configuration dict (fields, field_types, etc.)
        use_type_subdir: If True, output to output_dir/data_type/ (default: True)

    Returns:
        ParseSettlement (status success / partial_success / failed). The
        settlement_path field points at the written JSON record.
    """
    output_dir = Path(output_dir)
    if use_type_subdir:
        output_dir = output_dir / data_type
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract fields and types from platform config
    field_types = platform_config.get('field_types', {})
    if not field_types:
        raise ConfigurationError("No field_types configured in platform config")

    fields_to_extract = platform_config.get('fields', {}).get(data_type, [])
    if not fields_to_extract:
        raise ConfigurationError(f"No fields configured for data type: {data_type}")

    file_format = platform_config.get('file_format', 'csv')

    # Per-file error log: unique name so parallel workers cannot collide.
    input_path = Path(input_file)
    log_filename, log_handler = _configure_per_file_logging(output_dir, input_path)
    root_logger = logging.getLogger()

    # Determine output filename
    ext = '.parquet' if file_format == 'parquet' else '.csv'
    output_file = output_dir / f"{input_path.name}{ext}"

    parquet_row_group_size = platform_config.get('parquet_row_group_size', 0)

    try:
        # Process the file. Failures come back as a 'failed' settlement rather
        # than an exception so the record can still be written and audited.
        settlement = process_single_file(
            input_file=str(input_path),
            output_file=str(output_file),
            data_type=data_type,
            data_type_config=field_types,
            fields_to_extract=fields_to_extract,
            file_format=file_format,
            parquet_row_group_size=parquet_row_group_size,
        )
    finally:
        if log_handler is not None:
            root_logger.removeHandler(log_handler)
            log_handler.close()

    # Always persist the per-file settlement (even for failures).
    write_settlement(settlement, output_dir, input_path.name)

    # Clean up empty per-file log (no bad lines -> nothing was logged).
    if log_filename is not None:
        try:
            if log_filename.exists() and log_filename.stat().st_size == 0:
                log_filename.unlink()
        except OSError:
            pass

    return settlement


def parse_to_csv(
    input_file: str,
    output_dir: str,
    data_type: str,
    platform_config: Dict,
    use_type_subdir: bool = True
) -> str:
    """
    Parse a decompressed Reddit JSON file to CSV or Parquet.

    Output format is determined by platform_config['file_format'] (default: 'csv').

    A per-file settlement JSON (rows, bad lines, timestamp-fallback usage,
    output path, status) is written beside the output file regardless of
    outcome.

    Args:
        input_file: Path to decompressed JSON file (e.g., RC_2023-01)
        output_dir: Directory for output file (or base dir if use_type_subdir=True)
        data_type: 'submissions' or 'comments'
        platform_config: Loaded platform configuration dict (fields, field_types, etc.)
        use_type_subdir: If True, output to output_dir/data_type/ (default: True)

    Returns:
        Path to the output file

    Raises:
        ConfigurationError: If config is missing required keys
        ParseFailureError: If the whole file could not be parsed. The attached
            settlement (and its JSON record) describes the failure; any temp
            file has already been cleaned up.
    """
    settlement = parse_file_to_settlement(
        input_file=input_file,
        output_dir=output_dir,
        data_type=data_type,
        platform_config=platform_config,
        use_type_subdir=use_type_subdir,
    )

    if settlement.status == 'failed':
        raise ParseFailureError(settlement)

    return settlement.output_path


def _parse_file_worker(args: Tuple[str, str, str, Dict]) -> ParseSettlement:
    """
    Worker function for parallel parsing. Must be at module level for pickling.

    A failing file never raises out of the worker: it returns a 'failed'
    settlement so the pool can keep processing every other file and the
    caller receives one settlement per input file.

    Args:
        args: Tuple of (input_file, output_dir, data_type, platform_config)

    Returns:
        ParseSettlement for this input file
    """
    input_file, output_dir, data_type, platform_config = args
    try:
        return parse_file_to_settlement(input_file, output_dir, data_type, platform_config)
    except Exception as e:
        # Configuration-level or other pre-processing failure: synthesize a
        # failed settlement and still try to persist it for auditability.
        input_path = Path(input_file)
        type_dir = Path(output_dir) / data_type
        ext = '.parquet' if platform_config.get('file_format', 'csv') == 'parquet' else '.csv'
        settlement = ParseSettlement(
            input_file=input_path.name,
            data_type=data_type,
            output_path=str(type_dir / f"{input_path.name}{ext}"),
        ).mark_failed(e)
        try:
            type_dir.mkdir(parents=True, exist_ok=True)
            write_settlement(settlement, type_dir, input_path.name)
        except Exception:
            pass
        return settlement


def parse_files_parallel(
    files: List[Tuple[str, str]],
    output_dir: str,
    platform_config: Dict,
    workers: int
) -> List[ParseSettlement]:
    """
    Parse multiple Reddit JSON files in parallel.

    A single file failing never interrupts the others: every input file
    produces a settlement record and all records are collected and returned
    in input order. Callers distinguish outcomes via
    ``settlement.status`` (``success`` / ``partial_success`` / ``failed``).

    Args:
        files: List of tuples (input_file, data_type)
        output_dir: Directory for output files
        platform_config: Loaded platform configuration dict
        workers: Number of parallel workers

    Returns:
        List of ParseSettlement records, one per input file, in input order.
    """
    if not files:
        return []

    print(f"[sdp] Starting parallel parsing with up to {workers} workers for {len(files)} files")

    # Prepare arguments for workers
    worker_args = [
        (input_file, output_dir, data_type, platform_config)
        for input_file, data_type in files
    ]

    # Pre-size so results stay aligned with input order even though workers
    # finish at different times.
    settlements: List[Optional[ParseSettlement]] = [None] * len(worker_args)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(_parse_file_worker, args): idx
            for idx, args in enumerate(worker_args)
        }
        for future in future_to_index:
            idx = future_to_index[future]
            try:
                settlements[idx] = future.result()
            except Exception as e:
                # Extremely defensive: workers are expected to return failed
                # settlements rather than raise. Never let one file sink the run.
                input_file, data_type = files[idx]
                input_path = Path(input_file)
                type_dir = Path(output_dir) / data_type
                ext = '.parquet' if platform_config.get('file_format', 'csv') == 'parquet' else '.csv'
                settlement = ParseSettlement(
                    input_file=input_path.name,
                    data_type=data_type,
                    output_path=str(type_dir / f"{input_path.name}{ext}"),
                ).mark_failed(e)
                try:
                    type_dir.mkdir(parents=True, exist_ok=True)
                    write_settlement(settlement, type_dir, input_path.name)
                except Exception:
                    pass
                settlements[idx] = settlement
                print(f"[sdp] Error in parallel parsing for {input_path.name}: {e}")

    results = [s for s in settlements if s is not None]

    succeeded = sum(1 for s in results if s.status == 'success')
    partial = sum(1 for s in results if s.status == 'partial_success')
    failed = sum(1 for s in results if s.status == 'failed')
    print(
        f"[sdp] Parallel parsing complete: {len(results)} files processed "
        f"({succeeded} success, {partial} partial success, {failed} failed)"
    )
    return results
