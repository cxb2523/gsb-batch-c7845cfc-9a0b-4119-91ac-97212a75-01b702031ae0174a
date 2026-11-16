"""
Batch Ingestion Entry Point - Automobile Dataset
=================================================
Entry point baru untuk mengolah data yang masuk secara bertahap (per batch).
Setiap batch adalah satu file CSV di dalam sebuah direktori; semua file
dibaca berurutan sesuai nama file, dibersihkan memakai logika cleaning &
transformasi yang sama dengan pipeline single-file (src/pipeline.py),
lalu digabung menjadi satu processed dataset.

Perilaku utama:
    - Setiap batch dicatat dalam settlement manifest: nama file, baris
      dibaca, baris setelah cleaning, status (success/failed/skipped),
      dan alasan kegagalan yang dapat dilacak (kolom + nomor baris).
    - Kegagalan satu batch (baris rusak: nilai numerik tidak bisa
      di-parse, jumlah kolom tidak sesuai) TIDAK menghentikan batch lain.
    - Batch yang isinya persis sama dengan batch yang sudah sukses
      dilewati (skipped) dan ditandai sebagai duplikat.
    - Direktori tidak ada / kosong -> error yang jelas, bukan file kosong.

Jalankan dari root project:
    python src/batch_ingest.py data/raw/batches --out data/processed/automobileEDA_processed.csv
"""

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import pandas as pd

# Pastikan 'pipeline' dapat di-import baik saat file ini dijalankan sebagai
# script (python src/batch_ingest.py) maupun saat di-import dari tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline  # noqa: E402

ROOT_DIR = pipeline.ROOT_DIR
DEFAULT_PROCESSED_PATH = pipeline.PROCESSED_PATH

# Status batch dalam settlement manifest
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

MANIFEST_COLUMNS = ["batch_file", "status", "rows_read", "rows_clean", "reason"]

# Kolom yang diharapkan numerik pada data mentah. Dipakai untuk mendeteksi
# baris rusak (nilai tidak bisa di-parse sebagai angka) sebelum cleaning.
EXPECTED_NUMERIC_COLUMNS = [
    "symboling", "normalized-losses", "wheel-base", "length", "width",
    "height", "curb-weight", "engine-size", "bore", "stroke",
    "compression-ratio", "horsepower", "peak-rpm", "city-mpg",
    "highway-mpg", "price", "city-L/100km", "diesel", "gas",
]


def _rel(path: Path) -> str:
    return pipeline._rel(path)


# ---------------------------------------------------------------------------
# Validasi baris rusak (bad rows)
# ---------------------------------------------------------------------------
def _check_column_counts(path: Path):
    """Memeriksa jumlah field tiap baris terhadap header.

    Mengembalikan list tuple (nomor_baris, expected, actual) untuk baris
    yang jumlah kolomnya tidak sesuai. Nomor baris 1-based (header = 1).
    """
    mismatches = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return [(1, 0, 0)]  # file kosong sama sekali
        expected = len(header)
        for line_no, row in enumerate(reader, start=2):
            if len(row) != expected:
                mismatches.append((line_no, expected, len(row)))
    return mismatches


def _find_non_numeric_values(df: pd.DataFrame):
    """Mencari nilai non-numerik pada kolom yang seharusnya numerik.

    Mengembalikan list tuple (kolom, nomor_baris_csv, nilai). Missing value
    murni (sel kosong) tidak dianggap rusak — itu ditangani cleaning.
    """
    problems = []
    for col in EXPECTED_NUMERIC_COLUMNS:
        if col not in df.columns:
            continue
        converted = pd.to_numeric(df[col], errors="coerce")
        bad_mask = converted.isna() & df[col].notna()
        for idx in df.index[bad_mask]:
            # +2: index 0-based -> baris data ke-(idx+1) -> baris CSV ke-(idx+2)
            problems.append((col, int(idx) + 2, df.loc[idx, col]))
    return problems


