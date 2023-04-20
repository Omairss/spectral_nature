import os
import time
import requests
import pandas as pd
import matplotlib.pyplot as plt
import plotly.express as px
#from tqdm.notebook import tqdm_notebook as tqdm
from datetime import datetime
import argparse
import json
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from fredapi import Fred
from tqdm import tqdm
import logging


SECRET_PATH = '../secrets'
DATA_PATH = '../data'
LOG_PATH = '../log'
STOCK_DATA_PATH = os.path.join(DATA_PATH, 'stock_data')

def log_init():
    
    # Set up the logger
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)

    # Set up the file handler
    log_file = f"{datetime.now().strftime('%Y-%m-%d-%H')}.log"
    file_handler = logging.FileHandler(os.path.join(LOG_PATH,log_file))

    # Set up the formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    # Add the file handler to the logger
    logger.addHandler(file_handler)
    
    return logger

def get_fred_series_list():
    
    base_url = "https://fred.stlouisfed.org/tags/series?pageID="
    csv_filename = 'fred_series_id_large.csv'
    csv_filename_path = os.path.join(DATA_PATH, csv_filename)
    page_num = 1
    all_series = []

    while True:
        try:
            print(page_num)
            url = base_url + str(page_num)
            
            res = requests.get(url)
            time.sleep(1)

            soup = BeautifulSoup(res.content, 'html.parser')
            series_list = soup.find_all('a', {'class': 'series-title'})
            if len(series_list) == 0:
                break
            for series in series_list:
                series_id = series['href'].split('/')[-1]
                all_series.append(series_id)
            with open(csv_filename_path, "a") as f:
                f.write("\n".join(all_series) + "\n")
            print(f"Page {page_num} done.")
            page_num += 1
            all_series = []
        
        except Exception as e:
            print(e)
        
    print("Scraping complete.")

def get_fred_data(api_key, series_ids, logger):
    csv_filename = 'fred_data.csv'
    csv_filename_path = os.path.join(DATA_PATH, csv_filename)

    # Check if cached data exists and is less than a month old
    if os.path.exists(csv_filename):
        file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(csv_filename))
        if file_age < timedelta(days=30):
            # Read data from CSV file
            df = pd.read_csv(csv_filename)
            return df[df['series_id'].isin(series_ids)]

    # Read data from FRED API
    fred = Fred(api_key=api_key)
    df = pd.DataFrame()
    for series_id in tqdm(series_ids):
        time.sleep(0.5)
        try:
            data = fred.get_series(series_id)
            logger.info(series_id)
            logger.info((data[:5]))
            df = df.append(pd.DataFrame({'series_id': [series_id] * len(data), 'date': data.index, 'value': data.values}))
        except Exception as e:
            print(series_id + str(e))
    try:
        df.to_csv(csv_filename_path, index=False)
    except FileNotFoundError as e:
        os.mkdir(csv_filename)
        df.to_csv(csv_filename_path, index=False)

    return df

def get_ticker_list():
    # Doesn't work
    from bs4 import BeautifulSoup
    headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36 Edge/16.16299'
    }
    url = 'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=%25&count=1000&output=xml'
    response = requests.get(url, headers=headers)
    soup = BeautifulSoup(response.content, 'html.parser')

    return soup

    companies = []

    for company in tqdm(soup.find_all('company-info')):
        name = company.find('conformed-name').text
        ticker = company.find('symbol').text
        companies.append({'name': name, 'ticker': ticker})
    
    return companies

def get_ticker_list_2():
    
    #Doesn't work 
    url = 'https://www.nasdaq.com/api/v1/screener?page=0&pageSize=50000'
    df = pd.read_csv(url)

    # Filter out non-US companies
    us_companies = df[df['country'] == 'United States']
    return us_companies[['symbol', 'name']]


def get_batch_stock_data(api_keys, symbol_list, logger, outputsize='full',):
    
    SKIP_CACHED = True
    

    # Define rate limits (requests per minute and requests per day)
    requests_per_minute = 4
    requests_per_day = 490

    # Initialize counters for requests per minute and requests per day
    requests_count_minute = 0
    requests_count_day = 0

    api_key = api_keys.pop()

    for symbol in tqdm(symbol_list, desc='Processing items', unit='item'):

        # Check if requests per minute limit has been exceeded
        if requests_count_minute >= requests_per_minute:
            # Sleep for 60 seconds (1 minute)
            #print('Requests per minute limit exceeded. Sleeping for 60 seconds...')
            logger.info('Requests per minute limit exceeded. Sleeping for 60 seconds...')
            time.sleep(60)
            requests_count_minute = 0

        # Check if requests per day limit has been exceeded
        if requests_count_day >= requests_per_day:
            # Try a new API token from the list
            if api_keys:
                api_key = api_keys.pop()
                requests_count_day = 0
                #print(f'Requests per day limit exceeded. Trying a new API token: {api_token}')
                logger.info(f'Requests per day limit exceeded. Trying a new API token: {api_token}')
            else:
                #print('No API tokens available. Exiting...')
                logger.info(f'No API tokens available. Exiting...')
                return False

        csv_filename = f'{symbol}.csv'
        csv_filename_path = os.path.join(STOCK_DATA_PATH, csv_filename)
        url = f'https://www.alphavantage.co/query?function=TIME_SERIES_DAILY_ADJUSTED&symbol={symbol}&outputsize={outputsize}&apikey={api_key}&datatype=csv'

        if SKIP_CACHED == True:
            if os.path.exists(csv_filename_path):
                file_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(csv_filename_path))
                if file_age < timedelta(days=1):
                    continue

        try:
            df = pd.read_csv(url)
            df.index = pd.to_datetime(df.index)
            df.sort_index(inplace=True)
            #print(df.head(2))
            logger.info(symbol)
            logger.info(df.head(2))
            logger.info(len(df))
            df.to_csv(csv_filename_path)  # Cache data to CSV file
        except Exception as e:
            print(f'Error fetching data for {symbol}')
            logger.error(f'Error fetching data for {symbol}')
        
        # Update the request counters
        requests_count_minute += 1
        requests_count_day += 1

    return True



def pull_data(mode, logger):

    with open(os.path.join(SECRET_PATH,'fred_api_key.txt')) as f:
        fred_api_key = f.read()

    with open(os.path.join(SECRET_PATH,'alpha_vantage_api_key.txt')) as f:
        av_api_keys = f.read().splitlines()

    ##Read Config/Meta
    series_ids = list(pd.read_csv('../data/fred_series_id_large.csv', header = None)[0])   
    nasdaq_tickers = pd.read_csv('../data/nasdaq_ticker_list.csv') 
    
    if mode == 'stock':
        status = get_batch_stock_data(av_api_keys, list(nasdaq_tickers['Symbol']), logger, outputsize='full')
        return status

    if mode == 'fred':
        #series_ids = get_fred_series_list()
        fred_df = get_fred_data(fred_api_key, series_ids, logger)
        return fred_df
    
if __name__ == '__main__':

    # Create an argument parser
    parser = argparse.ArgumentParser(description="Example of using argparse for named command line arguments")

    # Add named arguments
    parser.add_argument('--mode', type=str, help='select [fred,stock]')

    # Parse the command line arguments
    args = parser.parse_args()

    # Access the named arguments
    mode = args.mode

    logger = log_init()

    data_df = pull_data(mode, logger)