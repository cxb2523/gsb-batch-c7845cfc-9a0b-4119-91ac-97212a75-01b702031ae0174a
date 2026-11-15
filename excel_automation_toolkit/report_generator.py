from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .data_loader import find_input_files, load_table
from .feature_engineering import add_time_features, aggregate_metrics
from .preprocessing import clean_dataframe, coerce_numeric_columns, normalize_column_name

SOURCE_FILE_COLUMN = "source_file"


def build_report(
    input_path: str | Path,
    output_path: str | Path,
    *,
    sheet_name: str | int | None = 0,
    required_columns: Iterable[str] = (),
    date_columns: Iterable[str] = (),
    numeric_columns: Iterable[str] = (),
    dimensions: Iterable[str] = (),
    amount_column: str | None = None,
) -> Path:
    """Create a formatted Excel report with raw, cleaned, summary, and metadata sheets.

    ``input_path`` may be a single CSV/Excel file or a directory. For a directory,
    every supported file (.csv/.xlsx/.xlsm/.xls) is loaded in filename order, each
    row is tagged with its source file name, and the merged data flows through the
    standard pipeline. Files that fail to load are skipped and recorded in the
    Metadata sheet; a clear error is raised when no supported file exists or all
    files fail.
    """
    source = Path(input_path)
    file_records: list[dict[str, object]] | None = None
    if source.is_dir():
        raw, file_records = _load_directory(source, sheet_name=sheet_name)
    else:
        raw = load_table(source, sheet_name=sheet_name)

    if file_records is None:
        cleaned = clean_dataframe(raw, required_columns=required_columns, date_columns=date_columns)
    else:
        # Deduplicate on the data columns only so identical rows coming from
        # different files are still dropped even though their source_file tags differ.
        cleaned = clean_dataframe(
            raw,
            required_columns=required_columns,
            date_columns=date_columns,
            drop_duplicates=False,
        )
        data_columns = [column for column in cleaned.columns if column != SOURCE_FILE_COLUMN]
        cleaned = cleaned.drop_duplicates(subset=data_columns).reset_index(drop=True)

    numeric_inputs = list(numeric_columns)
    if amount_column:
        numeric_inputs.append(amount_column)
    cleaned = coerce_numeric_columns(cleaned, numeric_inputs)
    enriched = add_time_features(cleaned, date_columns)

    summary = _build_summary(enriched, dimensions=dimensions, amount_column=amount_column)
    metadata = _build_metadata(
        input_path,
        rows_loaded=len(raw),
        rows_processed=len(enriched),
        file_records=file_records,
        enriched=enriched,
    )

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(destination, engine="openpyxl") as writer:
        raw.to_excel(writer, sheet_name="Raw Data", index=False)
        enriched.to_excel(writer, sheet_name="Processed Data", index=False)
        summary.to_excel(writer, sheet_name="Summary", index=False)
        metadata.to_excel(writer, sheet_name="Metadata", index=False)

    _format_workbook(destination)
    return destination


def _load_directory(
    directory: Path, *, sheet_name: str | int | None = 0
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Load every supported file in a directory, isolating per-file failures."""
    files = find_input_files(directory)
    if not files:
        raise ValueError(f"No supported input files (.csv/.xlsx/.xlsm/.xls) found in directory: {directory}")

    frames: list[pd.DataFrame] = []
    records: list[dict[str, object]] = []
    failures: list[str] = []
    for file_path in files:
        try:
            frame = load_table(file_path, sheet_name=sheet_name)
        except Exception as exc:  # noqa: BLE001 - isolate each file's failure
            records.append(
                {"file": file_path.name, "status": "failed", "rows_loaded": 0, "error": str(exc)}
            )
            failures.append(f"{file_path.name}: {exc}")
            continue

        frame.insert(0, SOURCE_FILE_COLUMN, file_path.name)
        frames.append(frame)
        records.append(
            {"file": file_path.name, "status": "ok", "rows_loaded": len(frame), "error": None}
        )

    if not frames:
        details = "; ".join(failures)
        raise ValueError(
            f"All {len(files)} supported files in directory {directory} failed to load: {details}"
        )

    return pd.concat(frames, ignore_index=True), records


def _build_metadata(
    input_path: str | Path,
    *,
    rows_loaded: int,
    rows_processed: int,
    file_records: list[dict[str, object]] | None,
    enriched: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = [
        {"key": "input_path", "value": str(input_path)},
        {"key": "rows_loaded", "value": rows_loaded},
        {"key": "rows_processed", "value": rows_processed},
        {"key": "generated_at_utc", "value": datetime.now(timezone.utc).isoformat(timespec="seconds")},
    ]

    if file_records is not None:
        processed_counts = (
            enriched[SOURCE_FILE_COLUMN].value_counts().to_dict()
            if SOURCE_FILE_COLUMN in enriched.columns
            else {}
        )
        for record in file_records:
            rows.append(
                {
                    "key": "file",
                    "value": record["file"],
                    "status": record["status"],
                    "rows_loaded": record["rows_loaded"],
                    "rows_processed": int(processed_counts.get(record["file"], 0)),
                    "error": record["error"],
                }
            )

    return pd.DataFrame(rows)


def _build_summary(
    df: pd.DataFrame,
    *,
    dimensions: Iterable[str],
    amount_column: str | None,
) -> pd.DataFrame:
    normalized_dimensions = [normalize_column_name(column) for column in dimensions if column]
    normalized_amount = normalize_column_name(amount_column) if amount_column else None

    if normalized_dimensions and normalized_amount and normalized_amount in df.columns:
        return aggregate_metrics(
            df,
            dimensions=normalized_dimensions,
            metrics={normalized_amount: ["count", "sum", "mean"]},
        )

    return pd.DataFrame(
        [
            {"metric": "rows", "value": len(df)},
            {"metric": "columns", "value": len(df.columns)},
        ]
    )


def _format_workbook(path: Path) -> None:
    from openpyxl import load_workbook

    workbook = load_workbook(path)
    header_fill = PatternFill("solid", fgColor="1C2B36")
    header_font = Font(color="E6EEF3", bold=True)

    for worksheet in workbook.worksheets:
        if worksheet.max_row == 0 or worksheet.max_column == 0:
            continue

        for cell in worksheet[1]:
            cell.fill = header_fill
            cell.font = header_font

        for column_cells in worksheet.columns:
            column_letter = get_column_letter(column_cells[0].column)
            max_length = max(len(str(cell.value or "")) for cell in column_cells)
            worksheet.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 42)

        table_ref = f"A1:{get_column_letter(worksheet.max_column)}{worksheet.max_row}"
        table_name = "".join(part for part in worksheet.title.title() if part.isalnum())[:240] or "ReportTable"
        table = Table(displayName=table_name, ref=table_ref)
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        worksheet.add_table(table)
        worksheet.freeze_panes = "A2"

    workbook.save(path)

