import os
import sys
import unittest
from unittest.mock import MagicMock, patch

_MODULES_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_REPO_ROOT = os.path.dirname(_MODULES_DIR)
_DAGS_DIR = os.path.join(_REPO_ROOT, 'dags')
for _path in (_MODULES_DIR, _DAGS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from custom_operators.netsuite_extraction_settlement_operator import (
    NetSuiteExtractionSettlementOperator
)
from custom_operators.netsuite_to_s3_operator import NetSuiteToS3Operator
from suite_connect.settlement import (
    FAILED,
    NO_DATA,
    STATUS_XCOM_KEY,
    SUCCESS,
    build_status,
)

SEARCH_TYPES = ['transaction', 'vendor', 'customer', 'currency']


def status(search_id, state, records=None, error=None):
    return build_status(
        task_id=f'extract_{search_id}',
        search_type='vendor',
        search_id=search_id,
        filename='netsuite_extracts_20240101',
        bucket_name='finance-data-lake-landing',
        status=state,
        records=records,
        error=error,
    )


class TestNetSuiteExtractionSettlementOperator(unittest.TestCase):
    """The group fails only when a subquery failed or never reported."""

    def _operator(self, task_ids):
        return NetSuiteExtractionSettlementOperator(
            task_id='settle_subqueries',
            subquery_task_ids=task_ids,
        )

    @staticmethod
    def _context(statuses):
        task_instance = MagicMock()
        task_instance.xcom_pull.side_effect = lambda task_ids, key: statuses[task_ids]
        return {'ti': task_instance}, task_instance

    def test_group_succeeds_when_only_success_and_no_data(self):
        statuses = {
            'extract_gl': status('customsearch_gl', SUCCESS, records=2),
            'extract_vendor': status('customsearch_vendor', NO_DATA),
        }
        context, task_instance = self._context(statuses)

        summary = self._operator(['extract_gl', 'extract_vendor']).execute(context)

        self.assertEqual(summary['counts'][SUCCESS], 1)
        self.assertEqual(summary['counts'][NO_DATA], 1)
        self.assertEqual(summary['counts'][FAILED], 0)
        task_instance.xcom_push.assert_called_once()

    def test_group_fails_when_any_subquery_failed(self):
        statuses = {
            'extract_gl': status('customsearch_gl', SUCCESS, records=2),
            'extract_customer': status(
                'customsearch_customer', FAILED, error='OSError: boom'
            ),
        }
        context, _ = self._context(statuses)
        operator = self._operator(['extract_gl', 'extract_customer'])

        with self.assertRaises(Exception) as raised:
            operator.execute(context)

        self.assertIn('Incomplete NetSuite extraction', str(raised.exception))
        self.assertIn('customsearch_customer', str(raised.exception))
        self.assertIn('OSError: boom', str(raised.exception))

    def test_group_fails_when_a_subquery_never_reported(self):
        statuses = {
            'extract_gl': status('customsearch_gl', SUCCESS),
            'extract_vendor': None,
        }
        context, _ = self._context(statuses)
        operator = self._operator(['extract_gl', 'extract_vendor'])

        with self.assertRaises(Exception) as raised:
            operator.execute(context)

        self.assertIn('extract_vendor', str(raised.exception))

    def test_group_uses_settlement_xcom_key(self):
        task_instance = MagicMock()
        task_instance.xcom_pull.return_value = status('customsearch_gl', SUCCESS)

        self._operator(['extract_gl']).execute({'ti': task_instance})

        _, kwargs = task_instance.xcom_pull.call_args
        self.assertEqual(kwargs['key'], STATUS_XCOM_KEY)

    def test_group_without_task_instance_reports_missing_not_success(self):
        operator = self._operator(['extract_gl'])

        # No task instance means no status is readable, so the group must not
        # silently pass as if the extraction had completed.
        with self.assertRaises(Exception) as raised:
            operator.execute({})

        self.assertIn('extract_gl', str(raised.exception))


class TestSettlementEndToEnd(unittest.TestCase):
    """Real subquery operators feed the group settlement, no hand-built XComs."""

    @patch('custom_operators.netsuite_to_s3_operator.SuiteExport')
    @patch('custom_operators.netsuite_to_s3_operator.SuiteQuery')
    def test_group_outcome_when_one_subquery_fails_and_one_is_empty(
        self, mock_query, mock_export
    ):
        rows = {
            'gl': [{'id': 'gl-1'}],
            'vendor': [],
            'customer': [{'id': 'cust-1'}],
            'currency': [{'id': 'cur-1'}],
        }

        def make_query(filters, columns):
            query = MagicMock()
            query.extract_results.side_effect = lambda: rows[filters[0]]
            return query

        def make_export(**kwargs):
            export = MagicMock()
            key = next(name for name, value in rows.items() if value == kwargs['results'])
            if key == 'customer':
                export.load_to_s3.side_effect = OSError('landing bucket unavailable')
            return export

        mock_query.side_effect = make_query
        mock_export.side_effect = make_export

        pulled = {}
        extract_tis = {}

        def run_extract(search_type, search_id, filter_key):
            task_instance = MagicMock()
            task_instance.xcom_push.side_effect = (
                lambda key, value: pulled.__setitem__(f'extract_{search_id}', value)
            )
            operator = NetSuiteToS3Operator(
                task_id=f'extract_{search_id}',
                search_types=SEARCH_TYPES,
                search_type=search_type,
                search_id=search_id,
                conn_id='minio_conn',
                bucket_name='finance-data-lake-landing',
                filename='netsuite_extracts_20240101',
                filter_expression=[filter_key],
            )
            operator.execute({'ti': task_instance})
            extract_tis[f'extract_{search_id}'] = task_instance

        subqueries = [
            ('transaction', 'customsearch_gl_posting_transactions_india', 'gl'),
            ('vendor', 'customsearch_vendor', 'vendor'),
            ('customer', 'customsearch_customer', 'customer'),
            ('currency', 'customsearch_currency', 'currency'),
        ]
        for search_type, search_id, filter_key in subqueries:
            run_extract(search_type, search_id, filter_key)

        # Every subquery settled: two successes, one no-data, one failure.
        self.assertEqual(pulled['extract_customsearch_gl_posting_transactions_india']['status'], SUCCESS)
        self.assertEqual(pulled['extract_customsearch_vendor']['status'], NO_DATA)
        self.assertEqual(pulled['extract_customsearch_customer']['status'], FAILED)
        self.assertEqual(pulled['extract_customsearch_currency']['status'], SUCCESS)

        settlement_ti = MagicMock()
        settlement_ti.xcom_pull.side_effect = lambda task_ids, key: pulled[task_ids]
        operator = NetSuiteExtractionSettlementOperator(
            task_id='settle_subqueries',
            subquery_task_ids=[f'extract_{search_id}' for _, search_id, _ in subqueries],
        )

        with self.assertRaises(Exception) as raised:
            operator.execute({'ti': settlement_ti})

        message = str(raised.exception)
        self.assertIn('2 succeeded', message)
        self.assertIn('1 with no data', message)
        self.assertIn('1 failed', message)
        self.assertIn('landing bucket unavailable', message)


if __name__ == '__main__':
    unittest.main()
