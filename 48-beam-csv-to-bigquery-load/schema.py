# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
BigQuery table schema and column definitions for the Beam CSV → BigQuery pipeline.

Centralising the schema here means pipeline.py and any downstream consumers
import from a single source of truth — no schema strings scattered across files.
"""

# Ordered list of column names matching the CSV header exactly.
CSV_COLUMNS = [
    "order_id",
    "order_date",
    "customer_name",
    "region",
    "product",
    "category",
    "quantity",
    "unit_price",
    "total_amount",
]

# BigQuery schema string (name:TYPE,...).
# For nested/repeated fields switch to a dict schema; flat sales rows fit here.
BQ_SCHEMA = (
    "order_id:STRING,"
    "order_date:DATE,"
    "customer_name:STRING,"
    "region:STRING,"
    "product:STRING,"
    "category:STRING,"
    "quantity:INTEGER,"
    "unit_price:FLOAT,"
    "total_amount:FLOAT"
)

# Human-readable schema for validation / documentation.
SCHEMA_FIELDS = [
    {"name": "order_id",       "type": "STRING",  "description": "Unique order identifier"},
    {"name": "order_date",     "type": "DATE",    "description": "Order date (YYYY-MM-DD)"},
    {"name": "customer_name",  "type": "STRING",  "description": "Customer display name"},
    {"name": "region",         "type": "STRING",  "description": "Sales region (US-East, EU, APAC, etc.)"},
    {"name": "product",        "type": "STRING",  "description": "GCP product or service name"},
    {"name": "category",       "type": "STRING",  "description": "Product category (AI, Data, Compute, etc.)"},
    {"name": "quantity",       "type": "INTEGER", "description": "Units purchased"},
    {"name": "unit_price",     "type": "FLOAT",   "description": "Price per unit (USD)"},
    {"name": "total_amount",   "type": "FLOAT",   "description": "Total order value (USD)"},
]


def print_schema() -> None:
    """Print the schema in a readable table format."""
    print(f"{'Field':<20} {'Type':<10} Description")
    print("-" * 65)
    for field in SCHEMA_FIELDS:
        print(f"  {field['name']:<18} {field['type']:<10} {field['description']}")
