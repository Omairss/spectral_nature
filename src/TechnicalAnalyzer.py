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
        self.cooldown_s = 3  # Set the cooldown period in seconds


    def get_fundamentals(self):
        print(f"Getting fundamentals for {self.ticker}")
        time.sleep(self.cooldown_s)
        self.fundamentals = r.stocks.get_fundamentals(self.ticker)
        return self.fundamentals
    
    def get_historicals_hybrid(self, intervals=['day', 'hour'], spans=['5year', '3month']) -> pd.DataFrame:

        historicals = []
        for interval, span in zip(intervals, spans):
            historicals.append(r.stocks.get_stock_historicals(self.ticker, interval=interval, span=span))
        
        df_list = [pd.DataFrame(h) for h in historicals]
        self.historicals_df = pd.concat(df_list, ignore_index=True).drop_duplicates(subset='begins_at')

        self.historicals_df['close_price'] = self.historicals_df['close_price'].astype(float)
        self.historicals_df['stationary_close'] = np.nan
        self.historicals_df = self.historicals_df.set_index('begins_at')
        self.historicals_df = self.historicals_df.sort_index()
        return 

    
    def get_historicals(self, interval='day', span='5year'):

        print(f"Getting historicals for {self.ticker}")
        time.sleep(self.cooldown_s)
        self.historicals = r.stocks.get_stock_historicals(self.ticker, interval=interval, span=span)
        print(f"Got historicals for {self.ticker} with {len(self.historicals)} records")

        self.historicals_df = pd.DataFrame(self.historicals)
        self.historicals_df = self.historicals_df.set_index('begins_at')
        self.historicals_df = self.historicals_df.sort_index()

        self.historicals_df.index = pd.to_datetime(self.historicals_df.index)

        self.historicals_df['close_price'] = pd.to_numeric(self.historicals_df['close_price'], errors='coerce')

        self.historicals_df['stationary_close'] = np.log(self.historicals_df['close_price']) - np.log(self.historicals_df['close_price'].shift(1))

        self.historicals_df = self.historicals_df.fillna(method='ffill')

        return

    def generate_plots(self):

        dates = self.historicals_df.index
        closes = self.historicals_df['close_price']
        closes_stationarized = self.historicals_df['stationary_close']

        nperseg = min(256, len(closes_stationarized))
        f, t, Zxx = stft(closes_stationarized, fs=1)

        closes_ser = pd.Series(closes)
        roll_window = 20
        upper_channel = closes_ser.rolling(roll_window).max()
        lower_channel = closes_ser.rolling(roll_window).min()

        pct_past_3 = []
        pct_future_2 = []
        for i in range(len(closes)):
            x_val = ((closes[i] - closes[i-3]) / closes[i-3] * 100) if i >= 3 else None
            y_val = ((closes[i+2] - closes[i]) / closes[i] * 100) if i+2 < len(closes) else None
            pct_past_3.append(x_val)
            pct_future_2.append(y_val)
        valid_x = [x for x in pct_past_3 if x is not None]
        valid_y = [y for y in pct_future_2 if y is not None]

        self.figs = make_subplots(
            rows=4, cols=1,
            subplot_titles=("Histogram of Stock Prices", "Spectrogram (STFT)", "Price with Channels", "Scatter of 3-Day vs. 2-Day % Changes")
        )

        self.figs.add_trace(go.Histogram(x=closes, xbins=dict(size=1)), row=1, col=1)

        # Add vertical line for the latest price on the histogram
        latest_price = closes.iloc[-1]
        self.figs.add_vline(x=latest_price, line_color='red', row=1, col=1)

        self.figs.add_trace(go.Heatmap(x=t, y=f, z=abs(Zxx), colorscale="Viridis"), row=2, col=1)

        self.figs.add_trace(go.Scatter(x=dates, y=closes, mode='lines', name='Close'), row=3, col=1)
        self.figs.add_trace(go.Scatter(x=dates, y=upper_channel, mode='lines', name='Upper Channel'), row=3, col=1)
        self.figs.add_trace(go.Scatter(x=dates, y=lower_channel, mode='lines', name='Lower Channel'), row=3, col=1)

        ## Add the scatter plot for the 3-day vs. 2-day % changes
        self.figs.add_trace(go.Scatter(x=valid_x, y=valid_y, mode='markers'), row=4, col=1)

        # Add the current point to the scatter plot
        current_i = len(closes) - 2
        print(f"current_i: {current_i}")
        print(f"pct_past_3 length: {len(pct_past_3)}")
        print(f"pct_future_2 length: {len(pct_future_2)}")

        # Plot the last valid point (current-1, 5-day period)
        i_current = len(closes) - 3  # last index for which future 2 days exist
        x_current = pct_past_3[i_current]
        y_current = pct_future_2[i_current]
        self.figs.add_trace(go.Scatter(
            x=[x_current],
            y=[y_current],
            mode='markers',
            marker=dict(color='red', size=10),
            name='Current-1 (5-day period)'
        ), row=4, col=1)

        # Add vertical red line for the current past 3 days %
        self.figs.add_shape(
            type='line',
            x0=x_current,
            x1=x_current,
            y0=min(valid_y),
            y1=max(valid_y),
            line=dict(color='red', width=2),
            row=4, col=1
        )

        self.figs.update_layout(
            title_text="Combined Stock Analysis Plots",
            height=2000,
            showlegend=False
        )

        return

def main(rh_username: str, rh_password: str, ticker: str, cache_mode: str, mode: str = 'normal'):
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
    
    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600

    # Check cache mode and load from cache if applicable
    if cache_mode == 'local' and os.path.exists(cache_file_path) and not is_cache_stale(cache_file_path):
        print("Loading technical data from local cache...")
        with open(cache_file_path, 'rb') as f:
            technical_bundle = pickle.load(f)
        return technical_bundle

    # Fetch new data
    r.login(rh_username, rh_password)
    technicals = Technicals(ticker)
    
    if mode == 'normal':
        technicals.get_historicals()
    elif mode == 'hybrid':
        technicals.get_historicals_hybrid()
    #technicals.get_fundamentals()
    technicals.generate_plots()

    technical_bundle = {
        'dataframe': technicals.historicals_df,
        'figs': technicals.figs
    }

    # Save the new data to the cache file if needed
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing technical data...")
        with open(cache_file_path, 'wb') as f:
            pickle.dump(technical_bundle, f)

    return technical_bundle


if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Robinhood Portfolio Performance')
    parser.add_argument('--username', required=True, help='Robinhood account username')
    parser.add_argument('--password', required=True, help='Robinhood account password')
    parser.add_argument('--ticker', required=True, help='Robinhood account password')
    parser.add_argument('--mode', default='normal', help='Mode for fetching data. Hybrid mode fetches data at multiple intervals and spans.')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the technical data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')

    args = parser.parse_args()
    
    rh_username = args.username
    rh_password = args.password
    ticker = args.ticker
    force_refresh = args.force_refresh
    force_local = args.force_local
    mode = args.mode

    if force_refresh:
        cache_mode = "refresh"
    elif not force_refresh:
        cache_mode = "normal"
        if force_local:
            cache_mode = "local"
    
    if force_refresh and force_local:
        print("Error: Cannot force refresh and use local cache at the same time.")
        exit(1)

    main(rh_username, rh_password, ticker, cache_mode, mode)

