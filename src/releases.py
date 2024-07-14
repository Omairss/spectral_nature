import requests
import pandas as pd
import sqlite3


def insert_into_db(df_data,db_name,table_name):
    
    
    conn = sqlite3.connect(db_name)

    # Store the DataFrame in the SQLite database
    df_data.to_sql(table_name, conn, index=False, if_exists='append')

    # Close the database connection
    conn.close()


def get_data_from_db(db_name,table_name):
    
    
    # SQLite database connection
    conn = sqlite3.connect(db_name)
    
    # Query to select all data from the table
    query = f"SELECT id FROM {table_name}"
    
    # Read data from the SQLite database into a DataFrame
    df = pd.read_sql_query(query, conn)
    
    # Close the database connection
    conn.close()
    
    return(df)
    
def get_all_release_for_each_source(source_id,api_key):

    
    print(f"\n>>Get all Release for source ID: {source_id}")
    endpoint = 'https://api.stlouisfed.org/fred/source/releases'
        
    params = {
    'source_id': source_id,
    'api_key': api_key,
    "realtime_start": "2013-08-14",
    "realtime_end": "2020-08-14",
    'file_type': 'json'
                }
    
    response_release = requests.get(endpoint,params=params)
    
    if response_release.status_code==200:
        print("\n\t>>Data has been fetched successfully")
        return(response_release.json())
    else:
        print("\n\t>>API CALL UNSUCCESSFIL++++++++++++++++++++++++++++++++++")
        print(f"\n\tRESPONSE CODE: {response_release.status_code}")
    



api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
db_name='../../db/db_spectral_nature.sql'
source_table_name='dim_source_data'
release_table_name='dim_release_data'

all_source_id=get_data_from_db(db_name,source_table_name)

print("\nStart of loop")
release_count=0

for source_id in all_source_id["id"]:
    print("\n********************************************************************")
    all_release_data=get_all_release_for_each_source(source_id,api_key)
    
    print(f"\nConverting relese data to dataframe for SOURCE ID: {source_id}")
    all_release_data_to_df=pd.DataFrame(all_release_data["releases"])
    
    print("\nInserting data into DB")
    insert_into_db(all_release_data_to_df, db_name, release_table_name)

    release_count=release_count + len(all_release_data_to_df)
    print(f"\nTotal processed release data:{release_count}")
