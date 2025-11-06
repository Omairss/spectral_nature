import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import TechnicalAnalyzer
import os, time, pickle, json, hashlib
from pathlib import Path

# Matches CurrentStatus.main data store layout (no env vars)
_DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature3/code/Users/omai.r/spectral_nature/data/'

def _default_user_cache_dir(rh_username: str) -> Path:
    return Path(_DATA_STORE_CORE) / 'user_specific' / rh_username / 'portfolio_history' / 'momentum_roc'

def _hash_config(windows: dict, pairs: list, eps: float) -> str:
    # Stable hash for (windows, pairs, eps)
    payload = json.dumps({
        'windows': dict(sorted(windows.items())),
        'pairs': pairs,
        'eps': eps
    }, sort_keys=True)
    return hashlib.md5(payload.encode('utf-8')).hexdigest()

class MomentumRoCFitter:
    DEFAULT_WINDOWS = {'1d': 2, '1w': 5, '1m': 21, '3m': 63, '1yr': 252}
    DEFAULT_PAIRS = [('1d','1w'), ('1w','1m'), ('1m','3m')]

    def __init__(self, holdings_df, ta, us, ps, equity_col_candidates=None,
                 rh_username: str = None,
                 cache_dir: str = None,
                 cache_ttl_seconds: int = 3*3600,
                 cache_enabled: bool = True):
        self.ta = ta
        self.us, self.ps = us, ps
        self.holdings = self._prep_holdings(holdings_df, equity_col_candidates)
        self.series = {}   # ticker -> close series
        self.roc_ts = {}   # ticker -> DataFrame with roc_* columns

        # caching aligned with CurrentStatus template
        self.cache_enabled = bool(cache_enabled)
        self.cache_ttl_seconds = int(cache_ttl_seconds)
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        elif rh_username:
            self.cache_dir = _default_user_cache_dir(rh_username)
        else:
            # If no username and no explicit dir, disable cache to avoid writing to unknown location
            self.cache_enabled = False
            self.cache_dir = None

        if self.cache_enabled and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _prep_holdings(holdings_df, equity_col_candidates):
        df = holdings_df.copy()
        # Use index as Ticker if not present
        if 'Ticker' not in df.columns:
            df['Ticker'] = [str(x).upper() for x in (df.index if df.index is not None else [])]
        # Try to find an equity column
        eq_cands = (equity_col_candidates or
                    ['equity','Equity','current_value','market_value','Market Value','value','Value'])
        equity_col = next((c for c in eq_cands if c in df.columns), None)
        if equity_col is None:
            df['Equity'] = np.nan
        else:
            df = df.rename(columns={equity_col: 'Equity'})
        # Ensure only needed columns
        keep = ['Ticker'] + ([ 'Equity' ] if 'Equity' in df.columns else [])
        return df[keep].dropna(subset=['Ticker'])

    # ---------- Cache helpers ----------
    def _ticker_close_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}__close.pkl"

    def _ticker_roc_path(self, ticker: str, cfg_hash: str) -> Path:
        return self.cache_dir / f"{ticker.upper()}__roc__{cfg_hash}.pkl"

    def _is_stale(self, path: Path) -> bool:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return True
        return (time.time() - mtime) > self.cache_ttl_seconds

    def _load_pickle(self, path: Path):
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None

    def _save_pickle(self, path: Path, obj) -> None:
        try:
            with open(path, "wb") as f:
                pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as e:
            print(f"[cache] save failed: {path}: {e}")

    def clear_cache(self, tickers=None) -> int:
        if not self.cache_enabled or not self.cache_dir or not self.cache_dir.exists():
            return 0
        removed = 0
        try:
            if tickers:
                for t in tickers:
                    for p in [self._ticker_close_path(t), *self.cache_dir.glob(f"{t.upper()}__roc__*.pkl")]:
                        if p.exists():
                            p.unlink(missing_ok=True)
                            removed += 1
            else:
                for p in self.cache_dir.glob("*.pkl"):
                    p.unlink(missing_ok=True)
                    removed += 1
        except Exception:
            pass
        return removed

    # ---------- Data fetch + caching ----------
    def fetch(self, windows=None, force_refresh=False):
        need = max((windows or self.DEFAULT_WINDOWS).values())
        tickers = sorted({str(t).upper() for t in self.holdings['Ticker'].dropna().astype(str)})
        for t in tickers:
            try:
                s = None
                if self.cache_enabled and not force_refresh:
                    p = self._ticker_close_path(t)
                    if p.exists() and not self._is_stale(p):
                        obj = self._load_pickle(p)
                        if isinstance(obj, pd.Series) and isinstance(obj.index, pd.DatetimeIndex):
                            s = obj

                if s is None:
                    s = self._fetch_close_from_library(t)
                    if self.cache_enabled and s is not None and s.notna().any():
                        self._save_pickle(self._ticker_close_path(t), s)

                if s is not None and s.notna().any():
                    self.series[t] = s.dropna().iloc[-(need*3 if len(s) >= need*3 else len(s)):]
                else:
                    self.series[t] = None
            except Exception as e:
                print(f"[warn] {t}: {e}")
                self.series[t] = None
        return self

    def _fetch_close_from_library(self, ticker: str) -> pd.Series:
        bundle = self.ta.main(self.us, self.ps, ticker, 'normal', 'normal')
        def walk(obj):
            if isinstance(obj, (pd.DataFrame, pd.Series)):
                yield obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    yield from walk(v)
            elif isinstance(obj, (list, tuple)):
                for v in obj:
                    yield from walk(v)

        close_candidates = {
            'close','adj close','adj_close','adjusted_close','adjclose',
            'c','close_price','closeprice','price','last'
        }
        date_candidates = ['begins_at','time','timestamp','date','datetime']

        for obj in walk(bundle):
            df = obj.to_frame(obj.name or 'value') if isinstance(obj, pd.Series) else obj
            if not isinstance(df, pd.DataFrame):
                continue
            df = df.copy()

            for sym_col in ['symbol','Symbol','ticker','Ticker']:
                if sym_col in df.columns:
                    df = df[df[sym_col].astype(str).str.upper() == ticker.upper()]
                    break
            if df.empty:
                continue

            if not isinstance(df.index, pd.DatetimeIndex):
                for dc in date_candidates:
                    if dc in df.columns:
                        idx = pd.to_datetime(df[dc], utc=True, errors='coerce').tz_convert(None)
                        df = df.set_index(idx)
                        break
            if not isinstance(df.index, pd.DatetimeIndex):
                continue

            cols_lower = {str(c).strip().lower(): c for c in df.columns}
            match = next((cols_lower[k] for k in close_candidates if k in cols_lower), None)
            if match is None:
                continue

            s = pd.to_numeric(df[match], errors='coerce').dropna()
            if s.empty:
                continue
            return s.sort_index().asfreq('B').ffill()

        raise ValueError(f"Could not find a close series for {ticker} in technical bundle.")

    def compute_timeseries(self, windows=None, pairs=None, eps=1e-8, force_refresh=False):
        windows = windows or self.DEFAULT_WINDOWS
        pairs = pairs or self.DEFAULT_PAIRS
        self.roc_ts = {}

        cfg_hash = _hash_config(windows, pairs, float(eps))

        def slope_window(arr):
            y = np.log(np.asarray(arr, dtype=float))
            x = np.arange(len(y), dtype=float)
            m, _ = np.polyfit(x, y, 1)
            return float(m)

        for t, s in self.series.items():
            if s is None or s.dropna().empty:
                self.roc_ts[t] = None
                continue

            # Try cache
            if self.cache_enabled and not force_refresh:
                p = self._ticker_roc_path(t, cfg_hash)
                if p.exists() and not self._is_stale(p):
                    obj = self._load_pickle(p)
                    if isinstance(obj, pd.DataFrame):
                        self.roc_ts[t] = obj
                        continue

            s = s.dropna()
            slopes_ts = {k: s.rolling(n, min_periods=n).apply(slope_window, raw=True)
                         for k, n in windows.items()}
            df = np.nan * s.to_frame("dummy").drop(columns=["dummy"])
            for a, b in pairs:
                sa, sb = slopes_ts.get(a), slopes_ts.get(b)
                if sa is None or sb is None:
                    continue
                roc = sb / sa - 1.0
                bad = (~np.isfinite(roc)) | (~np.isfinite(sa)) | (sa.abs() <= eps)
                roc[bad] = np.nan
                df[f'roc_{a}_to_{b}'] = roc

            self.roc_ts[t] = df

            # Save cache
            if self.cache_enabled:
                self._save_pickle(self._ticker_roc_path(t, cfg_hash), df)

        return self

