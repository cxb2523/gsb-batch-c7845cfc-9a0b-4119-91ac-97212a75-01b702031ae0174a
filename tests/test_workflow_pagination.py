#!/usr/bin/env python3
"""Unit tests for ds_list_workflows pagination continuation (fetch_all).

Covers:
- normal multi-page merging up to the reported total / empty page
- mid-pagination failure keeping already-fetched pages (no simple-list rollback)
- duplicate-code deduplication across pages
- name/search filters applied to the full merged collection
- use_simple branch remaining independent of fetch_all
- default (fetch_all=False) staying backward compatible
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dolphin_mcp_pilot.tools.workflow import register_workflow_tools


PROJECT_CODE = 10086


def _wf(code, name, description=""):
    return {
        "id": code,
        "code": code,
        "name": name,
        "releaseState": "OFFLINE",
        "description": description,
        "projectCode": PROJECT_CODE,
    }


def _page_response(items, total):
    return {"code": 0, "msg": "success", "data": {"total": total, "totalList": items}}


class _FakeMCP:
    """Minimal MCPServer stand-in that captures registered tool functions."""

    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


def _capture_tools():
    fake_mcp = _FakeMCP()
    register_workflow_tools(fake_mcp)
    return fake_mcp.tools


class ListWorkflowsFetchAllTest(unittest.TestCase):
    """fetch_all=True paginated continuation behavior."""

    def test_normal_multi_page_merge_until_total(self):
        """All pages are requested and merged until total is reached."""
        page1 = _page_response([_wf(1, "wf_a"), _wf(2, "wf_b")], 5)
        page2 = _page_response([_wf(3, "wf_c"), _wf(4, "wf_d")], 5)
        page3 = _page_response([_wf(5, "wf_e")], 5)

        calls = []

        def fake_ds_get(path):
            calls.append(path)
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            if "pageNo=3" in path:
                return page3
            raise AssertionError(f"unexpected page request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows(
                "demo_project", page_no=1, page_size=2, fetch_all=True
            )

        self.assertEqual(
            calls,
            [
                f"/projects/{PROJECT_CODE}/process-definition?pageNo=1&pageSize=2",
                f"/projects/{PROJECT_CODE}/process-definition?pageNo=2&pageSize=2",
                f"/projects/{PROJECT_CODE}/process-definition?pageNo=3&pageSize=2",
            ],
        )
        self.assertTrue(result["complete"])
        self.assertEqual(result["source"], "paginated")
        self.assertEqual(result["pages_fetched"], 3)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["deduped"], 0)
        self.assertIsNone(result["failed_page"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["page_size"], 2)
        self.assertEqual([w["code"] for w in result["workflows"]], [1, 2, 3, 4, 5])
        self.assertEqual(result["returned"], 5)

    def test_empty_page_terminates_continuation(self):
        """An empty page stops fetching and the result is marked complete."""
        # total claims more rows than exist; the empty next page ends the loop
        # instead of relying on the total threshold.
        page1 = _page_response([_wf(1, "wf_a")], 3)
        page2 = _page_response([], 3)

        def fake_ds_get(path):
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", page_size=10, fetch_all=True)

        self.assertTrue(result["complete"])
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual([w["code"] for w in result["workflows"]], [1])

    def test_page_failure_keeps_previous_pages_without_simple_fallback(self):
        """A non-zero code on page 3 stops continuation but keeps pages 1-2."""
        page1 = _page_response([_wf(1, "wf_a"), _wf(2, "wf_b")], 6)
        page2 = _page_response([_wf(3, "wf_c"), _wf(4, "wf_d")], 6)
        page3 = {"code": 10001, "msg": "internal server error", "data": None}

        requested_paths = []

        def fake_ds_get(path):
            requested_paths.append(path)
            if "simple-list" in path:
                raise AssertionError(
                    "fetch_all must not fall back to simple-list on page failure"
                )
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            if "pageNo=3" in path:
                return page3
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", page_size=2, fetch_all=True)

        self.assertFalse(result["complete"])
        self.assertEqual(result["failed_page"], 3)
        self.assertIn("internal server error", result["error"])
        self.assertIn("code=10001", result["error"])
        self.assertEqual(result["pages_fetched"], 2)
        self.assertEqual(result["total"], 6)
        # Previously merged entries survive the failure.
        self.assertEqual([w["code"] for w in result["workflows"]], [1, 2, 3, 4])
        self.assertTrue(
            all("simple-list" not in p for p in requested_paths),
            "no simple-list request should have been issued",
        )

    def test_page_request_exception_keeps_previous_pages(self):
        """A raised exception on a later page is reported, earlier pages retained."""
        page1 = _page_response([_wf(1, "wf_a"), _wf(2, "wf_b")], 5)

        def fake_ds_get(path):
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                raise ConnectionError("connection reset by peer")
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", page_size=2, fetch_all=True)

        self.assertFalse(result["complete"])
        self.assertEqual(result["failed_page"], 2)
        self.assertIn("connection reset by peer", result["error"])
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual([w["code"] for w in result["workflows"]], [1, 2])

    def test_first_page_failure_returns_empty_envelope(self):
        """Failure on the first page yields an empty but well-formed envelope."""
        page1 = {"code": 500, "msg": "boom", "data": None}

        def fake_ds_get(path):
            if "simple-list" in path:
                raise AssertionError("no simple-list fallback in fetch_all mode")
            return page1

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", fetch_all=True)

        self.assertFalse(result["complete"])
        self.assertEqual(result["failed_page"], 1)
        self.assertEqual(result["pages_fetched"], 0)
        self.assertEqual(result["workflows"], [])
        self.assertEqual(result["source"], "paginated")

    def test_duplicate_codes_deduped_first_occurrence_kept(self):
        """Repeated codes across pages are counted and only the first copy stays."""
        page1 = _page_response(
            [_wf(1, "wf_a"), _wf(2, "wf_b", description="first-copy")], 4
        )
        # Page 2 repeats code 2 (with different content) plus a new code 3.
        page2 = _page_response(
            [_wf(2, "wf_b_duplicate", description="second-copy"), _wf(3, "wf_c")],
            4,
        )
        page3 = _page_response([], 4)

        def fake_ds_get(path):
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            if "pageNo=3" in path:
                return page3
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", page_size=2, fetch_all=True)

        self.assertEqual(result["deduped"], 1)
        self.assertTrue(result["complete"])
        self.assertEqual([w["code"] for w in result["workflows"]], [1, 2, 3])
        # First occurrence wins.
        code2 = next(w for w in result["workflows"] if w["code"] == 2)
        self.assertEqual(code2["name"], "wf_b")
        self.assertEqual(code2["description"], "first-copy")

    def test_name_filter_applies_to_full_merged_collection(self):
        """Exact name match must see items beyond the first page."""
        page1 = _page_response([_wf(1, "alpha"), _wf(2, "beta")], 4)
        page2 = _page_response([_wf(3, "target_wf"), _wf(4, "gamma")], 4)

        def fake_ds_get(path):
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows(
                "demo_project", page_size=2, fetch_all=True, name="target_wf"
            )

        self.assertEqual([w["code"] for w in result["workflows"]], [3])
        self.assertEqual(result["returned"], 1)
        # All pages were still fetched before filtering.
        self.assertEqual(result["pages_fetched"], 2)

    def test_search_filter_applies_to_full_merged_collection(self):
        """Fuzzy keyword match runs against the merged set, not page 1 only."""
        page1 = _page_response(
            [_wf(1, "alpha", description="plain"), _wf(2, "beta")], 4
        )
        page2 = _page_response(
            [_wf(3, "gamma", description="contains KEYWORD here"), _wf(4, "delta")],
            4,
        )

        def fake_ds_get(path):
            if "pageNo=1" in path:
                return page1
            if "pageNo=2" in path:
                return page2
            raise AssertionError(f"unexpected request: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows(
                "demo_project", page_size=2, fetch_all=True, search="KeYwOrD"
            )

        self.assertEqual([w["code"] for w in result["workflows"]], [3])


class ListWorkflowsCompatibilityTest(unittest.TestCase):
    """Default and simple-list branch behavior."""

    def test_default_returns_bare_list_single_page(self):
        """fetch_all defaults to False and preserves the old list return shape."""
        single_page = _page_response([_wf(1, "wf_a"), _wf(2, "wf_b")], 2)

        def fake_ds_get(path):
            return single_page

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project")

        self.assertIsInstance(result, list)
        self.assertEqual([w["code"] for w in result], [1, 2])

    def test_default_still_falls_back_to_simple_list_on_failure(self):
        """The historical auto-fallback path is untouched."""
        simple = {
            "code": 0,
            "data": [_wf(9, "simple_wf")],
        }

        def fake_ds_get(path):
            if "simple-list" in path:
                return simple
            return {"code": 10001, "msg": "pagination broken", "data": None}

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project")

        self.assertIsInstance(result, list)
        self.assertEqual([w["code"] for w in result], [9])

    def test_use_simple_ignores_fetch_all_and_uses_simple_branch(self):
        """use_simple=True always takes the simple-list branch."""
        simple = {
            "code": 0,
            "data": [_wf(7, "simple_wf"), _wf(8, "other_wf")],
        }
        requested = []

        def fake_ds_get(path):
            requested.append(path)
            if "simple-list" in path:
                return simple
            raise AssertionError(f"paginated endpoint must not be called: {path}")

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", use_simple=True, fetch_all=True)

        self.assertEqual(result["source"], "simple-list")
        self.assertTrue(result["complete"])
        self.assertEqual(result["pages_fetched"], 1)
        self.assertEqual([w["code"] for w in result["workflows"]], [7, 8])
        self.assertTrue(all("pageNo=" not in p for p in requested))

    def test_use_simple_without_fetch_all_still_returns_bare_list(self):
        simple = {"code": 0, "data": [_wf(7, "simple_wf")]}

        def fake_ds_get(path):
            return simple

        with patch(
            "dolphin_mcp_pilot.tools.workflow.resolve_project_code",
            return_value=PROJECT_CODE,
        ), patch(
            "dolphin_mcp_pilot.tools.workflow.ds_get", side_effect=fake_ds_get
        ):
            list_workflows = _capture_tools()["ds_list_workflows"]
            result = list_workflows("demo_project", use_simple=True)

        self.assertIsInstance(result, list)
        self.assertEqual([w["code"] for w in result], [7])


if __name__ == "__main__":
    unittest.main(verbosity=2)
