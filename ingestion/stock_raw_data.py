import yfinance as yf
import pandas as pd
import yaml, uuid, mysql.connector
from datetime import datetime
import os
import warnings
warnings.filterwarnings("ignore")

# move to the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# ============ Load configuration ============
with open('../config/config.yml', 'r') as file:
    config = yaml.safe_load(file)

start_date = config["market_data"]["start_date"]
end_date = datetime.today().strftime('%Y-%m-%d') if config["market_data"]["end_date"].lower() == 'today' else config["market_data"]["end_date"]
interval = config["market_data"]["interval"]
url = config["url"]["company_list"]

run_id = f"{config['ingestion']['run_id_stock_prefix']}_{uuid.uuid4().hex[:8]}"

df_list = pd.read_csv(url)
symbols = df_list['Symbol'].tolist()
tickers = [s + ".NS" for s in symbols]

con = mysql.connector.connect(
    host=config["mysql"]["host"],
    user=config["mysql"]["user"],
    password=config["mysql"]["password"],
    database=config["mysql"]["database"]
)
cursor = con.cursor()

# log ingestion start time
start_time = datetime.now()
cursor.execute("""
INSERT INTO ingestion_logs (run_id, pipeline_type, start_time, status)
VALUES (%s, %s, %s, %s)
""", (run_id, "stock", start_time, "RUNNING"))
con.commit()

query = """
INSERT IGNORE INTO raw_prices
(stock_symbol, trade_date, open_price, high_price, low_price, close_price, volume, run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

# per-symbol settlement counters
success_symbols = []   # symbols successfully written
no_data_symbols = []   # symbols for which Yahoo returned no data
error_symbols = []     # symbols that failed during write
records_loaded = 0
fatal_error = None


def build_rows(symbol, stock_df):
    """Build insert payloads for one symbol using its real ticker code."""
    rows = []
    for date, row in stock_df.iterrows():
        rows.append((
            symbol,
            date.date(),
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(row["Volume"]),
            run_id
        ))
    return rows


def settle_symbol(symbol, stock_df):
    """Write one symbol's data; a failure here never stops the other symbols."""
    global records_loaded
    if stock_df is None or stock_df.empty:
        no_data_symbols.append(symbol)
        print(f"WARNING: {symbol}: Yahoo returned no data. Skipping.")
        return
    try:
        rows = build_rows(symbol, stock_df)
        cursor.executemany(query, rows)
        con.commit()
        records_loaded += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        success_symbols.append(symbol)
    except Exception as write_err:
        con.rollback()
        error_symbols.append(symbol)
        print(f"ERROR: {symbol}: failed to write rows to raw_prices: {write_err}")


try:
    print("\nFetching ALL valid NSE symbols...")
    data = yf.download(
        tickers,
        start=start_date,
        end=end_date,
        interval=interval,
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False
    )

    if data is None or data.empty:
        # the whole batch came back empty -> every symbol is missing
        no_data_symbols.extend(symbols)
        print("ERROR: Yahoo returned no data for the entire batch.")
    elif isinstance(data.columns, pd.MultiIndex):
        print("\nPreparing per-symbol batch inserts...")
        for symbol in symbols:
            symbol_ns = symbol + ".NS"
            try:
                stock_df = data[symbol_ns].dropna()
            except KeyError:
                no_data_symbols.append(symbol)
                print(f"WARNING: {symbol_ns}: Not found on Yahoo. Skipping.")
                continue
            settle_symbol(symbol, stock_df)
    else:
        # single-level columns: yfinance fell back to a single-ticker frame.
        # Use the real symbol code — never a placeholder like "UNKNOWN".
        if len(symbols) == 1:
            print("Single-ticker structure encountered; using the real symbol code.")
            settle_symbol(symbols[0], data.dropna())
        else:
            # cannot map columns to symbols reliably -> fetch each symbol on its own
            print("Unexpected single-ticker structure for multiple symbols; fetching per symbol.")
            for symbol, ticker in zip(symbols, tickers):
                try:
                    single_df = yf.download(
                        ticker,
                        start=start_date,
                        end=end_date,
                        interval=interval,
                        auto_adjust=False,
                        progress=False
                    )
                except Exception as dl_err:
                    error_symbols.append(symbol)
                    print(f"ERROR: {symbol}: download failed: {dl_err}")
                    continue
                settle_symbol(symbol, None if single_df is None else single_df.dropna())
except Exception as e:
    # unexpected failure (e.g. batch download blew up) -> the whole run failed
    fatal_error = e
    error_symbols.extend(s for s in symbols if s not in success_symbols and s not in no_data_symbols)
    records_loaded = 0
    print(f"ERROR: stock ingestion failed unexpectedly: {e}")

# settle the run status from the per-symbol counters
if success_symbols and not no_data_symbols and not error_symbols:
    status = "SUCCESS"
elif success_symbols:
    status = "PARTIAL"
else:
    status = "FAILED"

# log ingestion end time
end_time = datetime.now()
cursor.execute("""
    UPDATE ingestion_logs
    SET end_time=%s, status=%s, records_loaded=%s
    WHERE run_id=%s
    """,
    (end_time, status, records_loaded, run_id))
con.commit()

cursor.close()
con.close()

print(f"\nStock ingestion settled: status={status}, records_loaded={records_loaded}")
print(f"  success ({len(success_symbols)}): {success_symbols}")
print(f"  no data ({len(no_data_symbols)}): {no_data_symbols}")
print(f"  errors  ({len(error_symbols)}): {error_symbols}")
print(f"Run ID: {run_id}")

if fatal_error is not None:
    raise fatal_error
