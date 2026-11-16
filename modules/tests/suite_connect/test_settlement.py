import os
import sys
import unittest

# ``suite_connect`` lives under ``modules/`` (on PYTHONPATH inside Airflow).
_MODULES_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _MODULES_DIR not in sys.path:
    sys.path.insert(0, _MODULES_DIR)

from suite_connect.settlement import (
    FAILED,
    NO_DATA,
    SUCCESS,
    build_status,
    format_summary,
    has_failures,
    settle_subquery,
    summarize_subquery_statuses,
)


def field_values(search_id='customsearch_vendor'):
    return dict(
        task_id=f'extract_{search_id}',
        search_type='vendor',
        search_id=search_id,
        filename='netsuite_extracts_20240101',
        bucket_name='finance-data-lake-landing',
    )


def status(search_id, state, records=None, error=None):
    return build_status(
        **field_values(search_id),
        status=state,
        records=records,
        error=error,
    )


class TestSettleSubquery(unittest.TestCase):
    """Empty / non-empty / export-error settlement for a single subquery."""

    def test_empty_list_settles_as_no_data_without_exporting(self):
        exported = []

        settled, error = settle_subquery(
            extract=lambda: [],
            export=exported.append,
            field_values=field_values(),
        )

        self.assertEqual(settled['status'], NO_DATA)
        self.assertEqual(settled['records'], 0)
        self.assertIsNone(settled['error'])
        self.assertIsNone(error)
        self.assertEqual(exported, [])

    def test_non_empty_list_is_exported_once_and_settles_as_success(self):
        rows = [{'id': '1'}, {'id': '2'}, {'id': '3'}]
        exported = []

        settled, error = settle_subquery(
            extract=lambda: rows,
            export=exported.append,
            field_values=field_values(),
        )

        self.assertEqual(settled['status'], SUCCESS)
        self.assertEqual(settled['records'], 3)
        self.assertIsNone(error)
        self.assertEqual(exported, [rows])

    def test_export_error_is_recorded_and_returned(self):
        def export(_):
            raise OSError('landing bucket unavailable')

        settled, error = settle_subquery(
            extract=lambda: [{'id': '1'}],
            export=export,
            field_values=field_values(),
        )

        self.assertEqual(settled['status'], FAILED)
        self.assertIn('landing bucket unavailable', settled['error'])
        self.assertIn('OSError', settled['error'])
        self.assertIsInstance(error, OSError)

    def test_extraction_error_is_recorded_and_returned(self):
        def extract():
            raise ConnectionError('token expired')

        settled, error = settle_subquery(
            extract=extract,
            export=lambda rows: rows,
            field_values=field_values(),
        )

        self.assertEqual(settled['status'], FAILED)
        self.assertIn('token expired', settled['error'])
        self.assertIsInstance(error, ConnectionError)

    def test_none_extraction_is_a_failure_not_empty(self):
        exported = []

        settled, error = settle_subquery(
            extract=lambda: None,
            export=exported.append,
            field_values=field_values(),
        )

        self.assertEqual(settled['status'], FAILED)
        self.assertEqual(exported, [])
        self.assertIsNotNone(error)

    def test_one_failed_subquery_does_not_block_the_others(self):
        calls = []

        def export(key):
            def inner(rows):
                calls.append(key)
                if key == 'customer':
                    raise RuntimeError('export boom')
            return inner

        rows = {
            'gl': [{'id': '1'}],
            'vendor': [],
            'customer': [{'id': '2'}],
            'currency': [{'id': '3'}],
        }
        outcomes = {}
        for key in ('gl', 'vendor', 'customer', 'currency'):
            value = rows[key]
            outcomes[key], _ = settle_subquery(
                extract=(lambda result: (lambda: result))(value),
                export=export(key),
                field_values=field_values(search_id=f'customsearch_{key}'),
            )

        self.assertEqual(outcomes['gl']['status'], SUCCESS)
        self.assertEqual(outcomes['vendor']['status'], NO_DATA)
        self.assertEqual(outcomes['customer']['status'], FAILED)
        self.assertEqual(outcomes['currency']['status'], SUCCESS)
        # The empty subquery never reached the exporter; the failed one did,
        # and the subquery after it still exported.
        self.assertEqual(calls, ['gl', 'customer', 'currency'])


class TestSummary(unittest.TestCase):
    def test_three_way_summary_counts_each_category(self):
        summary = summarize_subquery_statuses([
            status('customsearch_gl', SUCCESS, records=4),
            status('customsearch_vendor', NO_DATA, records=0),
            status('customsearch_customer', FAILED, error='OSError: boom'),
        ])

        self.assertEqual(summary['counts'][SUCCESS], 1)
        self.assertEqual(summary['counts'][NO_DATA], 1)
        self.assertEqual(summary['counts'][FAILED], 1)
        self.assertEqual(summary['counts']['missing'], 0)
        self.assertEqual(summary[SUCCESS], ['customsearch_gl'])
        self.assertEqual(summary[NO_DATA], ['customsearch_vendor'])
        self.assertEqual(summary[FAILED], ['customsearch_customer'])
        self.assertEqual(summary['errors']['customsearch_customer'], 'OSError: boom')
        self.assertEqual(summary['records'][SUCCESS], 4)

    def test_missing_status_is_reported_as_incomplete(self):
        summary = summarize_subquery_statuses(
            [status('customsearch_gl', SUCCESS), None],
            task_ids=['extract_gl', 'extract_vendor'],
        )

        self.assertEqual(summary['counts']['missing'], 1)
        self.assertIn('extract_vendor', summary['missing'])
        self.assertTrue(has_failures(summary))

    def test_no_data_only_group_is_not_a_failure(self):
        summary = summarize_subquery_statuses([
            status('customsearch_vendor', NO_DATA),
            status('customsearch_currency', NO_DATA),
        ])

        self.assertFalse(has_failures(summary))
        self.assertIn('2 with no data', format_summary(summary))

    def test_summary_lists_failed_reasons(self):
        summary = summarize_subquery_statuses([
            status('customsearch_customer', FAILED, error='RuntimeError: S3 down'),
        ])

        rendered = format_summary(summary)
        self.assertIn('FAILED: customsearch_customer: RuntimeError: S3 down', rendered)


if __name__ == '__main__':
    unittest.main()
