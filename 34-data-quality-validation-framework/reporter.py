# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Validation Reporter — formats and outputs quality check results.

Accepts the structured output from validators.run_all_checks() and produces:
  - A grouped console report (by check category)
  - A summary verdict (PASS / FAIL with failure count)
  - An optional JSON report dict for downstream use (CI, Cloud Logging, etc.)
"""

import json
from datetime import datetime, timezone


def _section(title):
    print(f"\n== {title} ==")


def print_result(result):
    """Prints a single check result line with [PASS] / [FAIL] prefix."""
    status = result["status"]
    name = result["name"]
    detail = result.get("detail", "")
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")


def print_report(df_name, row_count, results, failures):
    """
    Renders the full validation report to stdout.

    Groups results by check category based on known check name prefixes.

    Args:
        df_name:   Name/path of the source file (for report header).
        row_count: Number of rows in the DataFrame.
        results:   List of check result dicts from validators.run_all_checks().
        failures:  List of failed check description strings.
    """
    print(f"\nvalidating {df_name}: {row_count} rows")

    # Group results by category
    categories = {
        "schema":       [],
        "completeness": [],
        "ranges":       [],
        "uniqueness":   [],
        "consistency":  [],
        "other":        [],
    }

    for r in results:
        name = r["name"]
        if name.startswith("column set") or name.startswith("dtype"):
            categories["schema"].append(r)
        elif name.startswith("no nulls"):
            categories["completeness"].append(r)
        elif any(name.startswith(p) for p in ["quantity", "unit_price", "order_date"]):
            categories["ranges"].append(r)
        elif "unique" in name:
            categories["uniqueness"].append(r)
        elif any(name.startswith(p) for p in ["region", "total_amount"]):
            categories["consistency"].append(r)
        else:
            categories["other"].append(r)

    label_map = {
        "schema": "schema",
        "completeness": "completeness",
        "ranges": "ranges",
        "uniqueness": "uniqueness",
        "consistency": "consistency",
        "other": "other checks",
    }

    for key, label in label_map.items():
        bucket = categories[key]
        if bucket:
            _section(label)
            for r in bucket:
                print_result(r)

    # Verdict
    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    print("\n" + "=" * 60)
    print(f"Checks : {total} total  |  {passed} passed  |  {len(failures)} failed")

    if failures:
        print(f"\nVALIDATION FAILED — {len(failures)} check(s):")
        for f in failures:
            print(f"  - {f}")
    else:
        print("\nVALIDATION PASSED — safe to hand off to the BQ load stage.")
    print("=" * 60)


def build_report_dict(df_name, row_count, results, failures):
    """
    Returns a structured dict suitable for JSON export, Cloud Logging, or CI.

    Args:
        df_name:   Source file name/path string.
        row_count: Number of rows validated.
        results:   List of check result dicts.
        failures:  List of failed check description strings.

    Returns:
        dict: Structured quality report with timestamp and verdict.
    """
    return {
        "source": str(df_name),
        "row_count": row_count,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS" if not failures else "FAIL",
        "total_checks": len(results),
        "passed_checks": sum(1 for r in results if r["status"] == "PASS"),
        "failed_checks": len(failures),
        "failures": failures,
        "checks": results,
    }


def save_report_json(report_dict, path):
    """
    Writes the report dict to a JSON file.

    Args:
        report_dict: Dict from build_report_dict().
        path:        File path (str or Path) to write.
    """
    with open(path, "w") as f:
        json.dump(report_dict, f, indent=2, default=str)
    print(f"\n  Report saved to: {path}")
