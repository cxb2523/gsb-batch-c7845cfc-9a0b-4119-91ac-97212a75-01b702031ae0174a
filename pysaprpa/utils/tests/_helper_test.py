import os
import tempfile
import unittest

from pysaprpa.utils._helper import HelperUtils


class TestCheckPathValid(unittest.TestCase):
    def setUp(self):
        self._temp_dir_ctx = tempfile.TemporaryDirectory()
        self.temp_dir = self._temp_dir_ctx.name

    def tearDown(self):
        self._temp_dir_ctx.cleanup()

    def test_missing_directory_raises(self):
        missing_dir = os.path.join(self.temp_dir, 'does_not_exist')
        with self.assertRaises(ValueError) as ctx:
            HelperUtils._check_path_valid(missing_dir, 'report.xlsx')
        self.assertIn('does not exist', str(ctx.exception))

    def test_existing_file_returns_true(self):
        file_name = 'test_file.xlsx'
        with open(os.path.join(self.temp_dir, file_name), 'w'):
            pass

        self.assertTrue(
            HelperUtils._check_path_valid(self.temp_dir, file_name)
        )

    def test_non_existing_file_returns_false(self):
        self.assertFalse(
            HelperUtils._check_path_valid(self.temp_dir, 'brand_new.xlsx')
        )

    def test_existence_check_uses_actual_disk_path(self):
        # The branch decision must reflect the real resolved location,
        # not just the file name/extension.
        file_name = 'nested_report.XLSX'
        self.assertFalse(
            HelperUtils._check_path_valid(self.temp_dir, file_name)
        )
        with open(os.path.join(self.temp_dir, file_name), 'w'):
            pass
        self.assertTrue(
            HelperUtils._check_path_valid(self.temp_dir, file_name)
        )

    def test_invalid_file_name_raises(self):
        for bad_name in ['', '   ', 'bad/name.xlsx', 'bad\\name.xlsx',
                         'bad?name.xlsx', 'bad:name.xlsx']:
            with self.subTest(bad_name=bad_name):
                with self.assertRaises(ValueError):
                    HelperUtils._check_path_valid(self.temp_dir, bad_name)

    def test_extension_is_not_validated_here(self):
        # Extension/export-method matching is ObjectTree.export's job now.
        # _check_path_valid must accept any legal name and only report
        # whether the file exists.
        self.assertFalse(
            HelperUtils._check_path_valid(self.temp_dir, 'data.csv')
        )
        self.assertFalse(
            HelperUtils._check_path_valid(self.temp_dir, 'notes.txt')
        )


class TestCleanFieldText(unittest.TestCase):
    def test_clean_field_text(self):
        helper = HelperUtils()

        # Accented characters
        self.assertEqual(helper._clean_field_text("ãäòábcd"), "aaoabcd")

        # Special characters
        self.assertEqual(
            helper._clean_field_text("sold-by&and-for"),
            "sold_by_and_for",
        )

        # Conjunctions
        self.assertEqual(helper._clean_field_text("And"), "and")

        # Normal text
        self.assertEqual(helper._clean_field_text("Hello World"), "hello_world")

        # Empty string
        self.assertEqual(helper._clean_field_text(""), "")


if __name__ == '__main__':
    unittest.main()