# ---------------------------------------------------------------------------
# Deteksi batch duplikat
# ---------------------------------------------------------------------------
def _fingerprint(df: pd.DataFrame) -> str:
    """Sidik jari isi sebuah batch (urutan baris tidak berpengaruh)."""
    # fillna dulu: pandas 3 mempertahankan NaN/NA pada dtype str
    as_str = df.astype(str).fillna("<NA>")
    rows = sorted(as_str.agg("\x1f".join, axis=1).tolist())
    return hashlib.sha256("\x1e".join(rows).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Pemrosesan satu batch
# ---------------------------------------------------------------------------
def _process_batch(path: Path):
    """Membaca + memvalidasi + membersihkan satu file batch.

    Mengembalikan (record_manifest, cleaned_df). Jika batch gagal,
    cleaned_df adalah None dan record berisi alasan yang dapat dilacak.
    """
    record = {
        "batch_file": path.name,
        "status": STATUS_FAILED,
        "rows_read": 0,
        "rows_clean": 0,
        "reason": "",
    }

    # (1) Jumlah kolom tiap baris harus sesuai header
    mismatches = _check_column_counts(path)
    if mismatches:
        line_no, expected, actual = mismatches[0]
        record["reason"] = (
            f"jumlah kolom tidak sesuai di baris {line_no}: "
            f"diharapkan {expected} field, ditemukan {actual} "
            f"({len(mismatches)} baris bermasalah)"
        )
        return record, None

    # (2) Baca CSV
    try:
        df = pd.read_csv(path)
    except Exception as exc:  # ParserError, UnicodeDecodeError, dsb.
        record["reason"] = f"gagal membaca CSV: {exc}"
        return record, None
    record["rows_read"] = int(len(df))

    if df.empty:
        record["reason"] = "file tidak memiliki baris data"
        return record, None

    # (3) Kolom numerik harus bisa di-parse
    problems = _find_non_numeric_values(df)
    if problems:
        col, line_no, value = problems[0]
        record["reason"] = (
            f"nilai non-numerik {value!r} di kolom '{col}' baris {line_no} "
            f"({len(problems)} nilai bermasalah)"
        )
        return record, None

    # (4) Cleaning memakai logika pipeline yang sudah ada
    print(f"\n[BATCH] Membersihkan batch: {path.name}")
    cleaned = pipeline.clean_data(df)
    record["rows_clean"] = int(len(cleaned))
    record["status"] = STATUS_SUCCESS
    return record, cleaned


# ---------------------------------------------------------------------------
# Orkestrasi batch ingestion
# ---------------------------------------------------------------------------
def run_batch_pipeline(batch_dir, processed_path=DEFAULT_PROCESSED_PATH,
                       manifest_path=None) -> pd.DataFrame:
    """Memproses semua batch CSV dalam sebuah direktori.

    Mengembalikan settlement manifest sebagai DataFrame dan menuliskannya
    ke CSV. Melempar FileNotFoundError/ValueError untuk direktori yang
    tidak ada atau kosong, dan RuntimeError bila semua batch gagal.
    """
    batch_dir = Path(batch_dir)
    processed_path = Path(processed_path)
    if manifest_path is None:
        manifest_path = processed_path.parent / "batch_manifest.csv"
    manifest_path = Path(manifest_path)

    if not batch_dir.exists():
        raise FileNotFoundError(f"Direktori batch tidak ditemukan: {batch_dir}")
    if not batch_dir.is_dir():
        raise NotADirectoryError(f"Bukan sebuah direktori: {batch_dir}")
    files = sorted(batch_dir.glob("*.csv"), key=lambda p: p.name)
    if not files:
        raise ValueError(
            f"Tidak ada file CSV di direktori batch: {batch_dir} "
            "(direktori kosong -> tidak ada output yang dihasilkan)"
        )

    print("\n" + "#" * 70)
    print(f"#  BATCH INGESTION - {len(files)} batch dari {_rel(batch_dir)}")
    print("#" * 70)

    records = []
    cleaned_batches = []
    seen_fingerprints = {}  # fingerprint -> nama batch sukses pertama

    for path in files:
        record, cleaned = _process_batch(path)
        if cleaned is not None:
            fingerprint = _fingerprint(cleaned)
            if fingerprint in seen_fingerprints:
                record["status"] = STATUS_SKIPPED
                record["reason"] = (
                    f"duplikat: isi identik dengan batch "
                    f"'{seen_fingerprints[fingerprint]}' yang sudah sukses"
                )
            else:
                seen_fingerprints[fingerprint] = path.name
                cleaned_batches.append(cleaned)
        records.append(record)
        print(f"[BATCH] {record['batch_file']}: {record['status']} "
              f"(dibaca={record['rows_read']}, bersih={record['rows_clean']})"
              + (f" - {record['reason']}" if record["reason"] else ""))

    # Settlement manifest selalu ditulis, berapapun hasilnya
    manifest = pd.DataFrame(records, columns=MANIFEST_COLUMNS)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.to_csv(manifest_path, index=False)
    print(f"\n[MANIFEST] Settlement manifest disimpan ke: {_rel(manifest_path)}")

    n_success = int((manifest["status"] == STATUS_SUCCESS).sum())
    n_failed = int((manifest["status"] == STATUS_FAILED).sum())
    n_skipped = int((manifest["status"] == STATUS_SKIPPED).sum())
    print(f"[MANIFEST] Ringkasan: {n_success} sukses, "
          f"{n_failed} gagal, {n_skipped} dilewati (duplikat)")

    if not cleaned_batches:
        raise RuntimeError(
            "Semua batch gagal/dilewati - tidak ada data untuk ditulis. "
            f"Lihat manifest: {_rel(manifest_path)}"
        )

    # Gabungkan batch sukses, lalu transformasi memakai logika yang sama
    merged = pd.concat(cleaned_batches, ignore_index=True)
    print(f"\n[MERGE] Total baris bersih dari {n_success} batch: {len(merged)}")
    df_final = pipeline.transform_data(merged)
    pipeline.save_data(df_final, processed_path)

    print("\n" + "#" * 70)
    print("#  BATCH INGESTION SELESAI")
    print("#" * 70 + "\n")
    return manifest


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        description="Batch ingestion: proses semua CSV dalam sebuah direktori "
                    "menjadi satu processed dataset + settlement manifest."
    )
    parser.add_argument("batch_dir",
                        help="Direktori berisi file CSV batch (dibaca urut nama)")
    parser.add_argument("--out", default=str(DEFAULT_PROCESSED_PATH),
                        help="Path output processed CSV "
                             f"(default: {DEFAULT_PROCESSED_PATH})")
    parser.add_argument("--manifest", default=None,
                        help="Path output settlement manifest CSV "
                             "(default: batch_manifest.csv di samping --out)")
    args = parser.parse_args(argv)
    run_batch_pipeline(args.batch_dir, args.out, args.manifest)


if __name__ == "__main__":
    main()
