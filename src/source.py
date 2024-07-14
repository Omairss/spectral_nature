import requests
import pandas as pd
import sqlite3


def get_data_from_db(db_name,table_name):
    
    try:
    
        # SQLite database connection
        conn = sqlite3.connect(db_name)
        
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


def insert_into_db(df_data,db_name,table_name):
    
    try:
    
    
        conn = sqlite3.connect(db_name)
    
        # Store the DataFrame in the SQLite database
        df_data.to_sql(table_name, conn, index=False, if_exists='replace')
    
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
        "realtime_start": "2000-08-14",
        "realtime_end": "2000-08-14",
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


api_key = '46ae2b0f7c69c4fa5b6f3f4710a107dc'
db_name='../../db/db_spectral_nature.sql'
table_name='dim_source_data'

all_sources_data_from_api=get_all_sources(api_key)

all_sources_data_to_df=pd.DataFrame(all_sources_data_from_api["sources"])

insert_into_db(all_sources_data_to_df,db_name,table_name)

print("print from db")
get_data_from_db(db_name,table_name)