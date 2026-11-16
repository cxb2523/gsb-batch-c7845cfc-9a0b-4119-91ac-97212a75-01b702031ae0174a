"""Tests for the input directory scan and its file inventory."""

from pathlib import Path

import pytest
from loguru import logger
from openpyxl import Workbook

from xl2times import main, utils


def _write_workbook(path: Path) -> None:
    """Create a minimal valid xlsx workbook with a single table tag."""
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.worksheets[0]
    sheet.append(["~FI_T"])
    sheet.append(["process", "act_bnd"])
    sheet.append(["PRC1", 1])
    workbook.save(path)


def _included(inventory) -> list[str]:
    return [entry.relative_path for entry in inventory.included]


def _excluded(inventory) -> dict[str, str]:
    return {entry.relative_path: entry.reason for entry in inventory.excluded}


class TestScanVedaDirectory:
    def test_inventory_records_included_and_excluded_files(self, tmp_path: Path):
        _write_workbook(tmp_path / "SysSettings.xlsx")
        _write_workbook(tmp_path / "VT_NZ.xlsx")
        _write_workbook(tmp_path / "SubRES_TMPL" / "SubRES_NewTechs.xlsx")
        _write_workbook(tmp_path / "not_a_veda_file.xlsx")
        _write_workbook(tmp_path / "~$SysSettings.xlsx")
        (tmp_path / "notes.txt").write_text("not a workbook", encoding="utf-8")

        inventory = utils.scan_input_directory(str(tmp_path))

        assert inventory.veda_based
        assert _included(inventory) == [
            "SubRES_TMPL/SubRES_NewTechs.xlsx",
            "SysSettings.xlsx",
            "VT_NZ.xlsx",
        ]
        assert _excluded(inventory) == {
            "~$SysSettings.xlsx": utils.REASON_TEMPORARY_FILE,
            "not_a_veda_file.xlsx": utils.REASON_VEDA_PATTERN_MISMATCH,
            "notes.txt": "unsupported file extension ('.txt')",
        }

    def test_inventory_order_is_stable_and_matches_read_order(self, tmp_path: Path):
        for name in ["SysSettings.xlsx", "VT_B.xlsx", "VT_A.xlsx", "Set_Regions.xlsx"]:
            _write_workbook(tmp_path / name)

        first = utils.scan_input_directory(str(tmp_path))
        second = utils.scan_input_directory(str(tmp_path))

        assert first.to_text() == second.to_text()
        # Included files are listed in the same order as they are read.
        assert first.input_files == [entry.path for entry in first.included]
        assert [Path(p).name for p in first.input_files] == [
            "Set_Regions.xlsx",
            "SysSettings.xlsx",
            "VT_A.xlsx",
            "VT_B.xlsx",
        ]

    def test_multiple_syssettings_raises_with_conflicting_files(self, tmp_path: Path):
        _write_workbook(tmp_path / "SysSettings.xlsx")
        _write_workbook(tmp_path / "sub" / "SysSettings.xlsx")
        _write_workbook(tmp_path / "VT_NZ.xlsx")

        with pytest.raises(ValueError) as excinfo:
            utils.scan_input_directory(str(tmp_path))

        message = str(excinfo.value)
        assert str(tmp_path / "SysSettings.xlsx") in message
        assert str(tmp_path / "sub" / "SysSettings.xlsx") in message


class TestScanNonVedaDirectory:
    def test_all_legal_suffix_files_are_included(self, tmp_path: Path):
        _write_workbook(tmp_path / "anything.xlsx")
        _write_workbook(tmp_path / "some_model.xlsm")
        _write_workbook(tmp_path / "~$anything.xlsx")
        (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

        inventory = utils.scan_input_directory(str(tmp_path))

        assert not inventory.veda_based
        assert _included(inventory) == ["anything.xlsx", "some_model.xlsm"]
        assert set(_excluded(inventory)) == {"~$anything.xlsx", "readme.txt"}

    def test_inventory_is_written_to_output_dir(self, tmp_path: Path):
        _write_workbook(tmp_path / "model.xlsx")

        inventory = utils.scan_input_directory(str(tmp_path))
        output_dir = tmp_path / "output"
        inventory_file = main.write_file_inventory(inventory, str(output_dir))

        assert Path(inventory_file).is_file()
        assert Path(inventory_file).name == "file_inventory.txt"
        text = Path(inventory_file).read_text(encoding="utf-8")
        assert f"input_directory: {tmp_path}" in text
        assert "veda_based: no" in text
        assert "scanned: 1 (included: 1, excluded: 0)" in text
        assert "[included] model.xlsx" in text


class TestReadXlInventory:
    def test_read_xl_writes_inventory_and_logs_counts(self, tmp_path: Path):
        _write_workbook(tmp_path / "model.xlsx")
        _write_workbook(tmp_path / "~$model.xlsx")
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")

        messages: list[str] = []
        sink_id = logger.add(
            lambda message: messages.append(str(message)), level="INFO"
        )
        try:
            model, _ = main.read_xl(
                [str(tmp_path)],
                regions="",
                include_dummy_imports=False,
                output_dir=str(tmp_path / "output"),
                no_cache=True,
                stop_after_read=True,
            )
        finally:
            logger.remove(sink_id)

        assert model.inventory is not None
        assert model.inventory.included[0].relative_path == "model.xlsx"
        inventory_file = tmp_path / "output" / "file_inventory.txt"
        assert inventory_file.is_file()
        assert "[included] model.xlsx" in inventory_file.read_text(encoding="utf-8")

        logged = "".join(messages)
        assert "Loading 1 files" in logged
        assert "1 included, 2 excluded, 3 scanned" in logged

    def test_file_list_input_is_unchanged(self, tmp_path: Path):
        first = tmp_path / "a.xlsx"
        second = tmp_path / "b.xlsx"
        _write_workbook(first)
        _write_workbook(second)

        model, _ = main.read_xl(
            [str(second), str(first)],
            regions="",
            include_dummy_imports=False,
            no_cache=True,
            stop_after_read=True,
        )

        # Explicit file lists keep their given order and produce no inventory.
        assert model.files == ["b", "a"]
        assert model.inventory is None
