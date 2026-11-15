"""Bronze · Menu Items

Cleansed menu items dimension snapshot from Azure SQL Server with
data quality enforcement on primary keys and pricing.
"""
from pyspark import pipelines as dp

from read_sql_server import read_sql_server_table


@dp.materialized_view(
    name="restaurant_chain_db.01_bronze.menu_items",
    comment="Cleansed menu items snapshot with data quality checks",
    table_properties={"quality": "bronze"},
)
@dp.expect_or_fail("valid_restaurant_id", "restaurant_id IS NOT NULL")
@dp.expect_or_fail("valid_item_id", "item_id IS NOT NULL")
@dp.expect("valid_price", "price > 0")
def menu_items():
    return read_sql_server_table("menu_items", dbutils)
