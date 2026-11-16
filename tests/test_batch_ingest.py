"""
Tests untuk batch ingestion (src/batch_ingest.py).

Mencakup:
    - penggabungan multi-batch normal (kolom processed identik pipeline single-file)
    - baris rusak di satu batch tidak menghentikan batch lain
    - batch duplikat dilewati (skipped) dan tidak dihitung ulang
    - direktori kosong / tidak ada -> error yang jelas

Jalankan: pytest -v
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

# Pastikan modul dapat di-import
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pipeline  # noqa: E402
import batch_ingest  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def raw_lines():
    """Header + baris data dari dataset mentah asli."""
    with open(pipeline.RAW_PATH, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    return lines[0], lines[1:]


def _write_batch(path: Path, header: str, data_lines):
    path.write_text("\n".join([header, *data_lines]) + "\n", encoding="utf-8")


def _run(batch_dir: Path, tmp_path: Path) -> pd.DataFrame:
    return batch_ingest.run_batch_pipeline(
        batch_dir,
        processed_path=tmp_path / "processed.csv",
        manifest_path=tmp_path / "manifest.csv",
    )


# ---------------------------------------------------------------------------
# 1. Penggabungan multi-batch normal
# ---------------------------------------------------------------------------
def test_multi_batch_merge(tmp_path, raw_lines):
    header, data = raw_lines
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "batch_01.csv", header, data[:100])
    _write_batch(batch_dir / "batch_02.csv", header, data[100:])

    manifest = _run(batch_dir, tmp_path)

    # Semua batch sukses, dicatat berurutan sesuai nama file
    assert manifest["batch_file"].tolist() == ["batch_01.csv", "batch_02.csv"]
    assert (manifest["status"] == batch_ingest.STATUS_SUCCESS).all()
    assert manifest["rows_read"].tolist() == [100, len(data) - 100]
    assert (manifest["rows_clean"] > 0).all()

    # Manifest tertulis ke disk dan bisa diverifikasi ulang
    reloaded = pd.read_csv(tmp_path / "manifest.csv")
    assert reloaded.shape == manifest.shape

    # Processed output: jumlah baris = total baris bersih semua batch
    processed = pd.read_csv(tmp_path / "processed.csv")
    assert processed.shape[0] == int(manifest["rows_clean"].sum())

    # Struktur kolom identik dengan pipeline single-file yang sudah ada
    single = pipeline.transform_data(
        pipeline.clean_data(pipeline.read_data(pipeline.RAW_PATH))
    )
    assert list(processed.columns) == list(single.columns)


def test_single_batch_matches_single_file_pipeline(tmp_path, raw_lines):
    """Satu batch berisi dataset penuh == hasil pipeline single-file."""
    header, data = raw_lines
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "batch_01.csv", header, data)

    manifest = _run(batch_dir, tmp_path)
    assert (manifest["status"] == batch_ingest.STATUS_SUCCESS).all()

    processed = pd.read_csv(tmp_path / "processed.csv")
    single = pipeline.transform_data(
        pipeline.clean_data(pipeline.read_data(pipeline.RAW_PATH))
    )
    assert list(processed.columns) == list(single.columns)
    assert processed.shape[0] == single.shape[0] == 201


# ---------------------------------------------------------------------------
# 2. Baris rusak di satu batch tidak menghentikan batch lain
# ---------------------------------------------------------------------------
def test_bad_rows_only_affect_their_batch(tmp_path, raw_lines):
    header, data = raw_lines
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()

    _write_batch(batch_dir / "batch_01_good.csv", header, data[:50])

    # Batch rusak (a): nilai non-numerik di kolom 'price' pada baris ke-3
    cols = header.split(",")
    bad = data[70].split(",")
    bad[cols.index("price")] = "not_a_number"
    _write_batch(batch_dir / "batch_02_bad_numeric.csv",
                 header, [data[69], ",".join(bad), data[71]])

    # Batch rusak (b): jumlah kolom tidak sesuai pada baris ke-2
    short_row = ",".join(data[80].split(",")[:-3])
    _write_batch(batch_dir / "batch_03_bad_columns.csv", header, [short_row])

    _write_batch(batch_dir / "batch_04_good.csv", header, data[100:150])

    manifest = _run(batch_dir, tmp_path)
    status = dict(zip(manifest["batch_file"], manifest["status"]))

    # Batch gagal tercatat, batch baik tetap diproses
    assert status["batch_01_good.csv"] == batch_ingest.STATUS_SUCCESS
    assert status["batch_02_bad_numeric.csv"] == batch_ingest.STATUS_FAILED
    assert status["batch_03_bad_columns.csv"] == batch_ingest.STATUS_FAILED
    assert status["batch_04_good.csv"] == batch_ingest.STATUS_SUCCESS

    # Alasan kegagalan dapat dilacak: menyebut kolom/baris yang bermasalah
    reason_numeric = manifest.loc[
        manifest["batch_file"] == "batch_02_bad_numeric.csv", "reason"].iloc[0]
    assert "price" in reason_numeric
    assert "baris 3" in reason_numeric  # baris data ke-2 -> baris CSV ke-3

    reason_columns = manifest.loc[
        manifest["batch_file"] == "batch_03_bad_columns.csv", "reason"].iloc[0]
    assert "baris 2" in reason_columns
    assert "kolom" in reason_columns

    # Output hanya berisi baris dari batch yang sukses
    processed = pd.read_csv(tmp_path / "processed.csv")
    expected_rows = int(manifest.loc[
        manifest["status"] == batch_ingest.STATUS_SUCCESS, "rows_clean"].sum())
    assert processed.shape[0] == expected_rows


# ---------------------------------------------------------------------------
# 3. Batch duplikat dilewati
# ---------------------------------------------------------------------------
def test_duplicate_batch_is_skipped(tmp_path, raw_lines):
    header, data = raw_lines
    batch_dir = tmp_path / "batches"
    batch_dir.mkdir()
    _write_batch(batch_dir / "batch_01.csv", header, data[:80])
    _write_batch(batch_dir / "batch_02.csv", header, data[:80])  # isi identik
    _write_batch(batch_dir / "batch_03.csv", header, data[80:120])

    manifest = _run(batch_dir, tmp_path)
    status = dict(zip(manifest["batch_file"], manifest["status"]))

    assert status["batch_01.csv"] == batch_ingest.STATUS_SUCCESS
    assert status["batch_02.csv"] == batch_ingest.STATUS_SKIPPED
    assert status["batch_03.csv"] == batch_ingest.STATUS_SUCCESS

    # Alasan skip menyebut batch asal yang diduplikasi
    reason = manifest.loc[
        manifest["batch_file"] == "batch_02.csv", "reason"].iloc[0]
    assert "batch_01.csv" in reason

    # Batch duplikat tidak dihitung ulang: baris output = batch 1 + batch 3
    rows_1 = int(manifest.loc[manifest["batch_file"] == "batch_01.csv",
                              "rows_clean"].iloc[0])
    rows_3 = int(manifest.loc[manifest["batch_file"] == "batch_03.csv",
                              "rows_clean"].iloc[0])
    processed = pd.read_csv(tmp_path / "processed.csv")
    assert processed.shape[0] == rows_1 + rows_3


# ---------------------------------------------------------------------------
# 4. Direktori kosong / tidak ada -> error yang jelas
# ---------------------------------------------------------------------------
def test_empty_directory_raises(tmp_path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError, match="Tidak ada file CSV"):
        _run(empty_dir, tmp_path)
    # Tidak boleh ada processed output yang diam-diam dibuat
    assert not (tmp_path / "processed.csv").exists()


def test_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="tidak ditemukan"):
        _run(tmp_path / "does_not_exist", tmp_path)
