import requests
import pandas as pd
# import sqlite3


import db_connect

def get_data_from_db(db_name,table_name):
    
    try:
    
        # SQLite database connection
        # conn = sqlite3.connect(db_name)

        conn = db_test.get_db_conn()
        
        # Query to select all data from the table
        query = f"SELECT * FROM {table_name}"
        
        # Read data from the SQLite database into a DataFrame
        df = pd.read_sql_query(query, conn)
        
        # Close the database connection
        conn.close()
        
        # Print the DataFrame
        print(df)
        
    except Exception as e:
        print(f'''
              ------------------------------------------------------------------------------
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ERROR in FUNC: get_data_from_db
                                       
              {e}
              
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ------------------------------------------------------------------------------
              ''')        


def insert_into_db(df_data,table_name):
    
    try:
    
    

    
        # conn = sqlite3.connect(db_name)

        
        # Store the DataFrame in the SQLite database
        # with engine.connect() as cnn:

        conn=db_connect.get_sqlalchemy_engine_conn()
        df_data.to_sql(table_name, con=conn,schema='DEV_FRED_DATA', index=False, if_exists='replace')
    
        # Close the database connection
        conn.close() 

    except Exception as e:
        print(f'''
              ------------------------------------------------------------------------------
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ERROR in FUNC: insert_into_db
                                       
              {e}
              
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ------------------------------------------------------------------------------
              ''')
def get_all_sources(api_key):
    
    try:
        print("\n--------------------------------------------------------------")
    
        print("Getting all sources")
        
        endpoint = 'https://api.stlouisfed.org/fred/sources'
        
        params = {
        'api_key': api_key,
        'file_type': 'json'
                    }
    
        source_response = requests.get(endpoint,params=params)
        
        if source_response.status_code==200:
            print("\n\t>>Data has been fetched successfully")
            return(source_response.json())
        else:
            print("\n\t>>API CALL UNSUCCESSFIL++++++++++++++++++++++++++++++++++")
            print(f"\n\tRESPONSE CODE: {source_response.status_code}")
        
    except Exception as e:
        print(f'''
              ------------------------------------------------------------------------------
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ERROR in FUNC: get_all_sources
                                       
              {e}
              
              >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
              ------------------------------------------------------------------------------
              ''')
              
              


# def main():
#     api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
#     get_all_sources(api_key)

# if __name__=="__main__":
#     main()

def get_sources():
    api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
    db_name='db_spectral_nature.sqlite'

    table_name='dim_source_data'

    all_sources_data_from_api=get_all_sources(api_key)

    all_sources_data_to_df=pd.DataFrame(all_sources_data_from_api["sources"])

    insert_into_db(all_sources_data_to_df,table_name)

    return ("Sources data has been fetched and inserted into the database")


