"""
ETL Data Pipeline - Automobile Dataset
=======================================
Pipeline sederhana untuk mengolah dataset otomotif mentah menjadi
processed dataset yang siap untuk analisis / pemodelan.

Alur ETL:
    Extract   -> membaca CSV mentah dari data/raw/
    Transform -> inspeksi, cleaning, dan transformasi data
    Load      -> menyimpan processed dataset ke data/processed/

Jalankan dari root project:
    python src/pipeline.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Konfigurasi path (relatif terhadap root project, bukan lokasi file ini)
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
RAW_PATH = ROOT_DIR / "data" / "raw" / "automobileEDA_dirty_training.csv"
PROCESSED_PATH = ROOT_DIR / "data" / "processed" / "automobileEDA_processed.csv"

# Peta kata -> angka untuk kolom kategorikal yang sebenarnya numerik
WORD_TO_NUM = {
    "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "eight": 8, "twelve": 12,
}

# Peta ordinal untuk horsepower-binned (Low < Medium < High)
HP_ORDINAL = {"low": 0, "medium": 1, "high": 2}

# Kolom numerik yang akan dinormalisasi dengan Min-Max Scaling
NUMERIC_TO_SCALE = [
    "wheel-base", "length", "width", "height", "curb-weight",
    "engine-size", "bore", "stroke", "compression-ratio",
    "horsepower", "peak-rpm", "city-mpg", "highway-mpg", "price",
]

# Kolom kategorikal yang akan di-one-hot encoding
CATEGORICAL_TO_ENCODE = [
    "body-style", "drive-wheels", "aspiration",
    "engine-type", "engine-location", "fuel-system",
]


def _rel(path: Path) -> str:
    """Path relatif terhadap root project (portable, tidak bocorkan path absolut)."""
    try:
        return str(Path(path).relative_to(ROOT_DIR))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# 1. EXTRACT - Membaca dataset
# ---------------------------------------------------------------------------
def read_data(path: Path) -> pd.DataFrame:
    """Membaca dataset CSV mentah menjadi DataFrame."""
    df = pd.read_csv(path)
    print(f"[EXTRACT] Dataset dibaca dari: {_rel(path)}")
    print(f"[EXTRACT] Ukuran awal: {df.shape[0]} baris x {df.shape[1]} kolom\n")
    return df


# ---------------------------------------------------------------------------
# 2. INSPECT - Memeriksa kondisi awal dataset
# ---------------------------------------------------------------------------
def inspect_data(df: pd.DataFrame) -> None:
    """Menampilkan pemeriksaan awal dataset ke terminal."""
    print("=" * 70)
    print("PEMERIKSAAN AWAL DATASET")
    print("=" * 70)

    print("\n-- 5 baris pertama --")
    print(df.head())

    print("\n-- Jumlah baris & kolom --")
    print(f"{df.shape[0]} baris, {df.shape[1]} kolom")

    print("\n-- Nama kolom --")
    print(list(df.columns))

    print("\n-- Tipe data tiap kolom --")
    print(df.dtypes)

    print("\n-- Missing values per kolom (hanya > 0) --")
    miss = df.isna().sum()
    print(miss[miss > 0] if (miss > 0).any() else "Tidak ada missing values")

    print("\n-- Jumlah duplicate records --")
    print(df.duplicated().sum())

    print("\n-- Nilai unik kolom kategorikal relevan --")
    for col in ["make", "body-style", "drive-wheels", "fuel-system",
                "num-of-doors", "aspiration", "horsepower-binned"]:
        if col in df.columns:
            uniques = sorted(df[col].dropna().astype(str).unique().tolist())
            print(f"  {col} ({len(uniques)}): {uniques}")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Helper: parsing tanggal multi-format menjadi format seragam
# ---------------------------------------------------------------------------
def _parse_mixed_dates(series: pd.Series) -> pd.Series:
    """Mengubah kolom tanggal dengan format campuran menjadi datetime seragam."""
    formats = ["%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y", "%d-%b-%Y"]
    parsed = pd.Series(pd.NaT, index=series.index, dtype="datetime64[ns]")
    remaining = series.copy()
    for fmt in formats:
        mask = parsed.isna() & remaining.notna()
        if not mask.any():
            break
        attempt = pd.to_datetime(remaining[mask], format=fmt, errors="coerce")
        parsed.loc[attempt.notna().index[attempt.notna()]] = attempt.dropna()
    # fallback untuk sisa format yang belum tertangani
    mask = parsed.isna() & remaining.notna()
    if mask.any():
        parsed.loc[mask] = pd.to_datetime(remaining[mask], errors="coerce")
    return parsed


# ---------------------------------------------------------------------------
# 3. TRANSFORM (a) - Data Cleaning
# ---------------------------------------------------------------------------
def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    """Membersihkan dataset: duplikat, whitespace, kategori, missing, tipe data."""
    print("=" * 70)
    print("DATA CLEANING")
    print("=" * 70)

    df = df.copy()
    rows_before = len(df)
    miss_before = int(df.isna().sum().sum())

    # (1) Standarisasi teks kategorikal: strip spasi + lowercase
    text_cols = df.select_dtypes(include="object").columns
    for col in text_cols:
        if col == "transaction_date":
            continue
        df[col] = df[col].str.strip().str.lower()
    # horsepower-binned dikembalikan ke Title case (Low/Medium/High)
    if "horsepower-binned" in df.columns:
        df["horsepower-binned"] = df["horsepower-binned"].str.title()

    # (2) Hapus duplicate records
    dup_count = int(df.duplicated().sum())
    df = df.drop_duplicates().reset_index(drop=True)
    print(f"[CLEAN] Duplikat dihapus: {dup_count} baris")

    # (3) Standarisasi format tanggal
    if "transaction_date" in df.columns:
        df["transaction_date"] = _parse_mixed_dates(df["transaction_date"])
        df["transaction_date"] = df["transaction_date"].dt.strftime("%Y-%m-%d")
        print("[CLEAN] transaction_date diseragamkan ke format YYYY-MM-DD")

    # (4) Konversi kategorikal-numerik (kata -> angka)
    for col in ["num-of-doors", "num-of-cylinders"]:
        if col in df.columns:
            df[col] = df[col].map(WORD_TO_NUM).astype("Int64")
    print("[CLEAN] num-of-doors & num-of-cylinders dikonversi teks -> angka")

    # (5) Tangani missing values
    #     - numerik  -> median (tahan outlier)
    #     - kategorikal -> modus
    num_cols = df.select_dtypes(include=[np.number]).columns
    for col in num_cols:
        if df[col].isna().any():
            df[col] = df[col].fillna(df[col].median())
    cat_cols = df.select_dtypes(include="object").columns
    for col in cat_cols:
        if col == "transaction_date":
            continue
        if df[col].isna().any():
            df[col] = df[col].fillna(df[col].mode()[0])
    print("[CLEAN] Missing values: numerik->median, kategorikal->modus")

    rows_after = len(df)
    miss_after = int(df.isna().drop(columns=["transaction_date"], errors="ignore").sum().sum())

    print("\n-- Ringkasan Cleaning --")
    print(f"  Baris  : {rows_before} -> {rows_after} (dihapus {rows_before - rows_after})")
    print(f"  Missing: {miss_before} -> {miss_after}")
    print("=" * 70 + "\n")
    return df


# ---------------------------------------------------------------------------
# 3. TRANSFORM (b) - Data Transformation
# ---------------------------------------------------------------------------
def _min_max_scale(series: pd.Series) -> pd.Series:
    """Min-Max Scaling manual: (x - min) / (max - min)."""
    lo, hi = series.min(), series.max()
    if hi == lo:
        return series * 0.0
    return (series - lo) / (hi - lo)


def transform_data(df: pd.DataFrame) -> pd.DataFrame:
    """Normalisasi numerik (Min-Max) + encoding kategorikal."""
    print("=" * 70)
    print("DATA TRANSFORMATION")
    print("=" * 70)

    df = df.copy()

    # (1) Min-Max Scaling untuk kolom numerik terpilih
    sample_col = "horsepower"
    before_sample = df[sample_col].head(3).tolist() if sample_col in df.columns else []
    for col in NUMERIC_TO_SCALE:
        if col in df.columns:
            df[col] = _min_max_scale(df[col].astype(float))
    after_sample = df[sample_col].head(3).tolist() if sample_col in df.columns else []
    print(f"[TRANSFORM] Min-Max Scaling pada {len(NUMERIC_TO_SCALE)} kolom numerik")
    print(f"            Contoh '{sample_col}': {before_sample} -> "
          f"{[round(v, 4) for v in after_sample]}")

    # (2) Ordinal encoding untuk horsepower-binned
    if "horsepower-binned" in df.columns:
        df["horsepower_ordinal"] = (
            df["horsepower-binned"].str.lower().map(HP_ORDINAL).astype("Int64")
        )
        df = df.drop(columns=["horsepower-binned"])
        print("[TRANSFORM] Ordinal encoding: horsepower-binned -> horsepower_ordinal "
              "(Low=0, Medium=1, High=2)")

    # (3) Frequency encoding untuk 'make' (kardinalitas tinggi)
    if "make" in df.columns:
        freq = df["make"].value_counts(normalize=True)
        df["make_freq"] = df["make"].map(freq)
        df = df.drop(columns=["make"])
        print("[TRANSFORM] Frequency encoding: make -> make_freq")

    # (4) One-Hot Encoding untuk kolom kategorikal nominal
    encode_cols = [c for c in CATEGORICAL_TO_ENCODE if c in df.columns]
    cols_before = df.shape[1]
    df = pd.get_dummies(df, columns=encode_cols, dtype=int)
    print(f"[TRANSFORM] One-Hot Encoding pada {len(encode_cols)} kolom: {encode_cols}")
    print(f"            Jumlah kolom: {cols_before} -> {df.shape[1]}")
    print("=" * 70 + "\n")
    return df


# ---------------------------------------------------------------------------
# 4. LOAD - Menyimpan hasil
# ---------------------------------------------------------------------------
def save_data(df: pd.DataFrame, path: Path) -> None:
    """Menyimpan processed dataset sebagai file CSV baru."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    print(f"[LOAD] Processed dataset disimpan ke: {_rel(path)}")
    print(f"[LOAD] Ukuran akhir: {df.shape[0]} baris x {df.shape[1]} kolom")


# ---------------------------------------------------------------------------
# Orkestrasi Pipeline (Extract -> Transform -> Load)
# ---------------------------------------------------------------------------
def run_pipeline() -> pd.DataFrame:
    """Menjalankan seluruh pipeline ETL dari awal sampai akhir."""
    print("\n" + "#" * 70)
    print("#  MENJALANKAN ETL PIPELINE - AUTOMOBILE DATASET")
    print("#" * 70 + "\n")

    df_raw = read_data(RAW_PATH)         # Extract
    inspect_data(df_raw)                 # Transform: inspeksi
    df_clean = clean_data(df_raw)        # Transform: cleaning
    df_final = transform_data(df_clean)  # Transform: transformasi
    save_data(df_final, PROCESSED_PATH)  # Load

    print("\n" + "#" * 70)
    print("#  PIPELINE SELESAI")
    print("#" * 70 + "\n")
    return df_final


if __name__ == "__main__":
    run_pipeline()
