"""Shared SQL Server JDBC reader for bronze dimension tables.

Pipeline utility — no datasets defined here. Imported by files in the
same directory (01_bronze/) via standard Python import.

Connection config comes from pipeline settings (SQL_HOST_NAME, SQL_DB_NAME)
and the res-chain-pipeline Databricks Secret Scope.
"""
from pyspark.sql import SparkSession, DataFrame


def read_sql_server_table(table_name: str, dbutils) -> DataFrame:
    """Read a table from the Azure SQL Server dbo schema.

    Uses SparkSession.getActiveSession() so this module can be imported
    without SDP-injected globals. Only dbutils is passed explicitly
    because there is no standard PySpark API for secret access.
    """
    spark = SparkSession.getActiveSession()
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
