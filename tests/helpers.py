"""Shared test helpers: lightweight tk-variable fakes and fixture factories."""

import csv
import os

import openpyxl
import pytest

from extractors import DataExtractor


class FakeVar:
    """Mimics the tiny slice of tkinter's IntVar API the extractors use."""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeLog:
    """Mimics the tkinter Text widget methods DataExtractor calls."""

    def __init__(self):
        self.content = ''

    def config(self, **kwargs):
        pass

    def delete(self, start, end):
        self.content = ''

    def insert(self, pos, text):
        self.content += text

    def see(self, pos):
        pass

    def get(self, start, end):
        return self.content


def make_extractor(excel=True, columns=True, exact=False):
    excel_var = FakeVar(excel)
    col_var = FakeVar(columns)
    exact_var = FakeVar(exact)
    return DataExtractor(excel_var, FakeLog(), col_var, exact_var)


def write(path, content, encoding='utf-8-sig'):
    with open(path, 'w', encoding=encoding) as f:
        f.write(content)


def read_csv_rows(path):
    with open(path, newline='', encoding='utf-8-sig') as f:
        return list(csv.reader(f))


def read_sheet_rows(path, sheet_name):
    wb = openpyxl.load_workbook(path)
    sheet = wb[sheet_name.title()]
    rows = [[cell for cell in row if cell is not None] for row in sheet.iter_rows(values_only=True)]
    wb.close()
    return [row for row in rows if row]
