import numpy as np
import pandas as pd
import os
import getpass
import argparse
import robin_stocks.robinhood as r
import plotly.express as px
import time
from datetime import datetime
import pickle
from plotly.subplots import make_subplots
from plotly.graph_objs import Figure
import robin_stocks.robinhood as r
import plotly.graph_objects as go
import numpy as np
import requests

import sys
sys.path.append("./analysis_modules")
sys.path.append("../analysis_modules")

import markets
import TechnicalAnalyzer
from datetime import timedelta

# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")



class MarketExplorer():

    def __init__(self, rh_username, rh_password):
        self.rh_username = rh_username
        self.rh_password = rh_password
        self.r = r
        self.r.login(self.rh_username, self.rh_password)
        self.technical_bundle_cache = {}
        self.news_bundle_cache = {}

    def get_market_data(self):

        cooldown_s = 5

        print("Fetching top movers in S&P 500 (up)...")
        time.sleep(cooldown_s)
        top_movers_sp500_up = markets.get_top_movers_sp500('up')

        print("Fetching top movers in S&P 500 (down)...")
        time.sleep(cooldown_s)
        top_movers_sp500_down = markets.get_top_movers_sp500('down')

        print("Fetching top 100 stocks...")
        time.sleep(cooldown_s)
        top_100 = markets.get_top_100()

        print("Fetching top movers...")
        time.sleep(cooldown_s)
        top_movers = markets.get_top_movers()

        print("Fetching stocks with upcoming earnings...")
        time.sleep(cooldown_s)
        upcoming_earnings = markets.get_all_stocks_from_market_tag('upcoming-earnings')

        return {
            "top_movers_sp500_up": top_movers_sp500_up,
            "top_movers_sp500_down": top_movers_sp500_down,
            "top_100": top_100,
            "top_movers": top_movers,
            "upcoming_earnings": upcoming_earnings
        }
    
    def plot_market_data(self, group_dictionary, start_date):
        """
        Plots the percentage return from the start date for the given market data.

        Parameters:
        market_data (dict): Dictionary containing market data with tickers and their historical prices.
        start_date (str): Relative start date (e.g., '5yr', '1yr', '1mo').

        Returns:
        plotly.graph_objects.Figure: Plotly figure with all tickers in one timechart.
        """

        # Notes disabled for now but cab be enables using this
        NOTES_ENABLED = False
        notes = {
                    "INTC": [
                        {"date": "2020-03-02", "note": "New product launch"},
                        {"date": "2021-01-15", "note": "Major earnings surprise"}
                    ],
                    "SMCI": [
                        {"date": "2020-09-10", "note": "Acquisition announcement"}
                    ]
                }

        # Calculate the start date
        now = datetime.now()
        if 'yr' in start_date:
            years = int(start_date.replace('yr', ''))
            start_date = now - timedelta(days=365 * years)
        elif 'mo' in start_date:
            months = int(start_date.replace('mo', ''))
            start_date = now - timedelta(days=30 * months)
        else:
            raise ValueError("Invalid start date format. Use '5yr', '1yr', '1mo', etc.")

        fig = go.Figure()

        for ticker_dict in group_dictionary:

            print(f"Plotting technicals for {ticker_dict['symbol']}")

            if ticker_dict['symbol'] in self.technical_bundle_cache:
                technical_bundle = self.technical_bundle_cache[ticker_dict['symbol']]
            
            else:
                technical_bundle = TechnicalAnalyzer.main(self.rh_username, self.rh_password, ticker_dict['symbol'], 'normal')
                self.technical_bundle_cache[ticker_dict['symbol']] = technical_bundle
            
            historical_data = pd.DataFrame({
                'begins_at': technical_bundle['dates'],
                'close_price': technical_bundle['closes']
            })

            historical_data['begins_at'] = pd.to_datetime(historical_data['begins_at'])
            filtered_data = historical_data[historical_data['begins_at'] >= start_date]
            if filtered_data.empty:
                continue

            dates = filtered_data['begins_at'].dt.strftime('%Y-%m-%d').tolist()
            prices = filtered_data['close_price'].astype(float).tolist()

            # Plot the actual prices instead of percentage returns
            fig.add_trace(go.Scatter(x=dates, y=prices, mode='lines', name=ticker_dict['symbol']))

            if NOTES_ENABLED:
            # Place the star marker at the actual price on the note date
                if notes and ticker_dict['symbol'] in notes:
                    for single_note in notes[ticker_dict['symbol']]:
                        note_date = single_note['date']
                        note_matches = [d for d in filtered_data if d['begins_at'].startswith(note_date)]
                        if note_matches:
                            note_price = float(note_matches[0]['close_price'])
                            fig.add_trace(go.Scatter(
                                x=[note_date],
                                y=[note_price],
                                mode='markers+lines',
                                marker=dict(size=20, color='red', symbol='star'),
                                name=f"{ticker_dict['symbol']} note",
                                text=[single_note['note']],
                                hoverinfo='text'
                            ))

        fig.update_layout(title='Stock Price over Time',
                        xaxis_title='Date',
                        yaxis_title='Stock Price',
                        template='plotly_dark')
        return fig
    
    def stocknews_api_endpoint(self, group_dictionary):
        

        cooldown_s = 5
        for ticker_dict in group_dictionary:

            print(f"Getting news for {ticker_dict['symbol']}")

            if not ticker_dict['symbol'] in self.technical_bundle_cache:
                
                url = "https://stocknews.ai/api/news/ai-search"
                params = {
                    "q": ticker_dict['symbol'],
                    "token": "internal-test-token"
                }
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"
                }
                time.sleep(cooldown_s)
                response = requests.get(url, params=params, headers=headers)
                response.raise_for_status()   
                news_bundle = response.json()
                self.news_bundle_cache[ticker_dict['symbol']] = news_bundle
        
        return self.news_bundle_cache

    def FORLATER_google_custom_search(query, api_key, cx, num=5):
        url = "https://www.googleapis.com/customsearch/v1"
        api_key = ""
        cx = ""
        
        params = {
            "key": api_key,
            "cx": cx,
            "q": query,
            "num": num
        }
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json().get("items", [])
    
    def MAYBE_get_group_technicals(self, group_dictionary):

        for ticker_dict in group_dictionary:
            print(f"Getting technicals for {ticker_dict['symbol']}")
            if ticker_dict['symbol'] in self.technical_bundle_cache:
                technical_bundle = self.technical_bundle_cache[ticker_dict['symbol']]
            
            else:
                technical_bundle = TechnicalAnalyzer.main(rh_username, rh_password, ticker_dict['symbol'], 'normal')
                self.technical_bundle_cache[ticker_dict['symbol']] = technical_bundle

