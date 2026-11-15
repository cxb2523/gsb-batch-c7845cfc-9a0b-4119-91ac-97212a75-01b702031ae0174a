from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from excel_automation_toolkit.cli import main
from excel_automation_toolkit.data_loader import load_table
from excel_automation_toolkit.pipeline import PipelineConfig, run_pipeline
from excel_automation_toolkit.preprocessing import clean_dataframe
from excel_automation_toolkit.report_generator import build_report


def test_clean_dataframe_normalizes_headers_and_trims_text() -> None:
    df = pd.DataFrame(
        {
            " Order Date ": ["2026-01-01"],
            " Region ": [" North "],
            "Revenue": ["1,250"],
        }
    )

    cleaned = clean_dataframe(df, required_columns=["order_date"], date_columns=["order_date"])

    assert list(cleaned.columns) == ["order_date", "region", "revenue"]
    assert cleaned.loc[0, "region"] == "North"
    assert pd.Timestamp("2026-01-01") == cleaned.loc[0, "order_date"]


def test_pipeline_generates_formatted_workbook(tmp_path: Path) -> None:
    input_path = tmp_path / "sales.csv"
    output_path = tmp_path / "report.xlsx"
    input_path.write_text(
        "Order Date,Region,Revenue\n"
        "2026-01-01,North,100\n"
        "2026-01-02,North,250\n"
        "2026-01-03,South,75\n",
        encoding="utf-8",
    )

    result = run_pipeline(
        PipelineConfig(
            input_path=input_path,
            output_path=output_path,
            date_columns=("Order Date",),
            numeric_columns=("Revenue",),
            dimensions=("Region",),
            amount_column="Revenue",
        )
    )

    assert result == output_path
    workbook = load_workbook(output_path, read_only=False)
    assert {"Raw Data", "Processed Data", "Summary", "Metadata"} <= set(workbook.sheetnames)

    summary = workbook["Summary"]
    rows = list(summary.iter_rows(values_only=True))
    assert rows[0] == ("region", "revenue_count", "revenue_sum", "revenue_mean")
    assert ("North", 2, 350, 175) in rows


