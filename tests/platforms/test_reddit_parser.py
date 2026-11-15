"""Tests for Reddit platform parser."""

import csv
import json
import os
from pathlib import Path

import pytest
import yaml

from social_data_pipeline.core.parser import (
    ParseSettlement,
    ParseFailureError,
    SETTLEMENT_STATUS_SUCCESS,
    SETTLEMENT_STATUS_PARTIAL,
    SETTLEMENT_STATUS_FAILED,
    SETTLEMENT_SUFFIX,
    settlement_file_path,
)
from social_data_pipeline.platforms.reddit.parser import (
    base36_to_int,
    determine_removal_status,
    get_all_columns,
    transform_json,
    parse_to_csv,
    parse_file_to_settlement,
    parse_files_parallel,
    process_single_file,
)


# ============================================================================
# base36_to_int
# ============================================================================


class TestBase36ToInt:
    def test_simple_value(self):
        assert base36_to_int("1a") == 46

    def test_letters_only(self):
        assert base36_to_int("abc") == 13368

    def test_empty_string(self):
        assert base36_to_int("") is None

    def test_none(self):
        assert base36_to_int(None) is None

    def test_invalid_characters(self):
        assert base36_to_int("!!!") is None

    def test_zero(self):
        assert base36_to_int("0") == 0

    def test_large_value(self):
        assert base36_to_int("zzzzzz") == 2176782335

    def test_single_digit(self):
        assert base36_to_int("z") == 35

    def test_mixed_case(self):
        # int(value, 36) is case-insensitive
        assert base36_to_int("ABC") == base36_to_int("abc")


# ============================================================================
# determine_removal_status
# ============================================================================


