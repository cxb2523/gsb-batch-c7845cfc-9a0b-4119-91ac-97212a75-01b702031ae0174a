"""Tests for ObjectTree.export path validation and generate/replace branching.

A fake SAP session records every interaction so the tests can prove that the
SAP save dialog is never opened when the target path is invalid, and that the
replace/generate branch is chosen from the real on-disk path.
"""
import json
import os
import tempfile

import pytest

from pysaprpa.core.object_tree import ObjectTree


class FakeGUIObject:
    """Stand-in for a SAP GUI scripting object (wnd[1], a text field, ...)."""

    def __init__(self, object_id, session):
        self._id = object_id
        self._session = session
        self.text = ''
        self.selected = False
        # Labels are parsed via FindById(...).LeftLabel; real labels expose
        # another object there, text fields expose None.
        self.LeftLabel = None

    def sendVKey(self, vkey):
        self._session.vkeys.append((self._id, int(vkey)))
        self._session.calls.append(('sendVKey', self._id, int(vkey)))

    def select(self):
        self.selected = True
        self._session.calls.append(('select', self._id))

    def press(self):
        self._session.calls.append(('press', self._id))


class FakeSession:
    """Records everything the export flow asks of the SAP session."""

    def __init__(self):
        self.calls = []
        self.vkeys = []
        self.fields = {}

    # SAP scripting spells this both ways across the code base.
    def FindById(self, object_id):
        return self.findById(object_id)

    def findById(self, object_id):
        if object_id not in self.fields:
            self.fields[object_id] = FakeGUIObject(object_id, self)
        return self.fields[object_id]

    def GetObjectTree(self, window_id, info_list):
        # Minimal save-dialog object tree: a non-changeable label followed by
        # the changeable directory and file name text fields.
        def node(object_id, object_type, changeable, text=''):
            return {
                'properties': {
                    'Id': f'app/ses[0]/{object_id}',
                    'Text': text,
                    'Type': object_type,
                    'Changeable': changeable,
                    'Name': '',
                    'IconName': '',
                }
            }

        tree = {
            'children': [
                node('wnd[1]/usr/lbl[1,1]', 'GuiLabel', 'false', 'Directory'),
                node('wnd[1]/usr/ctxt[2,1]', 'GuiTextField', 'true'),
                node('wnd[1]/usr/lbl[1,2]', 'GuiLabel', 'false', 'File Name'),
                node('wnd[1]/usr/ctxt[2,2]', 'GuiTextField', 'true'),
            ]
        }
        return json.dumps(tree)


def _make_object_tree(session):
    tree = ObjectTree(session=session)
    # Simulate a parsed export menu offering the spreadsheet option.
    tree.export_options_dict = {
        'spreadsheet': 'wnd[0]/mnu[1]/mnu[2]',
    }
    return tree


def test_export_replaces_when_file_already_exists():
    with tempfile.TemporaryDirectory() as temp_dir:
        file_name = 'existing_report.xlsx'
        with open(os.path.join(temp_dir, file_name), 'w'):
            pass

        session = FakeSession()
        tree = _make_object_tree(session)
        result = tree.export(
            how='spreadsheet', directory=temp_dir, file_name=file_name
        )

        expected_path = os.path.abspath(os.path.join(temp_dir, file_name))
        # The returned path is the actual write location.
        assert result == expected_path
        assert tree.path == expected_path
        # Final vkey 11 = Replace.
        assert session.vkeys[-1] == ('wnd[1]', 11)
        # Dialog fields were populated with the requested directory/name.
        assert session.findById('wnd[1]/usr/ctxt[2,1]').text == temp_dir
        assert session.findById('wnd[1]/usr/ctxt[2,2]').text == file_name


def test_export_generates_when_file_does_not_exist():
    with tempfile.TemporaryDirectory() as temp_dir:
        file_name = 'new_report.xlsx'
        assert not os.path.exists(os.path.join(temp_dir, file_name))

        session = FakeSession()
        tree = _make_object_tree(session)
        result = tree.export(
            how='spreadsheet', directory=temp_dir, file_name=file_name
        )

        expected_path = os.path.abspath(os.path.join(temp_dir, file_name))
        assert result == expected_path
        assert tree.path == expected_path
        # Final vkey 0 = Generate.
        assert session.vkeys[-1] == ('wnd[1]', 0)
        # Replace vkey must never be sent on the generate branch.
        assert all(vkey != 11 for _, vkey in session.vkeys)


def test_export_missing_directory_raises_without_touching_sap():
    with tempfile.TemporaryDirectory() as temp_dir:
        missing_dir = os.path.join(temp_dir, 'no_such_directory')

        session = FakeSession()
        tree = _make_object_tree(session)
        with pytest.raises(ValueError) as ctx:
            tree.export(
                how='spreadsheet',
                directory=missing_dir,
                file_name='report.xlsx',
            )
        assert 'does not exist' in str(ctx.value)

        # No SAP interaction at all: the save dialog must not open.
        assert session.calls == []
        assert session.vkeys == []
        assert session.fields == {}


def test_export_unsupported_extension_raises_without_touching_sap():
    with tempfile.TemporaryDirectory() as temp_dir:
        session = FakeSession()
        tree = _make_object_tree(session)
        with pytest.raises(ValueError):
            tree.export(
                how='spreadsheet', directory=temp_dir, file_name='report.csv'
            )
        assert session.calls == []


def test_export_supports_xls_extension_case_insensitively():
    with tempfile.TemporaryDirectory() as temp_dir:
        file_name = 'UPPER_REPORT.XLS'

        session = FakeSession()
        tree = _make_object_tree(session)
        result = tree.export(
            how='spreadsheet', directory=temp_dir, file_name=file_name
        )

        assert result == os.path.abspath(os.path.join(temp_dir, file_name))
        assert session.vkeys[-1] == ('wnd[1]', 0)
