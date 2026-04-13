import pyodbc
from sqlalchemy import create_engine
import urllib

import fred_config


def get_sqlalchemy_engine_conn():
    connection_str = fred_config.build_sql_odbc_connection_string()
    quoted = urllib.parse.quote_plus(connection_str)
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={quoted}")
    conn = engine.connect()
    return conn


def get_db_conn():
    connection_str = fred_config.build_sql_odbc_connection_string()
    conn = pyodbc.connect(connection_str)
    return conn
