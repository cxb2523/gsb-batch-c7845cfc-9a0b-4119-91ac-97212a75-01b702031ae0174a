import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# The pipeline keeps its operators under ``dags/`` and its libraries under
# ``modules/``; both are on PYTHONPATH inside the Airflow image.  Add them
# explicitly so this test can also run from a plain checkout.
_MODULES_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_MODULES_DIR)
_DAGS_DIR = os.path.join(_REPO_ROOT, 'dags')
for _path in (_MODULES_DIR, _DAGS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from custom_operators.netsuite_to_s3_operator import NetSuiteToS3Operator
from suite_connect.settlement import (
    FAILED,
    NO_DATA,
    STATUS_XCOM_KEY,
    SUCCESS,
)
SEARCH_TYPES = ['transaction', 'vendor', 'customer', 'currency']


def build_operator(search_type='vendor', search_id='customsearch_vendor', **overrides):
    kwargs = dict(
        task_id=f'extract_{search_type}',
        search_types=SEARCH_TYPES,
        search_type=search_type,
        search_id=search_id,
        conn_id='minio_conn',
        bucket_name='finance-data-lake-landing',
        filename='netsuite_extracts_20240101',
        filter_expression=[],
        columns=None,
    )
    kwargs.update(overrides)
    return NetSuiteToS3Operator(**kwargs)


class TestNetSuiteToS3OperatorSettlement(unittest.TestCase):
    """Each subquery must settle on its own without blocking its siblings."""

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_empty_results_skip_export_and_settle_as_no_data(
        self, mock_query, mock_export
    ):
        mock_query.return_value.extract_results.return_value = []

        operator = build_operator()
        status = operator.execute(context={})

        self.assertEqual(status['status'], NO_DATA)
        self.assertEqual(status['records'], 0)
        self.assertEqual(status['search_id'], 'customsearch_vendor')
        mock_export.assert_not_called()

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_non_empty_results_are_exported_and_settle_as_success(
        self, mock_query, mock_export
    ):
        rows = [{'id': '1'}, {'id': '2'}]
        mock_query.return_value.extract_results.return_value = rows

        operator = build_operator()
        status = operator.execute(context={})

        self.assertEqual(status['status'], SUCCESS)
        self.assertEqual(status['records'], 2)
        mock_export.assert_called_once_with(
            conn_id='minio_conn',
            bucket_name='finance-data-lake-landing',
            filename='netsuite_extracts_20240101',
            results=rows,
        )
        mock_export.return_value.load_to_s3.assert_called_once_with()

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_export_failure_is_recorded_without_raising(
        self, mock_query, mock_export
    ):
        mock_query.return_value.extract_results.return_value = [{'id': '1'}]
        mock_export.return_value.load_to_s3.side_effect = RuntimeError('S3 is down')
        task_instance = MagicMock()

        operator = build_operator()
        status = operator.execute(context={'ti': task_instance})

        # The subquery itself does not raise: it records the failure so the
        # other subqueries in the group can still finish.
        self.assertEqual(status['status'], FAILED)
        self.assertIn('S3 is down', status['error'])
        self.assertIn('RuntimeError', status['error'])
        _, kwargs = task_instance.xcom_push.call_args
        self.assertEqual(kwargs['value']['status'], FAILED)

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_extraction_error_is_recorded_without_raising(
        self, mock_query, mock_export
    ):
        mock_query.return_value.extract_results.side_effect = ConnectionError('token expired')
        task_instance = MagicMock()

        operator = build_operator()
        status = operator.execute(context={'ti': task_instance})

        self.assertEqual(status['status'], FAILED)
        self.assertIn('token expired', status['error'])
        mock_export.assert_not_called()

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_none_extraction_is_a_failure_not_an_empty_result(
        self, mock_query, mock_export
    ):
        # SuiteQuery swallows request errors and returns None.
        mock_query.return_value.extract_results.return_value = None

        operator = build_operator()
        status = operator.execute(context={})

        self.assertEqual(status['status'], FAILED)
        mock_export.assert_not_called()

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_failed_subquery_does_not_block_the_other_subqueries(
        self, mock_query, mock_export
    ):
        rows_by_filter = {
            'gl': [{'id': 'gl-1'}],
            'vendor': [],
            'customer': [{'id': 'cust-1'}],
            'currency': [{'id': 'cur-1'}],
        }
        exporting = []

        def make_query(filters, columns):
            query = MagicMock()
            # ``filter_expression`` is used here only to select a canned
            # result set; each entry is a single filter triple list.
            query.extract_results.side_effect = lambda: rows_by_filter[filters[0]]
            return query

        def make_export(**kwargs):
            export = MagicMock()
            key = next(
                name for name, rows in rows_by_filter.items()
                if rows == kwargs['results']
            )
            exporting.append(key)

            def load_to_s3():
                if key == 'customer':
                    raise OSError('landing bucket unavailable')
                return True

            export.load_to_s3.side_effect = load_to_s3
            return export

        mock_query.side_effect = make_query
        mock_export.side_effect = make_export

        subqueries = [
            ('transaction', 'customsearch_gl_posting_transactions_india', 'gl'),
            ('vendor', 'customsearch_vendor', 'vendor'),
            ('customer', 'customsearch_customer', 'customer'),
            ('currency', 'customsearch_currency', 'currency'),
        ]

        statuses = {}
        for search_type, search_id, filter_key in subqueries:
            operator = build_operator(
                search_type=search_type,
                search_id=search_id,
                # A one-item filter list is a no-op for operator validation and
                # lets the mocked query pick its canned rows.
                filter_expression=[filter_key],
            )
            statuses[filter_key] = operator.execute(context={})

        self.assertEqual(statuses['gl']['status'], SUCCESS)
        self.assertEqual(statuses['vendor']['status'], NO_DATA)
        self.assertEqual(statuses['customer']['status'], FAILED)
        self.assertIn('landing bucket unavailable', statuses['customer']['error'])
        # The subquery after the failed one still finished its export.
        self.assertEqual(statuses['currency']['status'], SUCCESS)
        self.assertEqual(exporting, ['gl', 'customer', 'currency'])

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_settlement_status_is_pushed_to_xcom(self, mock_query, mock_export):
        mock_query.return_value.extract_results.return_value = [{'id': '1'}]
        task_instance = MagicMock()

        operator = build_operator()
        operator.execute(context={'ti': task_instance})

        task_instance.xcom_push.assert_called_once()
        _, kwargs = task_instance.xcom_push.call_args
        self.assertEqual(kwargs['key'], STATUS_XCOM_KEY)
        self.assertEqual(kwargs['value']['status'], SUCCESS)

    def test_invalid_search_id_still_raises_original_value_error(self):
        operator = build_operator(search_id='not_a_custom_search')
        task_instance = MagicMock()

        with self.assertRaises(ValueError) as raised:
            operator.execute(context={'ti': task_instance})

        self.assertEqual(str(raised.exception), 'Invalid script_id property')
        # The rejected subquery is still visible to the group settlement.
        _, kwargs = task_instance.xcom_push.call_args
        self.assertEqual(kwargs['value']['status'], FAILED)
        self.assertIn('Invalid script_id property', kwargs['value']['error'])

    def test_invalid_filter_separation_still_raises_original_value_error(self):
        operator = build_operator(
            filter_expression=[['posting', 'is', 'T'], 'NOR', ['cogs', 'is', 'F']]
        )

        with self.assertRaises(ValueError) as raised:
            operator.execute(context={})

        self.assertEqual(str(raised.exception), 'Invalid filter operator separation')

    def test_invalid_search_type_still_raises_original_value_error(self):
        operator = build_operator(search_type='unsupported_type')

        with self.assertRaises(ValueError) as raised:
            operator.execute(context={})

        self.assertEqual(str(raised.exception), 'Invalid NetSuite search type')


if __name__ == '__main__':
    unittest.main()