def test_cli_run_generates_report(tmp_path: Path, capsys) -> None:
    input_path = tmp_path / "sales.csv"
    output_path = tmp_path / "cli-report.xlsx"
    input_path.write_text("Region,Revenue\nNorth,100\nSouth,75\n", encoding="utf-8")

    exit_code = main(
        [
            "run",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--numeric-column",
            "Revenue",
            "--dimension",
            "Region",
            "--amount-column",
            "Revenue",
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    assert f"Report generated: {output_path}" in capsys.readouterr().out


def test_loading_all_excel_sheets_raises_clear_error(tmp_path: Path) -> None:
    input_path = tmp_path / "multi-sheet.xlsx"
    with pd.ExcelWriter(input_path, engine="openpyxl") as writer:
        pd.DataFrame({"Region": ["North"]}).to_excel(writer, sheet_name="Sales", index=False)

    try:
        load_table(input_path, sheet_name=None)
    except ValueError as exc:
        assert "Expected a single worksheet" in str(exc)
        assert "Sales" in str(exc)
    else:
        raise AssertionError("Expected load_table to reject multiple worksheet output")


def _read_sheet(path: Path, sheet: str) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, engine="openpyxl")


def _file_rows(metadata: pd.DataFrame) -> pd.DataFrame:
    return metadata[metadata["key"] == "file"].reset_index(drop=True)


def test_directory_input_merges_supported_files(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "b_sales.csv").write_text(
        "Order Date,Region,Revenue\n"
        "2026-02-01,South,75\n"
        "2026-01-01,North,100\n",  # cross-file duplicate of a row in a_sales.csv
        encoding="utf-8",
    )
    (input_dir / "a_sales.csv").write_text(
        "Order Date,Region,Revenue\n"
        "2026-01-01,North,100\n"
        "2026-01-02,North,250\n",
        encoding="utf-8",
    )
    (input_dir / "notes.txt").write_text("not a supported file", encoding="utf-8")
    output_path = tmp_path / "report.xlsx"

    result = build_report(
        input_dir,
        output_path,
        date_columns=("Order Date",),
        numeric_columns=("Revenue",),
        dimensions=("Region",),
        amount_column="Revenue",
    )

    assert result == output_path
    workbook = load_workbook(output_path, read_only=False)
    assert {"Raw Data", "Processed Data", "Summary", "Metadata"} <= set(workbook.sheetnames)

    raw = _read_sheet(output_path, "Raw Data")
    # Files load in filename order and every row is tagged with its source file.
    assert list(raw["source_file"]) == ["a_sales.csv", "a_sales.csv", "b_sales.csv", "b_sales.csv"]

    processed = _read_sheet(output_path, "Processed Data")
    # The cross-file duplicate row (2026-01-01, North, 100) is dropped by cleaning.
    assert len(processed) == 3

    metadata = _read_sheet(output_path, "Metadata")
    overall = metadata.set_index("key")["value"]
    assert overall["rows_loaded"] == 4
    assert overall["rows_processed"] == 3

    files = _file_rows(metadata)
    assert list(files["value"]) == ["a_sales.csv", "b_sales.csv"]
    assert list(files["status"]) == ["ok", "ok"]
    assert list(files["rows_loaded"]) == [2, 2]
    # b_sales.csv contributed 2 rows but only 1 survives cross-file dedup.
    assert list(files["rows_processed"]) == [2, 1]

    summary = _read_sheet(output_path, "Summary")
    north = summary[summary["region"] == "North"].iloc[0]
    assert north["revenue_count"] == 2
    assert north["revenue_sum"] == 350


def test_directory_input_skips_bad_file_and_records_error(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "good.csv").write_text("Region,Revenue\nNorth,100\nSouth,75\n", encoding="utf-8")
    # A .xlsx file whose contents are not a real workbook fails to parse.
    (input_dir / "broken.xlsx").write_bytes(b"this is not a real xlsx file")
    output_path = tmp_path / "report.xlsx"

    result = build_report(input_dir, output_path, numeric_columns=("Revenue",))

    assert result == output_path
    processed = _read_sheet(output_path, "Processed Data")
    assert list(processed["source_file"]) == ["good.csv", "good.csv"]
    assert len(processed) == 2

    metadata = _read_sheet(output_path, "Metadata")
    files = _file_rows(metadata)
    assert list(files["value"]) == ["broken.xlsx", "good.csv"]

    broken = files[files["value"] == "broken.xlsx"].iloc[0]
    assert broken["status"] == "failed"
    assert broken["rows_loaded"] == 0
    assert broken["rows_processed"] == 0
    assert isinstance(broken["error"], str) and broken["error"]

    good = files[files["value"] == "good.csv"].iloc[0]
    assert good["status"] == "ok"
    assert good["rows_loaded"] == 2
    assert good["rows_processed"] == 2


def test_directory_input_without_supported_files_raises(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "notes.txt").write_text("nothing usable", encoding="utf-8")

    try:
        build_report(input_dir, tmp_path / "report.xlsx")
    except ValueError as exc:
        assert "No supported input files" in str(exc)
    else:
        raise AssertionError("Expected build_report to reject a directory without supported files")

    assert not (tmp_path / "report.xlsx").exists()


def test_directory_input_with_all_files_failing_raises(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    (input_dir / "broken_a.xlsx").write_bytes(b"not a workbook")
    (input_dir / "broken_b.xlsx").write_bytes(b"also not a workbook")
    output_path = tmp_path / "report.xlsx"

    try:
        build_report(input_dir, output_path)
    except ValueError as exc:
        message = str(exc)
        assert "failed to load" in message
        assert "broken_a.xlsx" in message and "broken_b.xlsx" in message
    else:
        raise AssertionError("Expected build_report to fail when every file fails")

    assert not output_path.exists()


def test_empty_directory_raises(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()

    try:
        build_report(input_dir, tmp_path / "report.xlsx")
    except ValueError as exc:
        assert "No supported input files" in str(exc)
    else:
        raise AssertionError("Expected build_report to reject an empty directory")