def is_cache_stale(file_path):
    if not os.path.exists(file_path):
        return True
    file_mod_time = datetime.fromtimestamp(os.path.getmtime(file_path))
    return (datetime.now() - file_mod_time).total_seconds() > 3 * 3600


def main(rh_username: str, rh_password: str, cache_mode: str, TEST: bool = False):
    
    
    DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'
    NEWS_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'news')

    if TEST:
        print("TEST mode: Using only a subset of data")

    if not os.path.exists(NEWS_HISTORY_STORE):
        os.makedirs(NEWS_HISTORY_STORE)

    current_date_str = datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(NEWS_HISTORY_STORE, f"{rh_username}_news_data_{current_date_str}.pkl")

    print("cache file path: ", cache_file_path)
    os.listdir(NEWS_HISTORY_STORE)

    print ("cache file path exists: ", os.path.exists(cache_file_path))
    print ("cache mode: ", cache_mode)

    if cache_mode == 'local' and os.path.exists(cache_file_path):
        print("Loading data from local cache...")
        with open(cache_file_path, 'rb') as f:
            market_group_data = pickle.load(f)
        return market_group_data

    r.login(rh_username, rh_password)
    m = MarketExplorer(rh_username, rh_password)
    markets = m.get_market_data()

    if TEST:
        markets = {k: markets[k] for k in list(markets)[:2]}
        print(f"TEST mode: Using only a subset of data {markets.keys()}")

    market_group_data = {}
    for group_name, group_data in markets.items():

        print(f"\n\nProcessing group: {group_name}")

        market_group_data[group_name] = {
            'fig': m.plot_market_data(group_data, '5yr'),
            'news': m.stocknews_api_endpoint(group_data)
        }

    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing data...")
        with open(cache_file_path, 'wb') as f:
            pickle.dump(market_group_data, f)

    return market_group_data


if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Robinhood Portfolio Performance')
    parser.add_argument('--username', required=True, help='Robinhood account username')
    parser.add_argument('--password', required=True, help='Robinhood account password')
    parser.add_argument('--profile', action='store_true', help='Force refresh the option data')
    parser.add_argument('--market', action='store_true', help='Force refresh the option data')
    parser.add_argument('--all', action='store_true', help='Force refresh the option data')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the option data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')
    parser.add_argument('--test', action='store_true', help='Test mode, use only a subset of data')

    args = parser.parse_args()
    
    rh_username = args.username
    rh_password = args.password
    force_refresh = args.force_refresh
    force_local = args.force_local
    test = args.test

    if force_refresh:
        cache_mode = "refresh"
    elif not force_refresh:
        cache_mode = "normal"
        if force_local:
            cache_mode = "local"
    
    if force_refresh and force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        exit(1)
    
    print(main(rh_username, rh_password, cache_mode, test))

