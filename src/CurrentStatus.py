import numpy as np
import pandas as pd
import os
import getpass
import argparse
import robin_stocks.robinhood as r
import plotly.express as px
import time
import datetime
import pickle
from plotly.subplots import make_subplots
from plotly.graph_objs import Figure
import robin_stocks.robinhood as r


import sys
sys.path.append("./analysis_modules")
import markets

# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

def get_market_data():

    print("Fetching top movers in S&P 500 (up)...")
    top_movers_sp500_up = markets.get_top_movers_sp500('up')
    print("Fetching top movers in S&P 500 (down)...")
    top_movers_sp500_down = markets.get_top_movers_sp500('down')
    print("Fetching top 100 stocks...")
    top_100 = markets.get_top_100()
    print("Fetching top movers...")
    top_movers = markets.get_top_movers()
    print("Fetching stocks with upcoming earnings...")
    upcoming_earnings = markets.get_all_stocks_from_market_tag('upcoming-earnings')

    return {
        "top_movers_sp500_up": top_movers_sp500_up,
        "top_movers_sp500_down": top_movers_sp500_down,
        "top_100": top_100,
        "top_movers": top_movers,
        "upcoming_earnings": upcoming_earnings
    }

def get_open_stock_positions():
    """
    Returns a list of open stock positions.
    
    Returns:
    list: List of open stock positions
    """

    # Query your positions
    positions = r.get_open_stock_positions()

    # Get Ticker symbols
    tickers = [r.get_symbol_by_url(item["instrument"]) for item in positions]

    # Get your quantities
    quantities = [float(item["quantity"]) for item in positions]

    # Query previous close price for each stock ticker
    prevClose = r.get_quotes(tickers, "previous_close")

    # Query last trading price for each stock ticker
    lastPrice = r.get_quotes(tickers, "last_trade_price")

    # Calculate the profit per share
    profitPerShare = [float(lastPrice[i]) - float(prevClose[i]) for i in range(len(tickers))]

    # Calculate the percent change for each stock ticker
    percentChange = [ 100.0 * profitPerShare[i] / float(prevClose[i]) for i in range(len(tickers)) ]

    # Calcualte your profit for each stock ticker
    profit = [profitPerShare[i] * quantities[i] for i in range(len(tickers))]

    # Combine into list of lists, for sorting
    tickersPerf = list(zip(profit, percentChange, tickers))

    tickersPerf.sort(reverse=True)

    # Create a DataFrame with the performance data
    columns = ['Profit', 'Percent Change', 'Ticker']
    performance_df = pd.DataFrame(tickersPerf, columns=columns)
    performance_df['Last Price'] = lastPrice
    performance_df['Previous Close'] = prevClose

    # Display the DataFrame
    return performance_df


def get_data(mode):

    if mode == 'market':
        return get_market_data()
    
    elif mode == 'profile':
        holdings_df = r.account.build_holdings(with_dividends=True)
        user_profile = r.account.build_user_profile()
        historical_df = r.account.get_historical_portfolio(interval='week', span='5year')
        positions_df = get_open_stock_positions()
        return holdings_df, user_profile, historical_df, positions_df
    
    elif mode == 'all':

        holdings_df = r.account.build_holdings(with_dividends=True)
        user_profile = r.account.build_user_profile()
        historical_df = r.account.get_historical_portfolio(interval='week', span='5year')
        positions_df = get_open_stock_positions()
        market_data = get_market_data()      
        return holdings_df, user_profile, historical_df, positions_df, market_data

    else:
        print("Invalid mode selected.")
        return None

def NOT_create_account_plotly(mode, data):
    if mode == 'holdings':
        fig = px.pie(data, values='quantity', names='ticker', title='Portfolio Holdings')
        return fig
    
    if mode == 'profile':
        fig = px.bar(data, x='key', y='value', title='User Profile')
        return fig
    
    if mode == 'historical':
        fig = px.line(data, x='begins_at', y='adjusted_close_equity', title='Historical Portfolio Performance')
        return fig

    

def main(rh_username: str, rh_password: str, mode: str, cache_mode: str):
    """
    Main function to retrieve and cache option data for a given ticker.
    Args:
        rh_username (str): Robinhood username for login.
        rh_password (str): Robinhood password for login.
        ticker (str): Stock ticker symbol.
        current_strike_price (float): Current strike price of the option.
        cache_mode (str): Cache mode, can be 'local', 'refresh', or other modes.
    Returns:
        dict: A dictionary containing the option data plotly figure and DataFrame.
    """

    DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'
    PORTFOLIO_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'user_specific',  rh_username, 'portfolio_history')
    print(os.listdir(DATA_STORE_CORE))

    # Check if the option history store directory exists, if not, create it
    if not os.path.exists(PORTFOLIO_HISTORY_STORE):
        os.makedirs(PORTFOLIO_HISTORY_STORE)

    # Define the file path for caching
    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(PORTFOLIO_HISTORY_STORE, f"{rh_username}_portfolio_data_{current_date_str}.pkl")
    
    if cache_mode == 'local':
        if os.path.exists(cache_file_path):
            print("Loading option data from local cache...")
            with open(cache_file_path, 'rb') as f:
                status_data = pickle.load(f)
            return status_data
        else:
            print("No local cache found but local cache mode is on. Exiting...")
            print('Looked in directory ', cache_file_path)
            #exit(1)

    r.login(rh_username, rh_password)

    status_data = get_data(mode)

    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600


    # Check cache mode
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing data...")
        # Save the DataFrame to the cache file
        with open(cache_file_path, 'wb') as f:
            pickle.dump(status_data, f)
    else:
        print("Loading option data from cache...")
        with open(cache_file_path, 'rb') as f:
            status_data = pickle.load(f)

    return status_data




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


    args = parser.parse_args()
    
    rh_username = args.username
    rh_password = args.password
    force_refresh = args.force_refresh
    force_local = args.force_local
    profile = args.profile
    market = args.market
    all = args.all

    if market:
        mode = 'markets'
    elif profile:
        mode = 'profile'
    elif all:
        mode = 'all'
    else:
        print("Error: No valid mode selected.")
        exit(1)

    if force_refresh:
        cache_mode = "refresh"
    elif not force_refresh:
        cache_mode = "normal"
        if force_local:
            cache_mode = "local"
    
    if force_refresh and force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        exit(1)

    print(main(rh_username, rh_password, mode, cache_mode))

