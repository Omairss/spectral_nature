import numpy as np
import pandas as pd
import os
import getpass
import argparse
import yfinance as yf
import plotly.express as px
import time
import datetime
import pickle
from plotly.subplots import make_subplots
from plotly.graph_objs import Figure
import plotly.graph_objects as go

# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'

TECHNICAL_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'stock_technical')
print(os.listdir(DATA_STORE_CORE))

class Financials():
    def __init__(self, ticker, data_path=None, data=None):
        self.ticker = ticker
        self.data_path = TECHNICAL_HISTORY_STORE
        self.income_statement = None
        self.balance_sheet = None
        self.cash_flow = None
        self.cooldown_s = 3  # Set the cooldown period in seconds

    def get_financials(self):
        print(f"Getting financials for {self.ticker}")
        time.sleep(self.cooldown_s)
        ticker = yf.Ticker(self.ticker)
        self.income_statement = ticker.financials
        self.balance_sheet = ticker.balance_sheet
        self.cash_flow = ticker.cashflow
        return self.income_statement, self.balance_sheet, self.cash_flow

    def generate_plots(self):
        # Transpose the income statement for better visualization
        income_statement_transposed = self.income_statement.T

        # Reset the index to have dates as a column
        income_statement_transposed.reset_index(inplace=True)

        # Melt the dataframe to have a long format suitable for plotly express
        income_statement_melted = income_statement_transposed.melt(id_vars="index", var_name="Account", value_name="Amount")

        # Create a line chart for the income statement
        fig_income_statement = px.line(income_statement_melted, 
                                       x="index", 
                                       y="Amount", 
                                       color="Account", 
                                       title="Income Statement",
                                       labels={"index": "Date", "Amount": "Amount"})

        fig_income_statement.update_layout(xaxis_title="Date", yaxis_title="Amount")

        self.figs = fig_income_statement

        return

def main(ticker: str, cache_mode: str):
    """
    Main function to retrieve and cache financial data for a given ticker.
    Args:
        ticker (str): Stock ticker symbol.
        cache_mode (str): Cache mode, can be 'local', 'refresh', or other modes.
    Returns:
        dict: A dictionary containing the financial data plotly figure and DataFrame.
    """

    # Check if the technical history store directory exists, if not, create it
    if not os.path.exists(TECHNICAL_HISTORY_STORE):
        os.makedirs(TECHNICAL_HISTORY_STORE)

    # Define the file path for caching
    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(TECHNICAL_HISTORY_STORE, f"{ticker}_financial_data_{current_date_str}.pkl")
    
    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600

    # Check cache mode and load from cache if applicable
    if cache_mode == 'local' and os.path.exists(cache_file_path) and not is_cache_stale(cache_file_path):
        print("Loading financial data from local cache...")
        print(cache_file_path)
        with open(cache_file_path, 'rb') as f:
            financial_bundle = pickle.load(f)
        return financial_bundle

    # Fetch new data
    financials = Financials(ticker)
    financials.get_financials()
    financials.generate_plots()

    financial_bundle = {
        'income_statement': financials.income_statement,
        'balance_sheet': financials.balance_sheet,
        'cash_flow': financials.cash_flow,
        'figs': financials.figs
    }

    # Save the new data to the cache file if needed
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing financial data...")
        with open(cache_file_path, 'wb') as f:
            pickle.dump(financial_bundle, f)

    return financial_bundle

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Financial Data Analysis')
    parser.add_argument('--ticker', required=True, help='Stock ticker symbol')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the financial data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')

    args = parser.parse_args()
    
    ticker = args.ticker
    force_refresh = args.force_refresh
    force_local = args.force_local

    if force_refresh:
        cache_mode = "refresh"
    elif not force_refresh:
        cache_mode = "normal"
        if force_local:
            cache_mode = "local"
    
    if force_refresh and force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        exit(1)

    main(ticker, cache_mode)
