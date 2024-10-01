import pyodbc 
from sqlalchemy import create_engine
import urllib



def get_sqlalchemy_engine_conn():

    server_info = 'spectral-nature-server.database.windows.net' 
    driver='{ODBC Driver 17 for SQL Server}'
    database_info = 'spectral-nature-db' 
    user_info = 'sn-sql-db' 
    password_info = 'monkey-loft-998644' 

    connection_str = f'DRIVER={driver};SERVER={server_info};DATABASE={database_info};UID={user_info};PWD={password_info};'


    quoted = urllib.parse.quote_plus(connection_str)
    engine = create_engine(f'mssql+pyodbc:///?odbc_connect={quoted}')
    conn=engine.connect()
    return conn


def get_db_conn():
    server_info = 'spectral-nature-server.database.windows.net' 
    driver='{ODBC Driver 17 for SQL Server}'
    database_info = 'spectral-nature-db' 
    user_info = 'sn-sql-db' 
    password_info = 'monkey-loft-998644' 

    connection_str = f'DRIVER={driver};SERVER={server_info};DATABASE={database_info};UID={user_info};PWD={password_info};'
    conn = pyodbc.connect(connection_str)
    return conn

