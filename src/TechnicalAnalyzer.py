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
import utils.helpers_MarketExplorer as helpers_MarketExplorer

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

        print(f"Getting historicals for {self.ticker} from remote")
        time.sleep(self.cooldown_s)
        self.historicals = r.stocks.get_stock_historicals(self.ticker, interval=interval, span=span)
        print(f"Got historicals for {self.ticker} with {len(self.historicals)} records")

        
        self.historicals_df = pd.DataFrame(self.historicals)
        #print(self.historicals_df)
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
        
        valid_data = [(x, y) for x, y in zip(pct_past_3, pct_future_2) if x is not None and y is not None]
        valid_x, valid_y = zip(*valid_data) if valid_data else ([], [])

        self.figs = make_subplots(
            rows=6, cols=1,
            subplot_titles=(
                "Histogram of Stock Prices",
                "Spectrogram (STFT)",
                "Price with Channels",
                "Scatter of 3-Day vs. 2-Day % Changes",
                "Stationarized Series — Highlighted by Threshold",
                "Transition Probabilities (row-normalized)"
            )
        )

        self.figs.add_trace(go.Histogram(x=closes, xbins=dict(size=1)), row=1, col=1)

        # Add vertical line for the latest price on the histogram
        latest_price = closes.iloc[-1]
        self.figs.add_vline(x=latest_price, line_color='red', row=1, col=1)

        # Constrain spectrogram colorbar to row 2 domain
        yaxis2 = getattr(self.figs.layout, "yaxis2", None)
        ydom2 = list(getattr(yaxis2, "domain", [0.0, 1.0]))
        y_center_2 = (ydom2[0] + ydom2[1]) / 2.0
        y_len_2 = (ydom2[1] - ydom2[0]) * 0.95
        self.figs.add_trace(
            go.Heatmap(
                x=t, y=f, z=abs(Zxx), colorscale="Viridis",
                colorbar=dict(
                    len=y_len_2, lenmode="fraction", y=y_center_2, yanchor="middle",
                    thickness=12
                )
            ),
            row=2, col=1
        )

        self.figs.add_trace(go.Scatter(x=dates, y=closes, mode='lines', name='Close'), row=3, col=1)
        self.figs.add_trace(go.Scatter(x=dates, y=upper_channel, mode='lines', name='Upper Channel'), row=3, col=1)
        self.figs.add_trace(go.Scatter(x=dates, y=lower_channel, mode='lines', name='Lower Channel'), row=3, col=1)

        # Scatter: Past 3-day % vs Next 2-day % with |%| filters + regression fit
        p, fwd = 3, 2
        min_past_pct = 1.5   # set >0 to require |past %| >= threshold (e.g., 1.0)
        min_future_pct = 1.5 # set >0 to require |future %| >= threshold (e.g., 0.5)
        use_abs = True       # True => use absolute magnitude filters

        vx = np.asarray(valid_x, dtype=float)
        vy = np.asarray(valid_y, dtype=float)
        mask = np.isfinite(vx) & np.isfinite(vy)
        if use_abs:
            mask &= (np.abs(vx) >= min_past_pct) & (np.abs(vy) >= min_future_pct)
        else:
            mask &= (vx >= min_past_pct) & (vy >= min_future_pct)
        x_f, y_f = vx[mask], vy[mask]

        # Points
        self.figs.add_trace(
            go.Scatter(x=x_f, y=y_f, mode='markers',
                       marker=dict(opacity=0.7, size=6, color='lightblue'),
                       name='Obs'),
            row=4, col=1
        )

        # Fit line (if enough points)
        if x_f.size >= 2:
            slope, intercept = np.polyfit(x_f, y_f, 1)
            xr = np.linspace(float(np.nanmin(x_f)), float(np.nanmax(x_f)), 50)
            yr = slope * xr + intercept
            self.figs.add_trace(
                go.Scatter(x=xr, y=yr, mode='lines',
                           name=f'Fit y={slope:.2f}x+{intercept:.2f}',
                           line=dict(color='orange')),
                row=4, col=1
            )

        # Highlight last valid point (current-1) if it passes the filter
        i_current = len(closes) - fwd - 1
        if i_current >= p and i_current + fwd < len(closes):
            x_cur = pct_past_3[i_current]
            y_cur = pct_future_2[i_current]
            passes = np.isfinite(x_cur) and np.isfinite(y_cur)
            if passes:
                if use_abs:
                    passes &= (abs(x_cur) >= min_past_pct) and (abs(y_cur) >= min_future_pct)
                else:
                    passes &= (x_cur >= min_past_pct) and (y_cur >= min_future_pct)
            if passes:
                self.figs.add_trace(
                    go.Scatter(x=[x_cur], y=[y_cur], mode='markers',
                               marker=dict(color='red', size=10),
                               name=f'Current-1 ({p}+{fwd} days)'),
                    row=4, col=1
                )
                y0 = float(np.nanmin(y_f)) if y_f.size else float(np.nanmin(vy)) if vy.size else 0.0
                y1 = float(np.nanmax(y_f)) if y_f.size else float(np.nanmax(vy)) if vy.size else 0.0
                self.figs.add_shape(
                    type='line', x0=x_cur, x1=x_cur, y0=y0, y1=y1,
                    line=dict(color='red', width=2),
                    row=4, col=1
                )

        self.figs.update_layout(
            title_text="Combined Stock Analysis Plots",
            height=2800,
            showlegend=False
        )
        # --- New: add stationarized chart (row 5) and transition heatmap (row 6) into self.figs ---
        # Stationarized chart with threshold highlighting (row 5)
        fig_combined, _ = helpers_MarketExplorer.plot_stationary_split(
            closes, method="log_return", threshold_pct=1.5, use_abs=True, separate=False
        )
        for tr in fig_combined.data:
            self.figs.add_trace(tr, row=5, col=1)
        # zero reference line for row 5
        self.figs.add_shape(
            type="line",
            x0=closes.index.min(), x1=closes.index.max(), y0=0, y1=0,
            line=dict(color="#999", width=1, dash="dot"),
            row=5, col=1
        )

        # Transition heatmap from bucketized 1-day log returns (row 6)
        ret_1d = helpers_MarketExplorer.stationarize_closes(closes, method="log_return")  # % log returns
        edges, labels, centers = helpers_MarketExplorer.make_symmetric_bins([0.5, 1, 2.5, 5, 10, 20])
        codes, labels = helpers_MarketExplorer.bucketize(ret_1d, edges, labels)
        C = helpers_MarketExplorer.transition_counts(codes, n_bins=len(labels), lag=1)
        fig_heat = helpers_MarketExplorer.plot_transition_heatmap(labels, C, normalize="row")
        # Constrain transition heatmap colorbar to row 6 domain (set remove_colorbar=True to hide)
        remove_colorbar = False
        yaxis6 = getattr(self.figs.layout, "yaxis6", None)
        ydom6 = list(getattr(yaxis6, "domain", [0.0, 1.0]))
        y_center_6 = (ydom6[0] + ydom6[1]) / 2.0
        y_len_6 = (ydom6[1] - ydom6[0]) * 0.95
        for tr in fig_heat.data:
            if isinstance(tr, go.Heatmap):
                if remove_colorbar:
                    tr.update(showscale=False)
                else:
                    tr.update(colorbar=dict(
                        len=y_len_6, lenmode="fraction", y=y_center_6, yanchor="middle",
                        thickness=12
                    ))
            self.figs.add_trace(tr, row=6, col=1)
        # improve label readability on the heatmap
        self.figs.update_xaxes(tickangle=45, row=6, col=1)
        # --- end new ---

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
    if cache_mode in ('local', 'normal') and os.path.exists(cache_file_path) and not is_cache_stale(cache_file_path):
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

