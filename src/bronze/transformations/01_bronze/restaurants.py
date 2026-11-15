"""Bronze · Restaurants

Cleansed restaurant dimension snapshot from Azure SQL Server with
data quality enforcement.
"""
from pyspark import pipelines as dp

from read_sql_server import read_sql_server_table


@dp.materialized_view(
    name="restaurant_chain_db.01_bronze.restaurants",
    comment="Cleansed restaurants snapshot with data quality checks",
    table_properties={"quality": "bronze"},
)
@dp.expect_or_fail("valid_restaurant_id", "restaurant_id IS NOT NULL")
def restaurants():
    return read_sql_server_table("restaurants", dbutils)
