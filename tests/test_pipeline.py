"""
Unit & integration tests untuk ETL pipeline.
Menjalankan pipeline end-to-end sekali (fixture), lalu memvalidasi
kualitas output: jumlah baris, tidak ada missing di kolom kritikal,
skala numerik dalam [0, 1], serta hasil encoding sesuai skema.

Jalankan: pytest -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Pastikan modul pipeline dapat di-import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def raw_df():
    """Dataset mentah apa adanya."""
    return pipeline.read_data(pipeline.RAW_PATH)


@pytest.fixture(scope="module")
def processed_df(raw_df):
    """Hasil akhir pipeline (clean + transform), tanpa menulis file."""
    cleaned = pipeline.clean_data(raw_df)
    return pipeline.transform_data(cleaned)


# ---------------------------------------------------------------------------
# 1. Extract
# ---------------------------------------------------------------------------
def test_raw_file_exists():
    assert pipeline.RAW_PATH.exists(), "Dataset mentah tidak ditemukan"


def test_raw_not_empty(raw_df):
    assert raw_df.shape[0] > 0
    assert raw_df.shape[1] > 0


# ---------------------------------------------------------------------------
# 2. Cleaning
# ---------------------------------------------------------------------------
def test_no_duplicates_after_clean(raw_df):
    cleaned = pipeline.clean_data(raw_df)
    assert cleaned.duplicated().sum() == 0, "Masih ada duplikat setelah cleaning"


def test_row_count_reduced_by_duplicates(raw_df):
    cleaned = pipeline.clean_data(raw_df)
    # 4 duplikat diketahui pada dataset ini
    assert cleaned.shape[0] == raw_df.shape[0] - 4


def test_categorical_values_are_lowercased(raw_df):
    cleaned = pipeline.clean_data(raw_df)
    makes = cleaned["make"].dropna().astype(str)
    assert (makes == makes.str.lower()).all(), "Ada nilai 'make' belum lowercase"
    # tidak ada whitespace berlebih
    assert (makes == makes.str.strip()).all(), "Masih ada whitespace pada 'make'"


def test_word_to_number_conversion(raw_df):
    cleaned = pipeline.clean_data(raw_df)
    assert pd.api.types.is_numeric_dtype(cleaned["num-of-doors"])
    assert pd.api.types.is_numeric_dtype(cleaned["num-of-cylinders"])
    assert set(cleaned["num-of-doors"].dropna().unique()) <= {2, 4}


# ---------------------------------------------------------------------------
# 3. Transformation
# ---------------------------------------------------------------------------
def test_no_missing_in_critical_columns(processed_df):
    """Kolom kritikal (fitur pemodelan) tidak boleh punya missing."""
    critical = ["price", "horsepower", "stroke", "engine-size", "curb-weight"]
    for col in critical:
        assert processed_df[col].isna().sum() == 0, f"Missing tersisa di '{col}'"


def test_scaled_columns_within_0_1(processed_df):
    """Semua kolom hasil Min-Max Scaling harus berada di rentang [0, 1]."""
    for col in pipeline.NUMERIC_TO_SCALE:
        assert processed_df[col].min() >= -1e-9, f"'{col}' < 0"
        assert processed_df[col].max() <= 1 + 1e-9, f"'{col}' > 1"


def test_ordinal_encoding_values(processed_df):
    assert set(processed_df["horsepower_ordinal"].dropna().unique()) <= {0, 1, 2}


def test_frequency_encoding_exists(processed_df):
    assert "make_freq" in processed_df.columns
    # proporsi harus di (0, 1]
    assert processed_df["make_freq"].between(0, 1).all()


def test_one_hot_columns_exist(processed_df):
    """Kolom one-hot untuk tiap kategori nominal harus tersedia."""
    expected_prefixes = ["body-style_", "drive-wheels_", "aspiration_",
                         "engine-type_", "engine-location_", "fuel-system_"]
    for prefix in expected_prefixes:
        assert any(c.startswith(prefix) for c in processed_df.columns), \
            f"Tidak ada kolom one-hot berawalan '{prefix}'"


def test_one_hot_rows_sum_to_one(processed_df):
    """Setiap grup one-hot harus tepat satu nilai '1' per baris."""
    for prefix in ["body-style_", "drive-wheels_", "aspiration_"]:
        cols = [c for c in processed_df.columns if c.startswith(prefix)]
        assert (processed_df[cols].sum(axis=1) == 1).all(), \
            f"Grup one-hot '{prefix}' tidak berjumlah 1 di semua baris"


def test_original_text_columns_dropped(processed_df):
    """Kolom teks asli harus sudah di-encode (tidak tersisa mentah)."""
    for col in ["make", "horsepower-binned", "body-style", "fuel-system"]:
        assert col not in processed_df.columns


# ---------------------------------------------------------------------------
# 4. End-to-end (Load)
# ---------------------------------------------------------------------------
def test_full_pipeline_writes_output(tmp_path, monkeypatch):
    """run_pipeline harus menghasilkan file CSV valid."""
    out = tmp_path / "out.csv"
    monkeypatch.setattr(pipeline, "PROCESSED_PATH", out)
    result = pipeline.run_pipeline()
    assert out.exists(), "File output tidak dibuat"
    reloaded = pd.read_csv(out)
    assert reloaded.shape == result.shape
    assert reloaded.shape[0] == 201


def test_raw_dataset_untouched(raw_df):
    """Menjalankan cleaning tidak boleh mengubah objek raw asli."""
    before = raw_df.shape
    pipeline.clean_data(raw_df)  # clean_data harus copy, bukan mutate
    assert raw_df.shape == before