class TestDetermineRemovalStatus:
    """Test the waterfall algorithm priority levels."""

    def test_priority1_meta_removal_type(self):
        data = {"_meta": {"removal_type": "moderator"}}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority1_meta_removal_type_passthrough(self):
        data = {"_meta": {"removal_type": "content_takedown"}}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "content_takedown"

    def test_priority2_was_deleted_later_with_no_type(self):
        """was_deleted_later alone defaults to 'deleted'."""
        data = {"_meta": {"was_deleted_later": True}}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "deleted"

    def test_priority2_was_deleted_later_with_later_priority(self):
        """was_deleted_later marks deleted, but type comes from later priority."""
        data = {"_meta": {"was_deleted_later": True}, "spam": True}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority3_removed_by_category_deleted(self):
        data = {"removed_by_category": "deleted"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "deleted"

    def test_priority3_removed_by_category_author(self):
        data = {"removed_by_category": "author"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "deleted"

    def test_priority3_removed_by_category_moderator(self):
        data = {"removed_by_category": "moderator"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority3_removed_by_category_anti_evil_ops(self):
        data = {"removed_by_category": "anti_evil_ops"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority3_removed_by_category_admin(self):
        data = {"removed_by_category": "admin"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority3_removed_by_category_automod_filtered(self):
        data = {"removed_by_category": "automod_filtered"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "automod_filtered"

    def test_priority3_removed_by_category_unknown(self):
        data = {"removed_by_category": "some_new_category"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority4_spam_true(self):
        data = {"spam": True}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority4_spam_false_skipped(self):
        """spam=False should not trigger."""
        data = {"spam": False}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is False
        assert removal_type == ""

    def test_priority5_removed_true(self):
        data = {"removed": True}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority6_banned_by_true(self):
        data = {"banned_by": True}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority6_banned_by_string_true(self):
        data = {"banned_by": "true"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "reddit"

    def test_priority6_banned_by_automoderator(self):
        data = {"banned_by": "AutoModerator"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "automod_filtered"

    def test_priority6_banned_by_other_string(self):
        data = {"banned_by": "some_mod_user"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority6_banned_by_skipped_values(self):
        """None, empty string, and False should be skipped."""
        for value in [None, "", False]:
            data = {"banned_by": value}
            is_deleted, removal_type = determine_removal_status(data)
            assert is_deleted is False, f"banned_by={value!r} should not trigger"

    def test_priority7_body_removed(self):
        data = {"body": "[removed]"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "moderator"

    def test_priority7_selftext_deleted(self):
        data = {"selftext": "[deleted]"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "deleted"

    def test_priority8_author_deleted(self):
        data = {"author": "[deleted]"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is True
        assert removal_type == "deleted"

    def test_not_removed(self):
        data = {"author": "normal_user", "body": "a normal comment"}
        is_deleted, removal_type = determine_removal_status(data)
        assert is_deleted is False
        assert removal_type == ""

    def test_priority_ordering_p1_wins_over_p3(self):
        """Priority 1 (_meta.removal_type) wins over priority 3 (removed_by_category)."""
        data = {
            "_meta": {"removal_type": "content_takedown"},
            "removed_by_category": "moderator",
        }
        is_deleted, removal_type = determine_removal_status(data)
        assert removal_type == "content_takedown"

    def test_priority_ordering_p3_wins_over_p7(self):
        """Priority 3 (removed_by_category) wins over text markers."""
        data = {
            "removed_by_category": "reddit",
            "body": "[deleted]",
        }
        is_deleted, removal_type = determine_removal_status(data)
        assert removal_type == "reddit"


# ============================================================================
# get_all_columns
# ============================================================================


class TestGetAllColumns:
    def test_basic(self):
        # retrieved_utc lives in YAML fields:, not parser MANDATORY_FIELDS
        # (so the DB column stays nullable for old Arctic Shift dumps).
        result = get_all_columns("comments", ["retrieved_utc", "author", "body", "score"])
        assert result == ["dataset", "id", "retrieved_utc", "author", "body", "score"]

    def test_empty_fields(self):
        result = get_all_columns("submissions", [])
        assert result == ["dataset", "id"]


# ============================================================================
# transform_json
# ============================================================================


class TestTransformJson:
    def test_basic_comment(self):
        data = {
            "id": "abc123",
            "author": "TestUser",
            "body": "Hello world",
            "subreddit": "Python",
            "score": 42,
            "created_utc": 1704067200,
            "retrieved_utc": 1704153600,
        }
        field_types = {"score": "integer", "created_utc": "integer", "author": "text", "body": "text", "subreddit": "text"}
        # retrieved_utc is now a regular YAML field (not parser-mandatory)
        # so callers must list it explicitly to keep it in the column list.
        fields = ["retrieved_utc", "created_utc", "author", "body", "subreddit", "score"]
        result = transform_json(data, "2024-01", field_types, fields)
        # [dataset, id, retrieved_utc, created_utc, author, body, subreddit, score]
        assert result[0] == "2024-01"
        assert result[1] == "abc123"  # id (truncated by char(7) -> "abc123" stays <= 7)
        assert result[2] == 1704153600  # retrieved_utc
        assert result[3] == 1704067200  # created_utc
        assert result[4] == "testuser"  # author lowercased
        assert result[6] == "python"  # subreddit lowercased

    def test_retrieved_utc_fallback_from_retrieved_on(self):
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_on": 12345,
        }
        field_types = {"author": "text", "subreddit": "text", "retrieved_utc": "integer"}
        result = transform_json(data, "2024-01", field_types, ["retrieved_utc", "author"])
        # retrieved_utc should come from retrieved_on
        assert result[2] == 12345

    def test_meta_retrieved_2nd_on(self):
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_utc": 11111,
            "_meta": {"retrieved_2nd_on": 22222},
        }
        field_types = {"author": "text", "subreddit": "text", "retrieved_utc": "integer"}
        result = transform_json(data, "2024-01", field_types, ["retrieved_utc", "author"])
        # retrieved_utc overridden by _meta.retrieved_2nd_on
        assert result[2] == 22222

    def test_retrieved_on_fallback_recorded_on_tracker(self):
        tracker = ParseSettlement(input_file="RC_x")
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_on": 12345,
        }
        field_types = {"author": "text", "subreddit": "text", "retrieved_utc": "integer"}
        result = transform_json(
            data, "2024-01", field_types, ["retrieved_utc", "author"],
            fallback_tracker=tracker,
        )
        assert result[2] == 12345
        assert tracker.used_retrieved_on_fallback is True
        assert tracker.retrieved_on_fallback_rows == 1
        assert tracker.used_retrieved_2nd_on_fallback is False

    def test_2nd_on_fallback_recorded_on_tracker(self):
        tracker = ParseSettlement(input_file="RS_x")
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_utc": 11111,
            "_meta": {"retrieved_2nd_on": 22222},
        }
        field_types = {"author": "text", "subreddit": "text", "retrieved_utc": "integer"}
        result = transform_json(
            data, "2024-01", field_types, ["retrieved_utc", "author"],
            fallback_tracker=tracker,
        )
        assert result[2] == 22222
        assert tracker.used_retrieved_2nd_on_fallback is True
        assert tracker.retrieved_2nd_on_fallback_rows == 1
        # retrieved_utc was present, so the legacy retrieved_on path is unused
        assert tracker.used_retrieved_on_fallback is False

    def test_no_fallback_recorded_when_retrieved_utc_present(self):
        tracker = ParseSettlement(input_file="RC_x")
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_utc": 999,
        }
        field_types = {"author": "text", "subreddit": "text", "retrieved_utc": "integer"}
        transform_json(
            data, "2024-01", field_types, ["retrieved_utc", "author"],
            fallback_tracker=tracker,
        )
        assert tracker.used_retrieved_on_fallback is False
        assert tracker.used_retrieved_2nd_on_fallback is False
        assert tracker.retrieved_on_fallback_rows == 0
        assert tracker.retrieved_2nd_on_fallback_rows == 0

    def test_no_tracker_leaves_output_unchanged(self):
        """Without a tracker the output values must be byte-identical."""
        data = {"id": "abc", "retrieved_on": 12345, "_meta": {"retrieved_2nd_on": 22222}}
        field_types = {"retrieved_utc": "integer"}
        result = transform_json(data, "2024-01", field_types, ["retrieved_utc"])
        assert result[2] == 22222

    def test_id10_computation(self):
        data = {
            "id": "1a",
            "author": "user",
            "subreddit": "test",
            "retrieved_utc": 1000,
        }
        field_types = {"id10": "bigint", "author": "text", "subreddit": "text"}
        result = transform_json(data, "2024-01", field_types, ["id10"])
        # [dataset, id, id10] — retrieved_utc not requested, so not emitted.
        # id10 = base36_to_int("1a") = 46
        assert result[2] == 46

    def test_is_deleted_and_removal_type_text(self):
        data = {
            "id": "abc",
            "author": "user",
            "subreddit": "test",
            "retrieved_utc": 1000,
            "spam": True,
        }
        field_types = {"is_deleted": "text", "removal_type": "text", "author": "text", "subreddit": "text"}
        result = transform_json(data, "2024-01", field_types, ["is_deleted", "removal_type"])
        # [dataset, id, is_deleted, removal_type] — spam=True -> (True, 'reddit').
        assert result[2] == "True"  # is_deleted as text
        assert result[3] == "reddit"  # removal_type as text


# ============================================================================
# parse_to_csv end-to-end: comments to CSV
# ============================================================================


class TestParseToCSV:
    def test_parse_comments_csv(self, config_fixtures_dir, reddit_fixtures_dir, tmp_path):
        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        platform_config["file_format"] = "csv"
        input_file = str(reddit_fixtures_dir / "RC_2024-01.ndjson")

        # Count input lines
        with open(input_file) as f:
            expected_rows = sum(1 for line in f if line.strip())

        output_path = parse_to_csv(
            input_file=input_file,
            output_dir=str(tmp_path),
            data_type="comments",
            platform_config=platform_config,
        )

        assert os.path.exists(output_path)
        with open(output_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)

        assert "dataset" in header
        assert "id" in header
        assert "retrieved_utc" in header
        assert len(rows) == expected_rows
        # Check no empty ids
        id_idx = header.index("id")
        for row in rows:
            assert row[id_idx] != ""

    def test_parse_submissions_parquet(self, config_fixtures_dir, reddit_fixtures_dir, tmp_path):
        import polars as pl

        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        platform_config["file_format"] = "parquet"
        input_file = str(reddit_fixtures_dir / "RS_2024-01.ndjson")

        with open(input_file) as f:
            expected_rows = sum(1 for line in f if line.strip())

        output_path = parse_to_csv(
            input_file=input_file,
            output_dir=str(tmp_path),
            data_type="submissions",
            platform_config=platform_config,
        )

        assert os.path.exists(output_path)
        assert output_path.endswith(".parquet")
        df = pl.read_parquet(output_path)
        assert len(df) == expected_rows
        assert "dataset" in df.columns
        assert "id" in df.columns
        assert "retrieved_utc" in df.columns
        # Check submission-specific fields present
        assert "title" in df.columns
        assert "selftext" in df.columns

    def test_parse_comments_parquet_row_count(self, config_fixtures_dir, reddit_fixtures_dir, tmp_path):
        import polars as pl

        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        platform_config["file_format"] = "parquet"
        input_file = str(reddit_fixtures_dir / "RC_2024-01.ndjson")

        with open(input_file) as f:
            expected_rows = sum(1 for line in f if line.strip())

        output_path = parse_to_csv(
            input_file=input_file,
            output_dir=str(tmp_path),
            data_type="comments",
            platform_config=platform_config,
        )

        df = pl.read_parquet(output_path)
        assert len(df) == expected_rows

    def test_parse_no_field_types_raises(self, tmp_path):
        from social_data_pipeline.core.config import ConfigurationError

        platform_config = {
            "fields": {"comments": ["author"]},
            "field_types": {},
        }
        with pytest.raises(ConfigurationError, match="No field_types"):
            parse_to_csv(
                input_file="dummy",
                output_dir=str(tmp_path),
                data_type="comments",
                platform_config=platform_config,
            )

    def test_parse_no_fields_for_data_type_raises(self, tmp_path):
        from social_data_pipeline.core.config import ConfigurationError

        platform_config = {
            "field_types": {"author": "text"},
            "fields": {},
        }
        with pytest.raises(ConfigurationError, match="No fields configured"):
            parse_to_csv(
                input_file="dummy",
                output_dir=str(tmp_path),
                data_type="comments",
                platform_config=platform_config,
            )


# ============================================================================
# process_single_file
# ============================================================================


class TestProcessSingleFile:
    def test_csv_output_uses_temp_rename(self, reddit_fixtures_dir, config_fixtures_dir, tmp_path):
        """Verify that the final CSV file exists (temp was renamed)."""
        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        input_file = str(reddit_fixtures_dir / "RC_2024-01.ndjson")
        output_file = str(tmp_path / "comments" / "RC_2024-01.csv")
        (tmp_path / "comments").mkdir()

        settlement = process_single_file(
            input_file=input_file,
            output_file=output_file,
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="csv",
        )

        out_path = settlement.output_path
        assert os.path.exists(out_path)
        assert settlement.input_size > 0
        assert settlement.status == "success"
        assert settlement.bad_rows == 0
        # Temp file should not exist
        assert not os.path.exists(output_file + ".temp")

    def test_parquet_output(self, reddit_fixtures_dir, config_fixtures_dir, tmp_path):
        import polars as pl

        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        input_file = str(reddit_fixtures_dir / "RC_2024-01.ndjson")
        output_file = str(tmp_path / "RC_2024-01.parquet")

        process_single_file(
            input_file=input_file,
            output_file=output_file,
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="parquet",
        )

        df = pl.read_parquet(output_file)
        assert len(df) == 10
        assert "dataset" in df.columns


# ============================================================================
# Settlement records: per-file results, bad lines, failures, fallbacks
# ============================================================================


def _write_ndjson(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestSettlementBadLines:
    def _config(self, config_fixtures_dir):
        return yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )

    def test_bad_lines_skipped_and_counted_csv(self, config_fixtures_dir, tmp_path):
        platform_config = self._config(config_fixtures_dir)
        platform_config["file_format"] = "csv"
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })
        input_path = tmp_path / "RC_2024-09"
        _write_ndjson(input_path, [good, "{not valid json", good, "   ", "{also broken"])

        settlement = process_single_file(
            input_file=str(input_path),
            output_file=str(tmp_path / "out.csv"),
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="csv",
        )

        assert settlement.status == SETTLEMENT_STATUS_PARTIAL
        assert settlement.success_rows == 2
        assert settlement.bad_rows == 2
        # Output contains header + 2 good rows only
        with open(settlement.output_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert len(rows) == 3
        # Bad-line samples retained for auditing
        assert len(settlement.error_samples) == 2
        assert {s["line"] for s in settlement.error_samples} == {2, 5}
        assert all(s["error"] == "JSONDecodeError" for s in settlement.error_samples)

    def test_bad_lines_settlement_written_by_parse_to_csv(
        self, config_fixtures_dir, tmp_path
    ):
        platform_config = self._config(config_fixtures_dir)
        platform_config["file_format"] = "csv"
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })
        input_path = tmp_path / "RC_2024-09"
        _write_ndjson(input_path, [good, "broken{"])

        parse_to_csv(
            input_file=str(input_path),
            output_dir=str(tmp_path / "parsed"),
            data_type="comments",
            platform_config=platform_config,
        )

        settlement_path = settlement_file_path(
            tmp_path / "parsed" / "comments", "RC_2024-09"
        )
        assert settlement_path.exists()
        record = json.loads(settlement_path.read_text(encoding="utf-8"))
        assert record["input_file"] == "RC_2024-09"
        assert record["status"] == SETTLEMENT_STATUS_PARTIAL
        assert record["success_rows"] == 1
        assert record["bad_rows"] == 1
        assert record["output_path"].endswith("RC_2024-09.csv")
        assert "used_retrieved_on_fallback" in record
        assert "used_retrieved_2nd_on_fallback" in record
        assert record["settlement_path"].endswith(SETTLEMENT_SUFFIX)

    def test_bad_lines_parquet(self, config_fixtures_dir, tmp_path):
        import polars as pl

        platform_config = self._config(config_fixtures_dir)
        platform_config["file_format"] = "parquet"
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })
        input_path = tmp_path / "RC_2024-09"
        _write_ndjson(input_path, [good, "broken", good])

        settlement = process_single_file(
            input_file=str(input_path),
            output_file=str(tmp_path / "out.parquet"),
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="parquet",
        )

        assert settlement.status == SETTLEMENT_STATUS_PARTIAL
        assert settlement.success_rows == 2
        assert settlement.bad_rows == 1
        df = pl.read_parquet(settlement.output_path)
        assert len(df) == 2


class TestSettlementFallbacks:
    def test_legacy_retrieved_on_file_flags_fallback(self, config_fixtures_dir, tmp_path):
        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        platform_config["file_format"] = "csv"
        # Old-format rows: no retrieved_utc, only retrieved_on
        rows = [
            json.dumps({"id": "a1", "author": "u", "subreddit": "s", "retrieved_on": 100}),
            json.dumps({"id": "a2", "author": "u", "subreddit": "s", "retrieved_on": 200}),
        ]
        input_path = tmp_path / "RS_2006-03"
        _write_ndjson(input_path, rows)

        settlement = parse_file_to_settlement(
            input_file=str(input_path),
            output_dir=str(tmp_path / "parsed"),
            data_type="submissions",
            platform_config=platform_config,
        )

        assert settlement.status == SETTLEMENT_STATUS_SUCCESS
        assert settlement.used_retrieved_on_fallback is True
        assert settlement.retrieved_on_fallback_rows == 2
        assert settlement.used_retrieved_2nd_on_fallback is False
        record = json.loads(Path(settlement.settlement_path).read_text(encoding="utf-8"))
        assert record["used_retrieved_on_fallback"] is True
        assert record["retrieved_on_fallback_rows"] == 2

    def test_new_format_2nd_on_file_flags_fallback(self, config_fixtures_dir, tmp_path):
        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        platform_config["file_format"] = "csv"
        rows = [
            json.dumps({
                "id": "a1", "author": "u", "subreddit": "s",
                "retrieved_utc": 100, "_meta": {"retrieved_2nd_on": 300},
            }),
        ]
        input_path = tmp_path / "RS_2024-09"
        _write_ndjson(input_path, rows)

        settlement = parse_file_to_settlement(
            input_file=str(input_path),
            output_dir=str(tmp_path / "parsed"),
            data_type="submissions",
            platform_config=platform_config,
        )

        assert settlement.used_retrieved_2nd_on_fallback is True
        assert settlement.retrieved_2nd_on_fallback_rows == 1
        assert settlement.used_retrieved_on_fallback is False

    def test_mixed_fixture_flags_both_fallbacks(
        self, config_fixtures_dir, reddit_fixtures_dir, tmp_path
    ):
        # RC_2024-01 intentionally mixes eras: one legacy row backfilled from
        # retrieved_on, one new-format row using _meta.retrieved_2nd_on.
        platform_config = yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )
        settlement = parse_file_to_settlement(
            input_file=str(reddit_fixtures_dir / "RC_2024-01.ndjson"),
            output_dir=str(tmp_path / "parsed"),
            data_type="comments",
            platform_config=platform_config,
        )
        assert settlement.status == SETTLEMENT_STATUS_SUCCESS
        assert settlement.used_retrieved_on_fallback is True
        assert settlement.retrieved_on_fallback_rows == 1
        assert settlement.used_retrieved_2nd_on_fallback is True
        assert settlement.retrieved_2nd_on_fallback_rows == 1


class TestSettlementFailures:
    def _config(self, config_fixtures_dir):
        return yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )

    def test_unopenable_input_returns_failed_settlement(self, config_fixtures_dir, tmp_path):
        platform_config = self._config(config_fixtures_dir)
        missing = tmp_path / "RC_does_not_exist"
        settlement = process_single_file(
            input_file=str(missing),
            output_file=str(tmp_path / "out.csv"),
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="csv",
        )
        assert settlement.status == SETTLEMENT_STATUS_FAILED
        assert settlement.error
        assert not os.path.exists(str(tmp_path / "out.csv"))
        assert not os.path.exists(str(tmp_path / "out.csv.temp"))

    def test_parse_to_csv_raises_failure_and_writes_settlement(
        self, config_fixtures_dir, tmp_path
    ):
        platform_config = self._config(config_fixtures_dir)
        missing = tmp_path / "RC_missing"
        with pytest.raises(ParseFailureError) as exc_info:
            parse_to_csv(
                input_file=str(missing),
                output_dir=str(tmp_path / "parsed"),
                data_type="comments",
                platform_config=platform_config,
            )
        assert exc_info.value.settlement.status == SETTLEMENT_STATUS_FAILED
        # A settlement JSON must still exist for the failed file
        settlement_path = settlement_file_path(
            tmp_path / "parsed" / "comments", "RC_missing"
        )
        assert settlement_path.exists()
        record = json.loads(settlement_path.read_text(encoding="utf-8"))
        assert record["status"] == SETTLEMENT_STATUS_FAILED
        assert record["input_file"] == "RC_missing"

    def test_write_failure_cleans_temp_and_marks_failed(
        self, config_fixtures_dir, tmp_path, monkeypatch
    ):
        platform_config = self._config(config_fixtures_dir)
        input_path = tmp_path / "RC_2024-09"
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })
        _write_ndjson(input_path, [good, good])

        real_open = open

        def flaky_open(path, *args, **kwargs):
            # Fail when opening the CSV temp output for writing.
            if str(path).endswith(".temp") and "w" in (args[0] if args else kwargs.get("mode", "")):
                raise OSError("simulated write failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(
            "social_data_pipeline.platforms.reddit.parser.open", flaky_open, raising=False
        )

        settlement = process_single_file(
            input_file=str(input_path),
            output_file=str(tmp_path / "out.csv"),
            data_type="comments",
            data_type_config=platform_config["field_types"],
            fields_to_extract=platform_config["fields"]["comments"],
            file_format="csv",
        )
        assert settlement.status == SETTLEMENT_STATUS_FAILED
        assert "simulated write failure" in settlement.error
        assert not os.path.exists(str(tmp_path / "out.csv"))
        assert not os.path.exists(str(tmp_path / "out.csv.temp"))


