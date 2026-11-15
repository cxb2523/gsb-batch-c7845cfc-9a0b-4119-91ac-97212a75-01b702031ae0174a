"""
Generate EDA Visualization Charts
=================================
Membuat visualisasi pendukung dokumentasi:
  1. Bar chart jumlah missing values per kolom (dataset mentah)
  2. Histogram distribusi 'price' sebelum vs sesudah Min-Max Scaling

Chart disimpan ke folder documentation/ dan di-embed pada README.md.
Jalankan: python src/generate_eda_charts.py
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from pipeline import RAW_PATH, NUMERIC_TO_SCALE, clean_data, _min_max_scale

ROOT_DIR = Path(__file__).resolve().parent.parent
DOC_DIR = ROOT_DIR / "documentation"

# Palet warna tervalidasi (colorblind-safe)
BLUE = "#2a78d6"      # kategori slot 1
AQUA = "#1baf7a"      # kategori slot 2
INK = "#0b0b0b"
MUTED = "#898781"
GRID = "#e1e0d9"


def _style_axes(ax):
    """Terapkan gaya minimalis: hilangkan spine berlebih, grid halus."""
    for spine in ["top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color(MUTED)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.set_axisbelow(True)


def chart_missing_values(raw: pd.DataFrame) -> None:
    """Bar chart horizontal: jumlah missing values per kolom (>0)."""
    miss = raw.isna().sum()
    miss = miss[miss > 0].sort_values()

    fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
    bars = ax.barh(miss.index, miss.values, color=BLUE, height=0.62)
    # direct label di ujung batang
    for bar, val in zip(bars, miss.values):
        ax.text(val + 0.08, bar.get_y() + bar.get_height() / 2,
                str(int(val)), va="center", ha="left",
                fontsize=9, color=INK, fontweight="bold")

    ax.set_xlim(0, miss.values.max() + 1)
    ax.set_xlabel("Jumlah missing values", fontsize=10, color=INK)
    ax.set_title("Missing Values per Kolom — Dataset Mentah",
                 fontsize=12, fontweight="bold", color=INK, pad=12)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(False)
    _style_axes(ax)
    fig.tight_layout()
    out = DOC_DIR / "missing-values.png"
    _save_clean(fig, out)
    print(f"[CHART] {out.name} disimpan")


def chart_scaling_before_after(raw: pd.DataFrame) -> None:
    """Histogram 'price' sebelum vs sesudah Min-Max Scaling (dua panel)."""
    clean = clean_data(raw)  # bersihkan dulu (mengisi missing)
    before = clean["price"].astype(float)
    after = _min_max_scale(before)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4), dpi=150)

    ax1.hist(before, bins=20, color=BLUE, edgecolor="white", linewidth=0.6)
    ax1.set_title("Sebelum — skala asli (USD)", fontsize=11,
                  fontweight="bold", color=INK)
    ax1.set_xlabel("price", fontsize=10, color=INK)
    ax1.set_ylabel("Frekuensi", fontsize=10, color=INK)

    ax2.hist(after, bins=20, color=AQUA, edgecolor="white", linewidth=0.6)
    ax2.set_title("Sesudah — Min-Max Scaling [0–1]", fontsize=11,
                  fontweight="bold", color=INK)
    ax2.set_xlabel("price (scaled)", fontsize=10, color=INK)
    ax2.set_ylabel("Frekuensi", fontsize=10, color=INK)

    for ax in (ax1, ax2):
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
        ax.xaxis.grid(False)
        _style_axes(ax)

    fig.suptitle("Distribusi 'price' — Sebelum vs Sesudah Normalisasi",
                 fontsize=13, fontweight="bold", color=INK, y=1.02)
    fig.tight_layout()
    out = DOC_DIR / "scaling-before-after.png"
    _save_clean(fig, out)
    print(f"[CHART] {out.name} disimpan")


def _save_clean(fig, path: Path) -> None:
    """Simpan figur lalu bersihkan metadata software dari PNG."""
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    from PIL import Image
    img = Image.open(path)
    clean = Image.new(img.mode, img.size)
    clean.putdata(list(img.getdata()))
    clean.save(path)


def main() -> None:
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    raw = pd.read_csv(RAW_PATH)
    chart_missing_values(raw)
    chart_scaling_before_after(raw)
    print("[CHART] Selesai.")


if __name__ == "__main__":
    main()
