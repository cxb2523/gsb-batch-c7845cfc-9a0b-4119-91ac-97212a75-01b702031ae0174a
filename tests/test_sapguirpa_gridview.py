"""Tests for the GuiGridView scraping capabilities of SapGuiRpa.

The tests use fake objects instead of a real SAP GUI installation, so they
run completely without a SAP environment. The fakes mimic the parts of the
GuiGridView scripting API that the scraping code relies on:

* ``RowCount`` / ``VisibleRowCount`` properties,
* ``GetCellValue(row_index, column_name)``,
* the scroll position property ``currentCellRow``.
"""

import pytest

from sap_gui_rpa.common import sapguirpa
from sap_gui_rpa.common.sapguirpa import (
    SapGuiRpa,
    ROW_SCRAPE_SUCCESS,
    ROW_SCRAPE_FAILED,
)


GRIDVIEW_ID = "wnd[0]/usr/cntlGRIDCONTROL/shellcont/shell"
COLUMN_NAMES = ["COL_A", "COL_B", "COL_C"]


class FakeComError(Exception):
    """Stands in for pywintypes.com_error raised by real SAP scripting."""


class FakeGridView:
    """Fake GuiGridView object.

    ``data`` maps a row index to a dict of column name -> cell value. Cells
    missing from ``data`` raise :class:`FakeComError`, simulating a cell that
    cannot be read.

    When ``visible_row_count`` is greater than zero a row has to be scrolled
    into the visible window before ``GetCellValue`` succeeds, mirroring the
    behaviour of a real grid. Every assignment to ``currentCellRow`` is
    recorded in ``scroll_calls`` so scrolling can be asserted on.
    """

    def __init__(self, data, row_count, visible_row_count):
        self.data = data
        self.RowCount = row_count
        self.VisibleRowCount = visible_row_count
        self.current_cell_row = 0
        self.scroll_calls = []

    @property
    def currentCellRow(self):
        return self.current_cell_row

    @currentCellRow.setter
    def currentCellRow(self, value):
        self.scroll_calls.append(value)
        self.current_cell_row = value

    def _first_visible_row(self):
        if self.VisibleRowCount <= 0:
            return 0
        # assigning currentCellRow scrolls the given row into the visible
        # window (clamped to the last row of the grid)
        target = min(self.current_cell_row, max(self.RowCount - 1, 0))
        first_visible = max(
            0, target - self.VisibleRowCount + 1)
        return first_visible

    def GetCellValue(self, row_index, column_name):
        if self.VisibleRowCount > 0:
            first_visible = self._first_visible_row()
            last_visible = first_visible + self.VisibleRowCount - 1
            if not first_visible <= row_index <= last_visible:
                raise FakeComError(
                    f"row {row_index} is not visible "
                    f"(window {first_visible}-{last_visible})")

        try:
            return self.data[row_index][column_name]
        except (KeyError, TypeError):
            raise FakeComError(
                f"cannot read cell row={row_index}, column={column_name}")


class FakeSession:
    """Minimal GuiSession stand-in resolving element ids via findById."""

    def __init__(self, elements):
        self._elements = elements

    def findById(self, element_id):
        return self._elements[element_id]


def build_rpa(gridview):
    """Create a SapGuiRpa instance wired to a fake session."""
    rpa = SapGuiRpa()
    rpa.session = FakeSession({GRIDVIEW_ID: gridview})
    return rpa


def build_data(row_count, column_names):
    """Build deterministic cell data for ``row_count`` rows."""
    return {
        row_index: {
            column_name: f"{column_name}-{row_index}"
            for column_name in column_names
        }
        for row_index in range(row_count)
    }


# ---------------------------------------------------------------------------
# scenario 1: empty table
# ---------------------------------------------------------------------------

def test_empty_table_per_row_returns_empty_list_without_scrolling():
    gridview = FakeGridView(data={}, row_count=0, visible_row_count=0)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    assert result == []
    # an empty table must not enter the scrolling branch at all
    assert gridview.scroll_calls == []


def test_empty_table_legacy_entry_returns_empty_list():
    gridview = FakeGridView(data={}, row_count=0, visible_row_count=0)
    rpa = build_rpa(gridview)

    assert rpa.scrape_rows_grid_view(GRIDVIEW_ID, COLUMN_NAMES) == []
    assert gridview.scroll_calls == []


# ---------------------------------------------------------------------------
# scenario 2: VisibleRowCount smaller than RowCount -> scrolling required
# ---------------------------------------------------------------------------

def test_scraping_scrolls_when_visible_rows_fewer_than_total():
    row_count = 10
    visible_row_count = 3
    gridview = FakeGridView(
        data=build_data(row_count, COLUMN_NAMES),
        row_count=row_count,
        visible_row_count=visible_row_count)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    # one settlement per row, all successful and in original order
    assert len(result) == row_count
    assert [row["row_index"] for row in result] == list(range(row_count))
    assert all(row["status"] == ROW_SCRAPE_SUCCESS for row in result)
    assert all(row["error"] is None for row in result)

    # values follow the column_names order
    for row_index, row in enumerate(result):
        assert row["values"] == [
            f"{column_name}-{row_index}"
            for column_name in COLUMN_NAMES
        ]

    # the grid had to be scrolled; pages start at rows 0, 3, 6, 9
    assert gridview.scroll_calls == [2, 5, 8, 9]


def test_scraping_without_scrolling_does_not_scroll():
    row_count = 4
    gridview = FakeGridView(
        data=build_data(row_count, COLUMN_NAMES),
        row_count=row_count,
        visible_row_count=row_count)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    assert len(result) == row_count
    assert all(row["status"] == ROW_SCRAPE_SUCCESS for row in result)
    assert gridview.scroll_calls == []


