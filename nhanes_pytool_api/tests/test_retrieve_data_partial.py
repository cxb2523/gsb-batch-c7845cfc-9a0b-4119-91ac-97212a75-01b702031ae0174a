"""
Offline acceptance tests for the multi-cycle retrieve_data rework.

These tests never touch the network: a fake subclass of NHANESDataAPI injects
locally fabricated data files and an injectable reader (_read_cycle_data), plus a
fabricated variable table so get_common_and_uncommon_variables also runs offline.

Covered scenarios:
1. Two cycles requested, one succeeds and one fails -> merged data from the
   successful cycle is returned, and the per-cycle report marks one 'success'
   and one 'failed' (with a reason).
2. A requested cycle has no data file -> it is marked 'skipped', the other
   cycle's data is still returned.
3. Every cycle fails or is skipped -> ValueError is raised (no empty-frame
   success), and the per-cycle report is still available afterwards.
4. include_uncommon_variables=False trims to variables common to the
   *successful* cycles, and the 'year' column marks the source cycle.
"""

import unittest

import pandas as pd

from nhanes_pytool_api.nhanes_data.nhanes_data_api import NHANESDataAPI


class FakeNHANESDataAPI(NHANESDataAPI):
    """NHANESDataAPI with injectable, fully offline data access.

    file_map: {cycle_year: data_file_name or None}  (None -> no data file for the cycle)
    data_map: {cycle_year: pd.DataFrame}            (fabricated local "data files")
    fail_map: {cycle_year: Exception}               (reader raises for these cycles)
    """

    def __init__(self, file_map, data_map, fail_map=None):
        super().__init__()
        self._file_map = file_map
        self._data_map = data_map
        self._fail_map = fail_map or {}

    def _retrieve_variable_table(self, data_category):
        # Fabricate a variable table from the injected data frames so that
        # get_common_and_uncommon_variables works without network access.
        rows = []
        for cycle_year, df in self._data_map.items():
            for column in df.columns:
                rows.append({
                    "Variable Name": column,
                    "Data File Description": "Fake File",
                    "Data File Name": self._file_map.get(cycle_year),
                    "Years": cycle_year,
                })
        return pd.DataFrame(rows)

    def _get_data_filename(self, data_category, cycle_year, data_file_description):
        data_file_name = self._file_map.get(cycle_year)
        if data_file_name is None:
            raise ValueError(
                f"No data file found for Data Category: {data_category}, "
                f"Year: {cycle_year}, Data File Description: {data_file_description}"
            )
        return data_file_name

    def _read_cycle_data(self, cycle_year, data_file_name):
        if cycle_year in self._fail_map:
            raise self._fail_map[cycle_year]
        return self._data_map[cycle_year].copy()


def make_frame(seqn_offset=0, extra_columns=()):
    data = {"SEQN": [1 + seqn_offset, 2 + seqn_offset], "RIDAGEYR": [30, 40]}
    for i, column in enumerate(extra_columns):
        data[column] = [100 + i, 200 + i]
    return pd.DataFrame(data)


def statuses_by_cycle(report):
    return {entry["cycle"]: entry["status"] for entry in report}


