import unittest
from unittest.mock import patch, MagicMock
from suite_connect import SuiteExport

class TestSuiteExport(unittest.TestCase):
    def setUp(self):
        self.results = [
            {'key1': 'value1', 'key2': 'value2', 'key3': {'subkey1': 'subvalue1', 'subkey2': 'subvalue2'}},
            {'key1': 'value3', 'key2': 'value4', 'key3': {'subkey1': 'subvalue3', 'subkey2': 'subvalue4'}}
        ]

    @patch('suite_connect.suiteexport.SuiteExport.logger')
    @patch('suite_connect.suiteexport.S3Hook')
    @patch('suite_connect.suiteexport.NamedTemporaryFile')
    @patch('suite_connect.suiteexport.writer')
    def test_load_to_s3(self, mock_writer, mock_temp_file, mock_s3_hook, mock_logger):
        mock_temp_file.return_value.__enter__.return_value.name = 'tempfile'
        mock_s3_hook.return_value.load_file.return_value = True

        suite_export = SuiteExport('aws_conn_id', 'my_bucket', 'my_file', self.results)
        result = suite_export.load_to_s3()

        self.assertIsNotNone(result)

        mock_s3_hook.assert_called_once_with(aws_conn_id='aws_conn_id')
        mock_s3_hook.return_value.load_file.assert_called_once_with(
            filename='tempfile',
            key='my_file.csv',
            bucket_name='my_bucket',
            replace=True
        )
        mock_logger.info.assert_called_once_with('SUCCESS: my_file.csv loaded to my_bucket')

    def test_flatten(self):
        suite_export = SuiteExport('aws_conn_id', 'my_bucket', 'my_file', self.results)
        result = suite_export.flatten(self.results[0])
        expected = {
            'key1': 'value1',
            'key2': 'value2',
            'key3_subkey1': 'subvalue1',
            'key3_subkey2': 'subvalue2'
        }
        self.assertEqual(result, expected)

    @patch('suite_connect.suiteexport.SuiteExport.logger')
    @patch('suite_connect.suiteexport.S3Hook')
    def test_load_to_s3_with_empty_results_does_not_read_first_row(
        self, mock_s3_hook, mock_logger
    ):
        suite_export = SuiteExport('aws_conn_id', 'my_bucket', 'my_file', [])

        result = suite_export.load_to_s3()

        self.assertIsNone(result)
        mock_s3_hook.assert_not_called()
        mock_s3_hook.return_value.load_file.assert_not_called()
        mock_logger.info.assert_called_once_with(
            'NO DATA: my_file.csv not exported to my_bucket because there are no results'
        )

if __name__ == '__main__':
    unittest.main()