def _build_palette():
    names = ["Plotly", "D3", "G10", "T10", "Alphabet", "Dark24", "Light24", "Set1", "Pastel1", "Pastel2", "Safe", "Vivid"]
    colors = []
    for n in names:
        colors += getattr(px.colors.qualitative, n, [])
    return colors or ['#1f77b4','#ff7f0e','#2ca02c','#d62728','#9467bd',
                      '#8c564b','#e377c2','#7f7f7f','#bcbd22','#17becf']

def plot_holdings_roc_momentum(us, ps, holdings_df,
                               rh_username: str = None,
                               windows=None,
                               pairs=None,
                               smooth=5,
                               cache_dir: str = None,
                               cache_ttl_seconds: int = 3*3600,
                               cache_enabled: bool = True,
                               force_refresh: bool = False) -> go.Figure:
    """
    Builds the all-holdings RoC momentum chart.
    Caching directory defaults to DATA_STORE_CORE/user_specific/<rh_username>/portfolio_history/momentum_roc.
    """
    windows = windows or {'1d': 2, '1w': 5, '1m': 21, '3m': 63, '1yr': 252}
    pairs = pairs or [('1d','1w'), ('1w','1m'), ('1m','3m')]

    mr = MomentumRoCFitter(
        holdings_df, TechnicalAnalyzer, us, ps,
        rh_username=rh_username,
        cache_dir=cache_dir,
        cache_ttl_seconds=cache_ttl_seconds,
        cache_enabled=cache_enabled
    )
    mr.fetch(windows=windows, force_refresh=force_refresh)\
      .compute_timeseries(windows=windows, pairs=pairs, force_refresh=force_refresh)

    palette = _build_palette()
    tickers = [t for t, df in mr.roc_ts.items() if df is not None]
    color_map = {t: palette[i % len(palette)] for i, t in enumerate(sorted(tickers))}
    dash_map = {('1d','1w'): 'solid', ('1w','1m'): 'dash', ('1m','3m'): 'dot'}

    fig = go.Figure()
    for t in sorted(tickers):
        df = mr.roc_ts[t]
        if df is None or df.dropna(how='all').empty:
            continue
        for a, b in pairs:
            col = f'roc_{a}_to_{b}'
            if col not in df.columns:
                continue
            y = df[col]
            if isinstance(smooth, int) and smooth > 1:
                y = y.rolling(smooth, min_periods=1).mean()
            if not np.isfinite(y).any():
                continue
            fig.add_scatter(
                x=y.index, y=y,
                mode='lines',
                name=f'{t} {a}→{b}',
                line=dict(color=color_map[t], dash=dash_map.get((a,b), 'solid')),
                hovertemplate=f'%{{x|%Y-%m-%d}}<br>{t} {a}→{b}: %{{y:.4f}}<extra></extra>',
                legendgroup=t
            )

    fig.update_layout(
        title='RoC Momentum over time (all holdings)',
        xaxis_title='Date',
        yaxis_title='RoC of slope',
        legend_title='Ticker · Pair',
        hovermode='x unified'
    )
    return fig


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
sys.path.append("../analysis_modules")
import markets

