import requests
import pandas as pd
import sqlite3
import logging

logging.basicConfig(filename='app.log', filemode='w', format='%(name)s - %(levelname)s - %(message)s')

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


def get_all_series_for_each_release(release_id,api_key):
    
    endpoint = 'https://api.stlouisfed.org/fred/release/series'

    
    params = {
    'release_id': release_id,
    'api_key': api_key,
    'file_type': 'json'
                }
    
    response_series = requests.get(endpoint,params=params)
    return(response_series.json())


api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
db_name='../../db/db_spectral_nature.sql'

source_table_name='dim_source_data'
release_table_name='dim_release_data'
series_id_table_name='dim_series_id'

all_release_id=get_data_from_db(db_name,release_table_name)

print("\nStart of loop")
series_count=0

for release_id in all_release_id["id"]:
    print("\n********************************************************************")
    all_series_data=get_all_series_for_each_release(release_id,api_key)
    try:
        print(f"\nConverting relese data to dataframe for SOURCE ID: {release_id}")
        all_series_data_to_df= pd.DataFrame(all_series_data["seriess"])
    except Exception as e:
        logging.error("\n\nException occurred", exc_info=True)
        logging.error(e)
        logging.error("-----------\n\n",all_series_data)

        
    print("Inserting data into DB")
    insert_into_db(all_series_data_to_df, db_name, series_id_table_name)

    series_count=series_count + len(all_series_data_to_df)
    print(f"\nTotal processed series data:{series_count}")
