import os
import traceback
from dataclasses import dataclass
from datetime import datetime as date
from typing import Optional

import pandas as pd
import matplotlib.pyplot as plt
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from config.settings import PDF_PATH, CHART_PATH
from src.data_source import load_clean_data
from src.email_report import send_email_report

SUMMARY_CSV_PATH = "output/weekly_customer_spend.csv"
SUMMARY_COLUMNS = ["customer_id", "sale_amount"]

STATUS_SUCCESS = "success"
STATUS_EMPTY_WINDOW = "empty_window"
STATUS_ERROR = "error"


@dataclass
class ReportResult:
    """Machine-readable outcome of generate_and_send_report()."""
    status: str                    # "success" | "empty_window" | "error"
    valid_rows: int = 0            # rows remaining after cleaning
    dropped_rows: int = 0          # rows dropped (unparseable invoice_date)
    window_rows: int = 0           # valid rows inside the 7-day window
    csv_path: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self):
        return self.status == STATUS_SUCCESS


def log(msg):
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{date.now()}] {msg}\n")


def log_exception():
    with open("log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{date.now()}] ERROR:\n")
        f.write(traceback.format_exc())
        print(traceback.format_exc())


def generate_and_send_report(
    source_type=None, current_date=None, **kwargs
):
    """Run the full collect -> clean -> report pipeline.

    Always returns a ReportResult; failures are logged to log.txt AND
    reported via ReportResult(status="error", error=...) instead of
    being silently swallowed.
    """
    try:
        log(f"📦 Loading data from {source_type} source...")
        df, dropped_rows = load_clean_data(source_type=source_type, **kwargs)
        valid_rows = len(df)
        if dropped_rows:
            log(f"⚠️ Dropped {dropped_rows} row(s) with unparseable invoice_date")
        log(f"📦 {valid_rows} valid row(s) after cleaning")

        recent = filter_last_7_days(df, current_date=current_date)

        if recent.empty:
            log("⚠️ No valid sales in the last 7 days — writing header-only "
                "summary CSV, skipping chart/PDF/email")
            remove_stale_artifacts()
            save_report(empty_summary(), SUMMARY_CSV_PATH)
            return ReportResult(
                status=STATUS_EMPTY_WINDOW,
                valid_rows=valid_rows,
                dropped_rows=dropped_rows,
                window_rows=0,
                csv_path=SUMMARY_CSV_PATH,
            )

        recent = add_sale_amount(recent)
        summary = summarize_sales(recent)
        generate_chart(summary)
        generate_pdf(summary)

        log("📦 Saving report to CSV...")
        save_report(summary, SUMMARY_CSV_PATH)

        log("📦 Sending email report...")

        # Change the subject and body as per your needs
        send_email_report(
            subject="Weekly Customer Spend Report",
            body="Attached is the latest customer spend summary.",
            attachment_path=SUMMARY_CSV_PATH,
        )

        return ReportResult(
            status=STATUS_SUCCESS,
            valid_rows=valid_rows,
            dropped_rows=dropped_rows,
            window_rows=len(recent),
            csv_path=SUMMARY_CSV_PATH,
        )

    except Exception as exc:
        log_exception()
        return ReportResult(
            status=STATUS_ERROR,
            error=f"{type(exc).__name__}: {exc}",
        )


def remove_stale_artifacts():
    """Delete previous chart/PDF so stale files are never mistaken for
    output of the current (empty-window) run."""
    for path in (CHART_PATH, PDF_PATH):
        if os.path.exists(path):
            os.remove(path)
            log(f"🗑️ Removed stale artifact: {path}")


def filter_last_7_days(df, current_date=None):
    if current_date is None:
        current_date = pd.to_datetime(date.today())
    else:
        current_date = pd.to_datetime(current_date)

    seven_days_ago = current_date - pd.Timedelta(days=7)
    return df[(df["invoice_date"] >= seven_days_ago) & (df["invoice_date"] <= current_date)].copy()


def add_sale_amount(df):
    df["sale_amount"] = (df["quantity"] * df["price"]).round(2)
    return df


def empty_summary():
    return pd.DataFrame(columns=SUMMARY_COLUMNS)


def summarize_sales(df):
    summary = df.groupby("customer_id")["sale_amount"].sum().reset_index()
    summary.sort_values("sale_amount", ascending=False, inplace=True)
    return summary


def generate_chart(summary):
    top5_df = summary.head(5)

    # Create the chart
    plt.figure(figsize=(8, 4))
    plt.bar(top5_df["customer_id"], top5_df["sale_amount"])
    plt.xlabel("Customer ID")
    plt.ylabel("Total Spend")
    plt.title("Top 5 Customers - Weekly Spend")
    plt.tight_layout()

    # Save the image
    _ensure_parent_dir(CHART_PATH)
    plt.savefig(CHART_PATH)
    plt.close()


def generate_pdf(summary):
    top5_df = summary.head(5)

    # Set up PDF
    _ensure_parent_dir(PDF_PATH)
    doc = SimpleDocTemplate(PDF_PATH, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # title
    elements.append(
        Paragraph("🧾 Weekly Customer Spend Report", styles['Title']))
    elements.append(Spacer(1, 12))

    # Add chart
    elements.append(Image(CHART_PATH, width=400, height=200))
    elements.append(Spacer(1, 12))

    # Table: Top 5 customers
    top5_table_data = [["Customer ID", "Total Spend (₹)"]]
    for _, row in top5_df.iterrows():
        top5_table_data.append(
            [row["customer_id"], f"{row['sale_amount']:.2f}"])

    table = Table(top5_table_data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]))

    elements.append(table)

    # Build the PDF
    doc.build(elements)
    print("📄 PDF report generated:", PDF_PATH)


def save_report(df, path):
    _ensure_parent_dir(path)
    df.to_csv(path, index=False)


def _ensure_parent_dir(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