class TestRetrieveDataMultiCycle(unittest.TestCase):
    def test_partial_failure_returns_successful_cycles(self):
        api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E", "2007-2008": "FAKE_F"},
            data_map={"2005-2006": make_frame(0), "2007-2008": make_frame(10)},
            fail_map={"2007-2008": IOError("simulated read failure")},
        )

        result = api.retrieve_data("examination", ["2005-2006", "2007-2008"], "Fake File")

        # Only the successful cycle's rows are returned, tagged by 'year'.
        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(result["year"].unique()), ["2005-2006"])
        self.assertEqual(sorted(result["SEQN"]), [1, 2])

        # Per-cycle settlement info is retrievable alongside the return value.
        report = api.get_last_cycle_report()
        self.assertIsNotNone(report)
        self.assertEqual([entry["cycle"] for entry in report], ["2005-2006", "2007-2008"])
        self.assertEqual(statuses_by_cycle(report),
                         {"2005-2006": "success", "2007-2008": "failed"})
        failed_entry = next(e for e in report if e["status"] == "failed")
        self.assertIn("simulated read failure", failed_entry["reason"])
        success_entry = next(e for e in report if e["status"] == "success")
        self.assertIsNone(success_entry["reason"])
        # Also attached to the returned DataFrame itself.
        self.assertEqual(result.attrs["cycle_report"], report)

    def test_cycle_without_data_file_is_skipped(self):
        api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E", "2007-2008": None},  # no file for 2007-2008
            data_map={"2005-2006": make_frame(0)},
        )

        result = api.retrieve_data("examination", ["2005-2006", "2007-2008"], "Fake File")

        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(result["year"].unique()), ["2005-2006"])

        report = api.get_last_cycle_report()
        self.assertEqual(statuses_by_cycle(report),
                         {"2005-2006": "success", "2007-2008": "skipped"})
        skipped_entry = next(e for e in report if e["status"] == "skipped")
        self.assertIn("No data file found", skipped_entry["reason"])

    def test_all_cycles_fail_raises_and_report_is_available(self):
        api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E", "2007-2008": None},
            data_map={"2005-2006": make_frame(0)},
            fail_map={"2005-2006": IOError("simulated read failure")},
        )

        with self.assertRaises(ValueError):
            api.retrieve_data("examination", ["2005-2006", "2007-2008"], "Fake File")

        # Even though the call raised, the settlement info is still accessible.
        report = api.get_last_cycle_report()
        self.assertIsNotNone(report)
        self.assertEqual(statuses_by_cycle(report),
                         {"2005-2006": "failed", "2007-2008": "skipped"})

    def test_common_variable_trimming_uses_successful_cycles(self):
        # 2005-2006 has EXTRA_A, 2007-2008 has EXTRA_B; 2009-2010 fails.
        # With include_uncommon_variables=False the merged frame must be trimmed
        # to variables common to the two *successful* cycles (SEQN, RIDAGEYR) + year.
        frame_a = make_frame(0, extra_columns=["EXTRA_A"])
        frame_b = make_frame(10, extra_columns=["EXTRA_B"])
        api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E", "2007-2008": "FAKE_F", "2009-2010": "FAKE_G"},
            data_map={"2005-2006": frame_a, "2007-2008": frame_b, "2009-2010": make_frame(20)},
            fail_map={"2009-2010": IOError("simulated read failure")},
        )

        result = api.retrieve_data("examination",
                                   ["2005-2006", "2007-2008", "2009-2010"],
                                   "Fake File",
                                   include_uncommon_variables=False)

        self.assertEqual(sorted(result.columns), ["RIDAGEYR", "SEQN", "year"])
        self.assertEqual(sorted(result["year"].unique()), ["2005-2006", "2007-2008"])
        self.assertEqual(len(result), 4)

        report = api.get_last_cycle_report()
        self.assertEqual(statuses_by_cycle(report),
                         {"2005-2006": "success", "2007-2008": "success", "2009-2010": "failed"})

    def test_single_cycle_behavior_unchanged(self):
        api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E"},
            data_map={"2005-2006": make_frame(0)},
        )

        result = api.retrieve_data("examination", "2005-2006", "Fake File")

        self.assertEqual(len(result), 2)
        self.assertEqual(sorted(result["year"].unique()), ["2005-2006"])
        self.assertEqual(sorted(result["SEQN"]), [1, 2])

        # Single-cycle read failure still propagates as before.
        failing_api = FakeNHANESDataAPI(
            file_map={"2005-2006": "FAKE_E"},
            data_map={"2005-2006": make_frame(0)},
            fail_map={"2005-2006": IOError("simulated read failure")},
        )
        with self.assertRaises(IOError):
            failing_api.retrieve_data("examination", "2005-2006", "Fake File")

    def test_invalid_cycle_still_raises(self):
        api = FakeNHANESDataAPI(file_map={}, data_map={})
        with self.assertRaises(ValueError):
            api.retrieve_data("examination", "2022-2023", "Fake File")


if __name__ == "__main__":
    unittest.main()
