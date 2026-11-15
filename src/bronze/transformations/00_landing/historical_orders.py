"""Landing · Historical Orders Snapshot

Raw, untouched snapshot of dbo.historical_orders from Azure SQL Server.
Published to 00_landing — JSON parsing and total recomputation happen
downstream in the bronze orders streaming table.
"""
from pyspark import pipelines as dp


def _read_sql_server(table_name: str):
    return (
        spark.read.format("sqlserver")
        .option("host", spark.conf.get("SQL_HOST_NAME"))
        .option("port", "1433")
        .option("database", spark.conf.get("SQL_DB_NAME"))
        .option("user", dbutils.secrets.get(scope="res-chain-pipeline", key="db_admin_username"))
        .option("password", dbutils.secrets.get(scope="res-chain-pipeline", key="db_admin_password"))
        .option("encrypt", "true")
        .option("dbtable", f"dbo.{table_name}")
        .option("fetchSize", "10000")
        .option("connectRetryCount", "3")
        .option("connectRetryInterval", "10")
        .option("loginTimeout", "60")
        .load()
    )


@dp.materialized_view(
    name="restaurant_chain_db.00_landing.historical_orders",
    comment="Raw snapshot of dbo.historical_orders from Azure SQL Server",
    table_properties={"quality": "landing"},
)
def historical_orders():
    return _read_sql_server("historical_orders")
