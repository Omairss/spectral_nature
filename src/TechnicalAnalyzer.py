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
import numpy as np
from scipy.signal import stft
import pandas as pd

import plotly.graph_objects as go


# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'

TECHNICAL_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'stock_technical')
print(os.listdir(DATA_STORE_CORE))


class Technicals():
    def __init__(self, ticker, data_path=None, data=None):
        self.ticker = ticker
        self.data_path = TECHNICAL_HISTORY_STORE
        self.historicals = data['historicals'] if data and 'historicals' in data else None
        self.fundamentals = data['fundamentals'] if data and 'fundamentals' in data else None

    def get_historicals(self, interval='day', span='5year'):
        print(f"Getting historicals for {self.ticker}")
        self.historicals = r.stocks.get_stock_historicals(self.ticker, interval=interval, span=span)
        print(f"Got historicals for {self.ticker} with {len(self.historicals)} records")
        return self.historicals

    def get_fundamentals(self):
        print(f"Getting fundamentals for {self.ticker}")
        self.fundamentals = r.stocks.get_fundamentals(self.ticker)
        return self.fundamentals
    
    def preprocess_close(self, historicals):

        # Convert historical data to DataFrame
        historicals_df = pd.DataFrame(historicals)

        # Convert 'begins_at' to datetime
        historicals_df['begins_at'] = pd.to_datetime(historicals_df['begins_at'])

        # Convert 'close_price' to numeric
        historicals_df['close_price'] = pd.to_numeric(historicals_df['close_price'], errors='coerce')

        historicals_df['stationary_close'] = np.log(historicals_df['close_price']) - np.log(historicals_df['close_price'].shift(1))
        #historicals_df['stationary_close'].fillna(0, inplace=True)

        # Handle missing values (e.g., forward fill)
        historicals_df.fillna(method='ffill', inplace=True)

        # Extract the closing prices
        closes = historicals_df['close_price'].values
        closes_stationarized = historicals_df['stationary_close'].values

        # Extract the dates
        dates = historicals_df['begins_at'].values
        # Convert dates to datetime objects
        dates = pd.to_datetime(dates)
        # Convert dates to a format suitable for Plotly
        dates = [date.strftime('%Y-%m-%d') for date in dates]

        return dates, closes, closes_stationarized

    def generate_plots(self, dates, closes, closes_stationarized):

        # Spectrogram of seasonality (time vs. amplitude)
        nperseg = min(256, len(closes_stationarized))  # Set nperseg to a value less than or equal to the length of the input data
        f, t, Zxx = stft(closes_stationarized, fs=1)

        # Simple line chart with upper/lower price channels
        closes_ser = pd.Series(closes)
        roll_window = 20
        upper_channel = closes_ser.rolling(roll_window).max()
        lower_channel = closes_ser.rolling(roll_window).min()

        # Scatter plot of % change over past 3 days (X) vs. next 2 days (Y)
        pct_past_3 = []
        pct_future_2 = []
        for i in range(len(closes)):
            x_val = ((closes[i] - closes[i-3]) / closes[i-3] * 100) if i >= 3 else None
            y_val = ((closes[i+2] - closes[i]) / closes[i] * 100) if i+2 < len(closes) else None
            pct_past_3.append(x_val)
            pct_future_2.append(y_val)
        valid_x = [x for x in pct_past_3 if x is not None]
        valid_y = [y for y in pct_future_2 if y is not None]

        # Combine all figures into one subplot
        fig_combined = make_subplots(
            rows=4, cols=1,
            subplot_titles=("Histogram of Stock Prices", "Spectrogram (STFT)", "Price with Channels", "Scatter of 3-Day vs. 2-Day % Changes")
        )

        # Add histogram to subplot
        fig_combined.add_trace(go.Histogram(x=closes, xbins=dict(size=1)), row=1, col=1)

        # Add spectrogram to subplot
        fig_combined.add_trace(go.Heatmap(x=t, y=f, z=abs(Zxx), colorscale="Viridis"), row=2, col=1)

        # Add line chart with channels to subplot
        fig_combined.add_trace(go.Scatter(x=dates, y=closes, mode='lines', name='Close'), row=3, col=1)
        fig_combined.add_trace(go.Scatter(x=dates, y=upper_channel, mode='lines', name='Upper Channel'), row=3, col=1)
        fig_combined.add_trace(go.Scatter(x=dates, y=lower_channel, mode='lines', name='Lower Channel'), row=3, col=1)

        # Add scatter plot to subplot
        fig_combined.add_trace(go.Scatter(x=valid_x, y=valid_y, mode='markers'), row=4, col=1)

        # Update layout
        fig_combined.update_layout(
            title_text="Combined Stock Analysis Plots",
            height=2000,
            showlegend=False
        )

        return fig_combined

def main(rh_username: str, rh_password: str, ticker: str, cache_mode: str):
    """
    Main function to retrieve and cache technical data for a given ticker.
    Args:
        rh_username (str): Robinhood username for login.
        rh_password (str): Robinhood password for login.
        ticker (str): Stock ticker symbol.
        cache_mode (str): Cache mode, can be 'local', 'refresh', or other modes.
    Returns:
        dict: A dictionary containing the technica data plotly figure and DataFrame.
    """

    # Check if the technical history store directory exists, if not, create it
    if not os.path.exists(TECHNICAL_HISTORY_STORE):
        os.makedirs(TECHNICAL_HISTORY_STORE)

    # Define the file path for caching
    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(TECHNICAL_HISTORY_STORE, f"{ticker}_technical_data_{current_date_str}.pkl")
    
    if cache_mode == 'local':
        if os.path.exists(cache_file_path):
            print("Loading technical data from local cache...")
            with open(cache_file_path, 'rb') as f:
                option_bundle = pickle.load(f)
            return option_bundle
        else:
            print("No local cache found but local cache mode is on. Exiting...")
            print('Looked in directory ', cache_file_path)
            #exit(1)

    r.login(rh_username, rh_password)

    technicals = Technicals(ticker)
    technicals.get_historicals()
    technicals.get_fundamentals()
    dates, closes, closes_stationarized = technicals.preprocess_close(technicals.historicals)
    figs = technicals.generate_plots(dates, closes, closes_stationarized)

    technical_bundle = {
        'closes': closes,
        'closes_stationarized': closes_stationarized,
        'figs': figs
    }

    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600


    # Check cache mode
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing technical data...")
        # Save the DataFrame to the cache file
        with open(cache_file_path, 'wb') as f:
            pickle.dump(technical_bundle, f)
    else:
        print("Loading technical data from cache...")
        with open(cache_file_path, 'rb') as f:
            technical_bundle = pickle.load(f)

    return technical_bundle


if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Robinhood Portfolio Performance')
    parser.add_argument('--username', required=True, help='Robinhood account username')
    parser.add_argument('--password', required=True, help='Robinhood account password')
    parser.add_argument('--ticker', required=True, help='Robinhood account password')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the technical data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')

    args = parser.parse_args()
    
    rh_username = args.username
    rh_password = args.password
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

    main(rh_username, rh_password, ticker, cache_mode)