class TestParseFilesParallel:
    def _config(self, config_fixtures_dir):
        return yaml.safe_load(
            (config_fixtures_dir / "valid_platform_reddit.yaml").read_text()
        )

    def test_mixed_success_partial_failure_returns_all(
        self, config_fixtures_dir, tmp_path
    ):
        platform_config = self._config(config_fixtures_dir)
        platform_config["file_format"] = "csv"
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })

        clean = tmp_path / "RC_clean"
        partial = tmp_path / "RC_partial"
        missing = tmp_path / "RC_missing"
        _write_ndjson(clean, [good, good])
        _write_ndjson(partial, [good, "broken", good])
        # `missing` is deliberately never created

        results = parse_files_parallel(
            files=[
                (str(clean), "comments"),
                (str(partial), "comments"),
                (str(missing), "comments"),
            ],
            output_dir=str(tmp_path / "parsed"),
            platform_config=platform_config,
            workers=2,
        )

        assert len(results) == 3
        statuses = [r.status for r in results]
        assert statuses == [
            SETTLEMENT_STATUS_SUCCESS,
            SETTLEMENT_STATUS_PARTIAL,
            SETTLEMENT_STATUS_FAILED,
        ]
        by_name = {r.input_file: r for r in results}
        assert by_name["RC_clean"].success_rows == 2
        assert by_name["RC_clean"].bad_rows == 0
        assert by_name["RC_partial"].success_rows == 2
        assert by_name["RC_partial"].bad_rows == 1
        assert by_name["RC_missing"].error

        # Each input file gets its own uniquely named settlement JSON —
        # no parallel overwrites.
        comments_dir = tmp_path / "parsed" / "comments"
        settlement_files = sorted(p.name for p in comments_dir.glob(f"*{SETTLEMENT_SUFFIX}"))
        assert settlement_files == [
            "RC_clean.settlement.json",
            "RC_missing.settlement.json",
            "RC_partial.settlement.json",
        ]
        for name in ("RC_clean", "RC_partial"):
            assert os.path.exists(by_name[name].output_path)
        # The failed file must not leave a half-finished output behind
        assert not os.path.exists(by_name["RC_missing"].output_path)

    def test_empty_input_list(self):
        assert parse_files_parallel([], "/tmp/whatever", {}, workers=2) == []

    def test_parallel_settlements_match_sequential_output(
        self, config_fixtures_dir, tmp_path
    ):
        """CSV content/column order must be unchanged by the rework."""
        platform_config = self._config(config_fixtures_dir)
        good = json.dumps({
            "id": "abc1234", "author": "u", "body": "hi", "subreddit": "x",
            "created_utc": 1, "retrieved_utc": 2,
        })
        f1 = tmp_path / "RC_one"
        f2 = tmp_path / "RC_two"
        _write_ndjson(f1, [good])
        _write_ndjson(f2, [good, good])

        results = parse_files_parallel(
            files=[(str(f1), "comments"), (str(f2), "comments")],
            output_dir=str(tmp_path / "parsed"),
            platform_config=platform_config,
            workers=2,
        )
        for settlement, expected_rows in zip(results, (1, 2)):
            assert settlement.status == SETTLEMENT_STATUS_SUCCESS
            with open(settlement.output_path, newline="", encoding="utf-8") as fh:
                reader = csv.reader(fh)
                header = next(reader)
                rows = list(reader)
            assert header[0:2] == ["dataset", "id"]
            assert "retrieved_utc" in header
            assert len(rows) == expected_rows

