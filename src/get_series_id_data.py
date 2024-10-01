import requests
import pandas as pd
import sqlite3
import logging
import db_connect


logging.basicConfig(filename='app.log', filemode='w', format='%(name)s - %(levelname)s - %(message)s')

def insert_into_db(df_data,db_name,table_name):
    
    
    # conn = sqlite3.connect(db_name)
    conn=db_connect.get_sqlalchemy_engine_conn()

    # Store the DataFrame in the SQLite database
    df_data.to_sql(table_name, con=conn,schema='DEV_FRED_DATA', index=False, if_exists='append')

    # Close the database connection
    conn.close()


def get_data_from_db(db_name,table_name):
    # SQLite database connection
    # conn = sqlite3.connect(db_name)
    conn = db_connect.get_db_conn()
    
    # Query to select all data from the table
    query = f"SELECT DISTINCT id FROM DEV_FRED_DATA.{table_name} WHERE id NOT IN (SELECT DISTINCT series_id FROM DEV_FRED_DATA.DIM_SERIES_ID_OBSERVATIONS)"
    
    # Read data from the SQLite database into a DataFrame
    df = pd.read_sql_query(query, conn)
    
    # Close the database connection
    conn.close()
    
    return(df)


def get_series_id_observations_data(series_id,api_key):
    
    endpoint = 'https://api.stlouisfed.org/fred/series/observations'

    
    params = {
    'series_id':series_id,
    'api_key': api_key,
    'file_type': 'json'
            }
    
    observations_data = requests.get(endpoint,params=params)
    return(observations_data.json())

def get_series_id_data():
    api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
    db_name='../../db/db_spectral_nature.sql'
    source_table_name='dim_source_data'
    release_table_name='dim_release_data'
    series_id_table_name='dim_series_id'
    series_id_observations_table_name='DIM_SERIES_ID_OBSERVATIONS'

    all_series_id=get_data_from_db(db_name,series_id_table_name)

    print("\nStart of loop")
    series_count=0

    for series_id in all_series_id["id"]:
        print("\n********************************************************************")
        observations_data=get_series_id_observations_data(series_id,api_key)
        try:
            print(f"\nConverting series data to dataframe for SOURCE ID: {series_id}")
            #print(observations_data["observations"])
            all_series_data_to_df= pd.DataFrame(observations_data["observations"])
            all_series_data_to_df['series_id']=series_id
        except Exception as e:
            logging.error("\n\nException occurred", exc_info=True)
            logging.error(e)
            # logging.error("-----------\n\n",all_series_data_to_df)


        print("Inserting data into DB")
        insert_into_db(all_series_data_to_df[['series_id','date','value']], db_name, series_id_observations_table_name)

    #     series_count=series_count + len(all_series_data_to_df)
    #     print(f"\nTotal processed series data:{series_count}")


    # return("Series ID data has been fetched and inserted into DB")

def main():
    get_series_id_data()


if __name__=="__main__":
    main()