from utils.helpers import to_daily
from utils.rh_fixes import get_historical_portfolio
from utils.analyze_portfolio import analyze_portfolio_performance, get_ticker_history

# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

def get_market_data():

    return {
        #"top_movers_sp500_up": markets.get_top_movers_sp500('up'),
        #"top_movers_sp500_down": markets.get_top_movers_sp500('down'),
        #"top_100": markets.get_top_100(),
        "top_movers": markets.get_top_movers(),
        #"upcoming_earnings": markets.get_all_stocks_from_market_tag('upcoming-earnings')
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


def get_data(mode, login_result, rh_username=None):
    if mode == 'market':
        return {"market_data": get_market_data()}

    elif mode == 'holdings':
        holdings_df = r.account.build_holdings(with_dividends=True)
        return {"holdings_df": holdings_df}

    elif mode == 'historical':
        historical_df = get_historical_portfolio(r, login_result=login_result, span='all')
        return {"historical_df": historical_df}
    
    elif mode == 'historical_w_benchmarks':

        print('MODE: historical_w_benchmarks')

        # Portfolio -> 'timestamp','portfolio'
        port_raw = get_historical_portfolio(r, login_result=login_result, span='all')
        port_daily = to_daily(port_raw).rename(columns={'value':'portfolio'})

        # Benchmarks -> merge as columns by symbol
        symbols = ['SPY', 'DIA', 'QQQ', 'VOO', 'BRK.B', 'ARKK']
        combined = port_daily
        for sym in symbols:
            b_raw = get_ticker_history(r, sym, interval='day', span='5year')
            b_daily = to_daily(b_raw).rename(columns={'value': sym})
            if not b_daily.empty:
                combined = combined.merge(b_daily, on='timestamp', how='outer')

        combined = combined.sort_values('timestamp').reset_index(drop=True)
        return {"historical_df": combined}

    elif mode == 'roc_momentum':
        # Build holdings and RoC figure using user-scoped cache dir under DATA_STORE_CORE
        if rh_username is None:
            raise ValueError("rh_username is required for roc_momentum caching")
        holdings_df = r.account.build_holdings(with_dividends=True)
        fig = plot_holdings_roc_momentum(
            rh_username,  # us
            login_result.get('password') if isinstance(login_result, dict) else None,  # ps (fallback if present)
            holdings_df,
            rh_username=rh_username,
            cache_dir=str(_default_user_cache_dir(rh_username)),
            cache_ttl_seconds=3*3600,
            cache_enabled=True,
            force_refresh=False
        )
        return {"holdings_df": holdings_df, "roc_momentum_fig": fig}

    elif mode == 'profile':
        holdings_df = r.account.build_holdings(with_dividends=True)
        user_profile = r.account.build_user_profile()
        #historical_df = r.account.get_historical_portfolio(interval='week', span='5year')
        historical_df = get_historical_portfolio(r, login_result=login_result, span='all')
        positions_df = get_open_stock_positions()
        return {
            "holdings_df": holdings_df,
            "user_profile": user_profile,
            "historical_df": historical_df,
            "positions_df": positions_df
        }

    elif mode == 'performance':
        
        indices = ['SPY', 'DIA', 'QQQ', 'VOO', 'IWM', 'BRK.B', 'VUG', 'VGT', 'ARKK']

        results = {}

        for index in indices:
            results[index] = analyze_portfolio_performance(r, login_result,
                entity=index,
                start_year=2020,
                end_year=2025,     # future-dated if you want the script to keep going
                interval='day',
                span='5year',
                risk_free_rate=0.02
            )

        results['portfolio']  = analyze_portfolio_performance(r, login_result,
            entity='portfolio',
            start_year=2020,
            end_year=2025,     # future-dated if you want the script to keep going
            interval='day',
            span='5year',
            risk_free_rate=0.02
        )


        # Flatten the nested results dictionary
        flattened_results = []
        for entity, yearly_data in results.items():
            for year, metrics in yearly_data.items():
                metrics['Year'] = year
                metrics['Entity'] = entity
                flattened_results.append(metrics)

        # Convert the flattened list of dictionaries into a DataFrame
        results_df = pd.DataFrame(flattened_results)

        return {
            "performance_df": results_df
        }


    elif mode == 'all':

        holdings_df = r.account.build_holdings(with_dividends=True)
        user_profile = r.account.build_user_profile()
        #historical_df = r.account.get_historical_portfolio(interval='week', span='5year')
        historical_df = get_historical_portfolio(r, login_result=login_result, span='all')
        positions_df = get_open_stock_positions()
        market_data = get_market_data()
        # Optional: include a prebuilt RoC figure for dashboards
        if rh_username:
            roc_fig = plot_holdings_roc_momentum(
                rh_username, login_result.get('password') if isinstance(login_result, dict) else None,
                holdings_df, rh_username=rh_username,
                cache_dir=str(_default_user_cache_dir(rh_username)),
                cache_enabled=True, cache_ttl_seconds=3*3600
            )
        else:
            roc_fig = None
        return {
            "holdings_df": holdings_df,
            "user_profile": user_profile,
            "historical_df": historical_df,
            "positions_df": positions_df,
            "market_data": market_data,
            "roc_momentum_fig": roc_fig
        }

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
    Main entry that aligns cache paths to DATA_STORE_CORE (no env vars).
    """
    PORTFOLIO_HISTORY_STORE = Path(_DATA_STORE_CORE) / 'user_specific' / rh_username / 'portfolio_history'
    PORTFOLIO_HISTORY_STORE.mkdir(parents=True, exist_ok=True)

    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_key_file_path = PORTFOLIO_HISTORY_STORE / f"{rh_username}_portfolio_data_{mode}_{current_date_str}.pkl"

    if cache_mode == 'local':
        if cache_key_file_path.exists():
            with open(cache_key_file_path, 'rb') as f:
                return pickle.load(f)
        else:
            print(f"No local cache found at {cache_key_file_path}")
            # fall through to fetch

    login_result = r.login(rh_username, rh_password)

    # Helper: 3h TTL
    def is_cache_stale(p: Path) -> bool:
        if not p.exists():
            return True
        file_mod_time = datetime.datetime.fromtimestamp(p.stat().st_mtime)
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600

    if cache_mode == 'refresh' or is_cache_stale(cache_key_file_path):
        status_data = get_data(mode, login_result, rh_username=rh_username)
        with open(cache_key_file_path, 'wb') as f:
            pickle.dump(status_data, f)
    else:
        with open(cache_key_file_path, 'rb') as f:
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