def test_legacy_entry_keeps_row_list_structure_when_scrolling():
    row_count = 7
    gridview = FakeGridView(
        data=build_data(row_count, COLUMN_NAMES),
        row_count=row_count,
        visible_row_count=3)
    rpa = build_rpa(gridview)

    rows = rpa.scrape_rows_grid_view(GRIDVIEW_ID, COLUMN_NAMES)

    # backwards-compatible structure: list of lists, ordered by column_names
    assert rows == [
        [f"{column_name}-{row_index}" for column_name in COLUMN_NAMES]
        for row_index in range(row_count)
    ]
    # no duplicates and no missing rows
    assert len(rows) == row_count
    assert gridview.scroll_calls == [2, 5, 6]


# ---------------------------------------------------------------------------
# scenario 3: a single row fails while the rest is delivered
# ---------------------------------------------------------------------------

def test_single_row_failure_does_not_abort_the_batch():
    row_count = 5
    data = build_data(row_count, COLUMN_NAMES)
    failing_row = 2
    # one cell of one row cannot be read, like a flaky COM call
    del data[failing_row]["COL_B"]

    gridview = FakeGridView(
        data=data, row_count=row_count, visible_row_count=2)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    assert len(result) == row_count
    # every row, successful or not, keeps its original row number
    assert [row["row_index"] for row in result] == list(range(row_count))

    failed = next(row for row in result if row["row_index"] == failing_row)
    assert failed["status"] == ROW_SCRAPE_FAILED
    assert "COL_B" in failed["error"]
    # readable cells of the failed row are still delivered; the missing one
    # is represented by None and column order is preserved
    assert failed["values"] == [
        f"COL_A-{failing_row}", None, f"COL_C-{failing_row}"]

    successful = [
        row for row in result if row["row_index"] != failing_row]
    assert all(row["status"] == ROW_SCRAPE_SUCCESS for row in successful)
    assert all(row["error"] is None for row in successful)

    # the legacy entry drops only the failed row and keeps every other row
    legacy_rows = rpa.scrape_rows_grid_view(GRIDVIEW_ID, COLUMN_NAMES)
    assert len(legacy_rows) == row_count - 1
    assert legacy_rows == [
        [f"{column_name}-{row_index}" for column_name in COLUMN_NAMES]
        for row_index in range(row_count)
        if row_index != failing_row
    ]


def test_failed_row_is_settled_when_visible_row_count_is_zero():
    # VisibleRowCount 0 with rows present: no scrolling branch, no exception,
    # rows are read one by one and settled individually
    row_count = 3
    data = build_data(row_count, COLUMN_NAMES)
    del data[1]["COL_A"]

    gridview = FakeGridView(
        data=data, row_count=row_count, visible_row_count=0)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    assert gridview.scroll_calls == []
    assert [row["row_index"] for row in result] == [0, 1, 2]
    assert result[0]["status"] == ROW_SCRAPE_SUCCESS
    assert result[1]["status"] == ROW_SCRAPE_FAILED
    assert result[1]["values"] == [None, "COL_B-1", "COL_C-1"]
    assert "COL_A" in result[1]["error"]
    assert result[2]["status"] == ROW_SCRAPE_SUCCESS


# ---------------------------------------------------------------------------
# edge cases
# ---------------------------------------------------------------------------

def test_empty_column_names_returns_empty_row_values():
    row_count = 3
    gridview = FakeGridView(
        data=build_data(row_count, COLUMN_NAMES),
        row_count=row_count,
        visible_row_count=2)
    rpa = build_rpa(gridview)

    result = rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, [])

    assert len(result) == row_count
    assert all(row["values"] == [] for row in result)
    assert all(row["status"] == ROW_SCRAPE_SUCCESS for row in result)
    assert all(row["error"] is None for row in result)

    # the legacy entry returns one empty list per row, instead of raising
    legacy_rows = rpa.scrape_rows_grid_view(GRIDVIEW_ID, [])
    assert legacy_rows == [[] for _ in range(row_count)]


def test_cell_read_uses_gridview_getcellvalue():
    # the per-row entry must read cells through GetCellValue, exactly like
    # get_cell_value_grid_view does
    gridview = FakeGridView(
        data=build_data(2, COLUMN_NAMES),
        row_count=2,
        visible_row_count=2)
    rpa = build_rpa(gridview)

    rpa.scrape_rows_grid_view_per_row(GRIDVIEW_ID, COLUMN_NAMES)

    expected_calls = [
        (row_index, column_name)
        for row_index in range(2)
        for column_name in COLUMN_NAMES
    ]
    assert gridview.get_cell_value_calls == expected_calls


def test_status_constants_are_stable_strings():
    assert ROW_SCRAPE_SUCCESS == "success"
    assert ROW_SCRAPE_FAILED == "failed"
    # the module keeps the constants publicly importable
    assert sapguirpa.ROW_SCRAPE_SUCCESS == "success"
    assert sapguirpa.ROW_SCRAPE_FAILED == "failed"


@pytest.fixture(autouse=True)
def _record_get_cell_value_calls():
    """Wrap GetCellValue to capture (row, column) call arguments."""
    original = FakeGridView.GetCellValue

    def wrapped(self, row_index, column_name):
        if not hasattr(self, "get_cell_value_calls"):
            self.get_cell_value_calls = []
        self.get_cell_value_calls.append((row_index, column_name))
        return original(self, row_index, column_name)

    FakeGridView.GetCellValue = wrapped
    yield
    FakeGridView.GetCellValue = original
