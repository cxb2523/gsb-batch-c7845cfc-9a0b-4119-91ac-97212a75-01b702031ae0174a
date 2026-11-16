import yfinance as yf
import yaml
import uuid
import mysql.connector
from datetime import datetime
import os
import warnings
warnings.filterwarnings("ignore")

# move to the script's directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# load the configuration file
with open('../config/config.yml', 'r') as file:
    config = yaml.safe_load(file)

symbol = config["market_data"]["index_symbol"]
index_name = config["market_data"]["index_name"]
start_date = config["market_data"]["start_date"]
end_date = config["market_data"]["end_date"]
interval = config["market_data"]["interval"]

# convert end_date to datetime object
if end_date.lower() == 'today':
    end_date = datetime.today().strftime('%Y-%m-%d')
else:
    end_date = end_date

# generate run_id
run_id = f"{config['ingestion']['run_id_nifty_prefix']}_{uuid.uuid4().hex[:8]}"

# prepare mysql connection
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
""", (run_id, "index", start_time, "RUNNING"))
con.commit()

# insert query
query = """
INSERT IGNORE INTO index_prices
(index_name, trade_date, open_price, high_price, low_price, close_price, volume, run_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""

# per-symbol settlement counters
success_count = 0   # symbols successfully written
no_data_count = 0   # symbols for which Yahoo returned no data
error_count = 0     # symbols that failed during write
records_loaded = 0
fatal_error = None

try:
    # Fetch data from yfinance
    df = yf.download(symbol, start=start_date, end=end_date, interval=interval)

    if df is None or df.empty:
        no_data_count += 1
        print(f"WARNING: {symbol}: Yahoo returned no data for this run.")
    else:
        try:
            rows = []
            for trade_date, row in df.iterrows():
                rows.append((
                    index_name,
                    trade_date.date(),
                    round(float(row["Open"]), 2),
                    round(float(row["High"]), 2),
                    round(float(row["Low"]), 2),
                    round(float(row["Close"]), 2),
                    int(row["Volume"]) if not str(row["Volume"]) == 'nan' else None,
                    run_id
                ))
            cursor.executemany(query, rows)
            con.commit()
            records_loaded = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
            success_count += 1
        except Exception as write_err:
            con.rollback()
            error_count += 1
            records_loaded = 0
            print(f"ERROR: {symbol}: failed to write rows to index_prices: {write_err}")
except Exception as e:
    # unexpected failure (e.g. download blew up) -> the whole run failed
    fatal_error = e
    error_count += 1
    records_loaded = 0
    print(f"ERROR: index ingestion failed unexpectedly: {e}")

# settle the run status from the per-symbol counters
if success_count > 0 and no_data_count == 0 and error_count == 0:
    status = "SUCCESS"
elif success_count > 0:
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

print(f"\nIndex ingestion settled: status={status}, records_loaded={records_loaded} "
      f"(success={success_count}, no_data={no_data_count}, errors={error_count})")
print(f"Run ID: {run_id}")

if fatal_error is not None:
    raise fatal_error
