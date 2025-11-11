##############################################
################## Imports ###################
##############################################

import time
import os
import math
import json 
import requests
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import scipy.optimize as opt
import pickle as pkl
import re
import copy


from tqdm import tqdm
from scipy.optimize import brentq
from scipy.stats import norm
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path



##############################################
################# Variables ##################
##############################################

repo_path = '/Users/zohairshafi/Local Workspace/spectral_nature/'
notebook_path = os.path.join(repo_path, 'notebooks')
cache_path = os.path.join(repo_path, 'cache')
CACHE_ROOT = Path(os.path.join(repo_path, 'cache/options'))
options_cache_memory = {}  # dict[contract_symbol] -> DataFrame


##############################################
############### Math Functions ###############
##############################################

def power_law_log(log_x, alpha, logC):

    # Define power law in log-log space
    return (-alpha * log_x) + logC

def forward_ratio(dte_1, dte_2, iv_1, iv_2):
    
    try:
        T1 = dte_1 / 365.0
        T2 = dte_2 / 365.0
        s_1 = iv_1 
        s_2 = iv_2 

        tv_1 = (s_1 ** 2) * T1
        tv_2 = (s_2 ** 2) * T2

        denom = T2 - T1
        if denom <= 0:
            return 0
        fwd_var = (tv_2 - tv_1) / denom
        if fwd_var <= 0:
            return 0
        fwd_sigma = math.sqrt(fwd_var) 
        ff_ratio = (s_1 - fwd_sigma) / fwd_sigma
    except:
        ff_ratio = 0
    
    return ff_ratio * 100

def get_risk_free_rate_estimate(days_to_expiration):
            """Simple estimate based on current yield curve"""
            if days_to_expiration <= 30:
                return 0.0393  # 3-month rate
            elif days_to_expiration <= 365:
                return 0.0370  # 1-year rate
            else:
                return 0.0408  # 10-year rate

def calculate_implied_volatility(option_price: float, S: float, K: float, T: float, r: float, option_type: str):
    """
    Calculate implied volatility using the Black-Scholes model.

    Args:
        option_price: Market price of the option
        S: Current stock price (underlying asset price)
        K: Strike price of the option
        T: Time to expiration in years
        r: Risk-free interest rate
        option_type: Type of option (ContractType.CALL or ContractType.PUT)

    Returns:
        Implied volatility as a float, or None if calculation fails
    """
    # Define a reasonable range for sigma
    sigma_lower = 1e-6
    sigma_upper = 5.0  # Adjust upper limit if necessary

    # Check if the option is out-of-the-money and price is close to zero
    intrinsic_value = max(0, (S - K) if option_type == "call" else (K - S))
    if option_price <= intrinsic_value + 1e-6:
        # print("Option price is close to intrinsic value; implied volatility is near zero.") # Uncomment for checking the status
        return 0.0

    # Define the function to find the root
    def option_price_diff(sigma: float) -> float:
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == "call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        elif option_type == "put":
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        return price - option_price

    try:
        return brentq(option_price_diff, sigma_lower, sigma_upper)
    except ValueError as e:
#         print(f"Failed to find implied volatility: {e}")
        return None
    
def calculate_delta_historical(option_price: float, strike_price: float, expiry: pd.Timestamp, underlying_price: float, risk_free_rate: float, option_type: str, timestamp: pd.Timestamp):
    """
    Calculate option delta using historical data and the Black-Scholes model.

    Args:
        option_price: Market price of the option
        strike_price: Strike price of the option
        expiry: Option expiration datetime
        underlying_price: Current price of underlying asset
        risk_free_rate: Risk-free interest rate
        option_type: Type of option ('call' or 'put')
        timestamp: Current timestamp for calculation

    Returns:
        Option delta as a float, or None if calculation fails
    """
    # Calculate the time to expiry in years
    T = (expiry - timestamp).total_seconds() / (365 * 24 * 60 * 60)
    # Set minimum T to avoid zero
    T = max(T, 1e-6)

    if T == 1e-6:
#         print("Option has expired or is expiring now; setting delta based on intrinsic value.")
        if option_type == "put":
            return -1.0 if underlying_price < strike_price else 0.0
        else:
            return 1.0 if underlying_price > strike_price else 0.0

    implied_volatility = calculate_implied_volatility(
        option_price, underlying_price, strike_price, T, risk_free_rate, option_type
    )
    if implied_volatility is None or implied_volatility <= 1e-6:
        # print("Implied volatility could not be determined, skipping delta calculation.")
        return None

    d1 = (
        np.log(underlying_price / strike_price)
        + (risk_free_rate + 0.5 * implied_volatility**2) * T
    ) / (implied_volatility * np.sqrt(T))
    delta = norm.cdf(d1) if option_type == "call" else -norm.cdf(-d1)
    return delta

def calculate_gamma_historical(option_price: float, strike_price: float, expiry: pd.Timestamp, underlying_price: float, risk_free_rate: float, option_type: str, timestamp: pd.Timestamp):
    """
    Calculate option gamma using historical inputs and Black–Scholes.

    Args:
        option_price: Market price of the option (used to infer IV)
        strike_price: Strike price of the option
        expiry: Option expiration datetime
        underlying_price: Current price of underlying asset
        risk_free_rate: Annualized risk-free rate (as decimal, e.g., 0.04)
        option_type: 'call' or 'put' (not used directly in gamma, but kept for parity)
        timestamp: Current timestamp for calculation

    Returns:
        Gamma as a float (per $1 change in underlying), or None if calculation fails
    """
    # Time to expiry in years; clamp to avoid division by zero
    T = (expiry - timestamp).total_seconds() / (365 * 24 * 60 * 60)
    T = max(T, 1e-6)

    if T == 1e-6:
        return 0.0

    implied_volatility = calculate_implied_volatility(
        option_price, underlying_price, strike_price, T, risk_free_rate, option_type
    )
    if implied_volatility is None or implied_volatility <= 1e-6:
        return None

    try:
        d1 = (
            np.log(underlying_price / strike_price)
            + (risk_free_rate + 0.5 * implied_volatility**2) * T
        ) / (implied_volatility * np.sqrt(T))
        gamma = norm.pdf(d1) / (underlying_price * implied_volatility * np.sqrt(T))
        return gamma
    except Exception:
        return None

def calculate_vega_historical(option_price: float, strike_price: float, expiry: pd.Timestamp, underlying_price: float, risk_free_rate: float, option_type: str, timestamp: pd.Timestamp):
    """
    Calculate option vega using historical inputs and Black–Scholes.

    Args:
        option_price: Market price of the option (used to infer IV)
        strike_price: Strike price of the option
        expiry: Option expiration datetime
        underlying_price: Current price of underlying asset
        risk_free_rate: Annualized risk-free rate (as decimal, e.g., 0.04)
        option_type: 'call' or 'put'
        timestamp: Current timestamp for calculation

    Returns:
        Vega as a float (price change for a 1.0 change in volatility), or None if calculation fails.
        Note: To get vega per 1% vol change, multiply this value by 0.01.
    """
    # Time to expiry in years; clamp to avoid division by zero
    T = (expiry - timestamp).total_seconds() / (365 * 24 * 60 * 60)
    T = max(T, 1e-6)

    if T == 1e-6:
        return 0.0

    implied_volatility = calculate_implied_volatility(
        option_price, underlying_price, strike_price, T, risk_free_rate, option_type
    )
    if implied_volatility is None or implied_volatility <= 1e-6:
        return None

    try:
        d1 = (
            np.log(underlying_price / strike_price)
            + (risk_free_rate + 0.5 * implied_volatility**2) * T
        ) / (implied_volatility * np.sqrt(T))
        vega = underlying_price * norm.pdf(d1) * np.sqrt(T)
        return vega
    except Exception:
        return None

def calculate_theta_historical(option_price: float, strike_price: float, expiry: pd.Timestamp, underlying_price: float, risk_free_rate: float, option_type: str, timestamp: pd.Timestamp):
    """
    Calculate option theta using historical inputs and Black–Scholes.

    Args:
        option_price: Market price of the option (used to infer IV)
        strike_price: Strike price of the option
        expiry: Option expiration datetime
        underlying_price: Current price of underlying asset
        risk_free_rate: Annualized risk-free rate (as decimal, e.g., 0.04)
        option_type: 'call' or 'put'
        timestamp: Current timestamp for calculation

    Returns:
        Theta as a float (annualized rate of price decay). For daily theta, divide by 365.
        Returns None if calculation fails.
    """
    # Time to expiry in years; clamp to avoid division by zero
    T = (expiry - timestamp).total_seconds() / (365 * 24 * 60 * 60)
    T = max(T, 1e-6)

    if T == 1e-6:
        # Approaching expiry, theta can be unstable; approximate as 0
        return 0.0

    implied_volatility = calculate_implied_volatility(
        option_price, underlying_price, strike_price, T, risk_free_rate, option_type
    )
    if implied_volatility is None or implied_volatility <= 1e-6:
        return None

    try:
        sqrtT = np.sqrt(T)
        d1 = (
            np.log(underlying_price / strike_price)
            + (risk_free_rate + 0.5 * implied_volatility**2) * T
        ) / (implied_volatility * sqrtT)
        d2 = d1 - implied_volatility * sqrtT

        first_term = -(underlying_price * norm.pdf(d1) * implied_volatility) / (2 * sqrtT)
        if option_type == "call":
            second_term = -risk_free_rate * strike_price * np.exp(-risk_free_rate * T) * norm.cdf(d2)
            theta = first_term + second_term
        else:  # put
            second_term = risk_free_rate * strike_price * np.exp(-risk_free_rate * T) * norm.cdf(-d2)
            theta = first_term + second_term

        return theta
    except Exception:
        return None

##############################################
############### API Functions ################
##############################################

def get_current_value(symbol, headers):
    
    url = f"https://data.alpaca.markets/v2/stocks/bars/latest?symbols={symbol}&feed=iex"
    data = json.loads(requests.get(url, headers = headers).text)
    current_price = data['bars'][symbol]['c']
    
    return float(current_price), data['bars'][symbol]['t']

def get_options_information(symbol, start_day_offset, end_day_offset, option_type, percentage_to_search, headers):
    
    current_price, current_time = get_current_value(symbol, headers)
    print (f"Symbol | {symbol}\nCurrent Price: {current_price} (Time : {current_time})")

    
    # Days To Expiry
    now = datetime.now(tz = ZoneInfo("America/New_York"))
    start_date = now + timedelta(days = start_day_offset)
    end_date = now + timedelta(days = end_day_offset)

    # Set Expiration Dates
    expiration_date_gte = start_date
    expiration_date_lte = end_date

    # Options Type and Strike Price 
    if option_type == 'put':
        strike_price_gte = current_price - (current_price * percentage_to_search)
        strike_price_lte = current_price

    elif option_type == 'call':
        strike_price_gte = current_price
        strike_price_lte = current_price + (current_price * percentage_to_search)

    # Run API Call
    limit = 1000
    url = f"https://api.alpaca.markets/v2/options/contracts?underlying_symbols={symbol}&limit={limit}&status=active&expiration_date_gte={expiration_date_gte.date()}&expiration_date_lte={expiration_date_lte.date()}&root_symbol={symbol}&type={option_type}&style=american&strike_price_gte={strike_price_gte}&strike_price_lte={strike_price_lte}"
    response = requests.get(url, headers = headers)
    data = json.loads(response.text)

    d = np.zeros((len(data['option_contracts']), 5), dtype = object)
    use_idx = []
    for idx, option in enumerate(data['option_contracts']):
        if option['close_price'] is not None:
            d[idx][0] = option['symbol']
            d[idx][1] = option['expiration_date']
            d[idx][2] = option['strike_price']
            d[idx][3] = option['close_price']
            d[idx][4] = option['type'] 
            use_idx.append(idx)

    data = pd.DataFrame(d[use_idx, :])
    data.columns = ['Symbol', 'Expiration Date', 'Strike Price', 'Close Price', 'Type']

    # A different API call is needed for Greeks and IV
    # Get additional data for each option 
    if len(data['Symbol']) <= 100:
        option_symbol = ",".join(data['Symbol'])
        url = f"https://data.alpaca.markets/v1beta1/options/snapshots?symbols={option_symbol}&feed=indicative&limit=100"
        response = requests.get(url, headers = headers)
        options_data = json.loads(response.text)
    else: 
        options_data = {'snapshots' : {}}
        for batch in np.array_split(data['Symbol'].values, 1 + (len(data['Symbol']) // 100)):
            option_symbol = ",".join(batch)
            url = f"https://data.alpaca.markets/v1beta1/options/snapshots?symbols={option_symbol}&feed=indicative&limit=100"
            response = requests.get(url, headers = headers)
            options_data['snapshots'] = options_data['snapshots'] | json.loads(response.text)['snapshots'] 

    # Clean it all up and put into dataframe 
    d = np.zeros((len(options_data['snapshots']), 13), dtype = object)
    for idx, option in enumerate(options_data['snapshots']):
        d[idx][0] = option

        other_data = data[data['Symbol'] == option].values[0]
        d[idx][1] = other_data[1]
        d[idx][2] = other_data[2]
        d[idx][3] = other_data[3]
        d[idx][4] = other_data[4]

        d[idx][5] = options_data['snapshots'][option]['greeks']['delta']
        d[idx][6] = options_data['snapshots'][option]['greeks']['gamma']
        d[idx][7] = options_data['snapshots'][option]['greeks']['rho']
        d[idx][8] = options_data['snapshots'][option]['greeks']['theta']
        d[idx][9] = options_data['snapshots'][option]['greeks']['vega']
        d[idx][10] = options_data['snapshots'][option]['impliedVolatility']
        d[idx][11] = options_data['snapshots'][option]['latestQuote']['ap']
        d[idx][12] = options_data['snapshots'][option]['latestQuote']['bp']
    option_details = pd.DataFrame(d)
    option_details.columns = ['Symbol', 'Expiration Date', 'Strike Price', 'Close Price', 'Type', 'Delta', 'Gamma', 'Rho', 'Theta', 'Vega', 'IV', 'Buy Price', 'Sell Price']
    option_details = option_details.sort_values(by = ['Strike Price', 'Expiration Date']).reset_index(drop = True)
    strike_group = {int(key): group for key, group in option_details.groupby('Strike Price')}

    return option_details, strike_group, current_price

def get_historical_options_data(option_symbols, current_date, stock_data, timeframe = '1D', headers = None):
    
    options_price_tracker = {}
    options_symbols = ",".join(option_symbols)
    start_date = str(current_date.date())
    end_date = current_date + timedelta(30)

    url = f"https://data.alpaca.markets/v1beta1/options/bars?symbols={options_symbols}&timeframe={timeframe}&start={start_date}&limit=1000&sort=asc"
    response = requests.get(url, headers=headers).text
    data = json.loads(response)

    # Accumulate all pages of results per symbol
    combined_bars = data.get('bars', {})
    next_token = data.get('next_page_token')
    while next_token is not None:
        url = f"https://data.alpaca.markets/v1beta1/options/bars?symbols={options_symbols}&timeframe={timeframe}&start={start_date}&limit=1000&sort=asc&page_token={next_token}"
        page = json.loads(requests.get(url, headers=headers).text)
        for sym, rows in page.get('bars', {}).items():
            if sym in combined_bars:
                combined_bars[sym].extend(rows)
            else:
                combined_bars[sym] = rows
        next_token = page.get('next_page_token')

    for op_symbol in combined_bars:
        df = []
        for idx in range(len(combined_bars[op_symbol])):

            mid_op_price = combined_bars[op_symbol][idx]['c']
            high_op_price = combined_bars[op_symbol][idx]['h']
            low_op_price = combined_bars[op_symbol][idx]['l']
            cur_date = pd.to_datetime(combined_bars[op_symbol][idx]['t'])
            # Find matching stock price for the same date; skip if missing
            try:
                cur_stock_price = stock_data[(stock_data['Date'].dt.date == pd.to_datetime(cur_date, utc = True).date())]['Mid'].values[0]
            except Exception:
                continue
            strike_price = extract_strike_price_from_symbol(op_symbol)
            exp_date = pd.to_datetime(op_symbol[-15:-9], format = '%y%m%d', utc = True)
            if op_symbol[-9] == 'P':
                op_type = 'put'
            elif op_symbol[-9] == 'C':
                op_type = 'call'
            dte = (exp_date - cur_date).days

            if dte > 0:

                iv = calculate_implied_volatility(option_price = mid_op_price,
                                                  S = cur_stock_price,
                                                  K = strike_price,
                                                  T =  dte / 365,
                                                  r = get_risk_free_rate_estimate(dte),
                                                  option_type = op_type)
                delta = calculate_delta_historical(option_price = mid_op_price,
                                                   strike_price = strike_price,
                                                   expiry = pd.Timestamp(exp_date),
                                                   underlying_price = cur_stock_price,
                                                   risk_free_rate = get_risk_free_rate_estimate(dte),
                                                   option_type = op_type,
                                                   timestamp = pd.Timestamp(cur_date))
                gamma = calculate_gamma_historical(option_price = mid_op_price,
                                                   strike_price = strike_price,
                                                   expiry = pd.Timestamp(exp_date),
                                                   underlying_price = cur_stock_price,
                                                   risk_free_rate = get_risk_free_rate_estimate(dte),
                                                   option_type = op_type,
                                                   timestamp = pd.Timestamp(cur_date))
                vega = calculate_vega_historical(option_price = mid_op_price,
                                                   strike_price = strike_price,
                                                   expiry = pd.Timestamp(exp_date),
                                                   underlying_price = cur_stock_price,
                                                   risk_free_rate = get_risk_free_rate_estimate(dte),
                                                   option_type = op_type,
                                                   timestamp = pd.Timestamp(cur_date))
                theta = calculate_theta_historical(option_price = mid_op_price,
                                                   strike_price = strike_price,
                                                   expiry = pd.Timestamp(exp_date),
                                                   underlying_price = cur_stock_price,
                                                   risk_free_rate = get_risk_free_rate_estimate(dte),
                                                   option_type = op_type,
                                                   timestamp = pd.Timestamp(cur_date))

                # Add bounds to IV calculation
                if iv is not None and (iv < 0 or iv > 5):  # IV > 500% is suspicious
                    print(f"Unusual IV={iv:.2f} for {op_symbol} on {cur_date}")
                    iv = None  # or skip this data point

                # Delta should be bounded
                if op_type == 'call' and delta is not None and (delta < 0 or delta > 1):
                    print(f"Invalid call delta={delta:.2f} for {op_symbol}")
                    delta = None

                if op_type == 'put' and delta is not None and (delta < -1 or delta > 0):
                    print(f"Invalid put delta={delta:.2f} for {op_symbol}")
                    delta = None

                if iv != 0 and iv is not None and delta is not None:
                    df.append([op_symbol, high_op_price, mid_op_price, low_op_price, cur_date, cur_stock_price, strike_price, exp_date, op_type, iv, delta, dte, gamma, vega, theta])


        df = pd.DataFrame(df)
        if df.shape[0] != 0:
            
            df.columns = ['Symbol', 'High Option Price', 'Current Option Price', 'Low Option Price', 'Current Date', 'Current Stock Price', 'Strike Price', 'Expiration Date', 'Type', 'IV', 'Delta', 'DTE', 'Gamma', 'Vega', 'Theta']

            options_price_tracker[op_symbol] = df
            options_price_tracker[op_symbol] = options_price_tracker[op_symbol].sort_values(by = 'Current Date').reset_index(drop = True)
        
    return options_price_tracker

def get_historical_stock_price(symbol, timeframe, historical_start_date, historical_end_date = 'now', headers = None):
    
    if historical_end_date == 'now':
        now = datetime.now()
        historical_end_date = str(now.date())
    c = []
    h = []
    l = []
    t = []

    url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc"
    response = requests.get(url, headers = headers)

    data = json.loads(response.text)
    for idx in range(len(data['bars'][symbol])):
        c.append(data['bars'][symbol][idx]['c'])
        h.append(data['bars'][symbol][idx]['h'])    
        l.append(data['bars'][symbol][idx]['l'])    
        t.append(data['bars'][symbol][idx]['t'])

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc&page_token={data['next_page_token']}"
        response = requests.get(url, headers = headers)
        data = json.loads(response.text)
        for idx in range(len(data['bars'][symbol])):
            c.append(data['bars'][symbol][idx]['c'])
            h.append(data['bars'][symbol][idx]['h'])    
            l.append(data['bars'][symbol][idx]['l'])    
            t.append(data['bars'][symbol][idx]['t'])    

    c = np.array(c)
    h = np.array(h)
    l = np.array(l)
    t = np.array([pd.to_datetime(x) for x in t])
    
    return c, h, l, t

def _opt_cache_path(symbol: str, timeframe: str) -> Path:
    # One file per symbol per timeframe
    safe_symbol = symbol.replace('/', '_')
    return CACHE_ROOT / timeframe / f'{safe_symbol}.pkl'

def _load_cached_df(path: Path):
    if path.exists():
        try:
            return pd.read_pickle(path)
        except Exception:
            return None
    return None

def _save_cached_df(path: Path, df: pd.DataFrame):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)

def get_historical_options_data_cached(option_symbols, current_date, stock_data, timeframe, headers):
    """
    Cache-aware wrapper around get_historical_options_data.
    Single-file cache per underlying+timeframe: stores a dict[contract_symbol] -> DataFrame
    in one pickle file (e.g., options_cache_AAPL_1D.pkl) instead of one file per contract.

    - Loads the monolithic cache for the timeframe if present
    - For each requested symbol, if missing or stale (max Current Date < current_date),
      fetch updates, merge, and de-duplicate by Current Date
    - Saves the whole cache back to disk once if any updates occurred

    Returns a dict[symbol] -> DataFrame for the requested symbols.
    """
    current_date = pd.to_datetime(current_date)

    # --- Monolithic cache helpers ---
    def _mono_cache_path(tf: str, underlying: str) -> Path:
        safe_u = underlying.replace('/', '_').upper() if underlying else 'UNKNOWN'
        return CACHE_ROOT / f"options_cache_{safe_u}_{tf}.pkl"

    def _mono_load(tf: str, underlying: str) -> dict:
        path = _mono_cache_path(tf, underlying)
        if path.exists():
            try:
                data = pd.read_pickle(path)
                # Expecting a dict[symbol] -> DataFrame
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        # Backfill from any legacy per-contract cache files for this underlying if present
        legacy_dir = CACHE_ROOT / tf
        legacy_cache: dict = {}
        if legacy_dir.exists() and legacy_dir.is_dir():
            for f in legacy_dir.glob('*.pkl'):
                try:
                    df_legacy = pd.read_pickle(f)
                    if isinstance(df_legacy, pd.DataFrame):
                        # Heuristic: contract symbols start with underlying
                        if f.stem.upper().startswith((underlying or '').upper()):
                            legacy_cache[f.stem] = df_legacy
                except Exception:
                    continue
        return legacy_cache

    def _mono_save(tf: str, underlying: str, data: dict):
        path = _mono_cache_path(tf, underlying)
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.to_pickle(data, path)

    def _extract_underlying(sym: str) -> str:
        # Expected format: UNDERLYING + YYMMDD + (C|P) + 8-digit strike
        m = re.match(r'^([A-Z\.]+)\d{6}[CP]\d{8}$', sym)
        if m:
            return m.group(1)
        # Fallback: remove the last 15 chars (YYMMDD + C/P + 8 digits)
        return sym[:-15] if len(sym) > 15 else sym

    # Group requested option symbols by underlying
    by_underlying: dict[str, list[str]] = {}
    for sym in option_symbols:
        u = _extract_underlying(sym)
        by_underlying.setdefault(u, []).append(sym)

    final_result: dict = {}

    # Process per underlying symbol and persist per-underlying cache file
    for underlying, syms in by_underlying.items():
        cache_all = _mono_load(timeframe, underlying)
        to_fetch: set[str] = set()

        # Inspect cache for each requested symbol in this underlying
        for sym in syms:
            df_cached = cache_all.get(sym)
            if df_cached is None or len(df_cached) == 0:
                to_fetch.add(sym)
            else:
                final_result[sym] = df_cached
                if 'Current Date' in df_cached.columns:
                    try:
                        max_dt = pd.to_datetime(df_cached['Current Date']).max()
                        if pd.isna(max_dt) or max_dt.date() < current_date.date():
                            to_fetch.add(sym)
                    except Exception:
                        to_fetch.add(sym)
                else:
                    to_fetch.add(sym)

        updated = False
        if len(to_fetch) > 0:
            fetched = get_historical_options_data(
                option_symbols=list(to_fetch),
                current_date=current_date,
                stock_data=stock_data,
                timeframe=timeframe,
                headers=headers,
            )
            for sym, df_new in fetched.items():
                df_existing = cache_all.get(sym)
                if df_existing is not None and len(df_existing) > 0:
                    combined = pd.concat([df_existing, df_new], ignore_index=True)
                    if 'Current Date' in combined.columns:
                        combined['Current Date'] = pd.to_datetime(combined['Current Date'])
                        combined.drop_duplicates(subset=['Current Date'], keep='last', inplace=True)
                        combined.sort_values('Current Date', inplace=True)
                    cache_all[sym] = combined
                    final_result[sym] = combined
                else:
                    cache_all[sym] = df_new
                    final_result[sym] = df_new
                updated = True

        if updated:
            _mono_save(timeframe, underlying, cache_all)

    return final_result

def get_historical_options_data_cached_fast(option_symbols, current_date, stock_data, timeframe, headers):
    """
    Wrapper that minimizes disk I/O by using an in-memory cache first and only
    hitting the on-disk cache/API when either:
      - the symbol is not in memory, or
      - the cached data does not extend up to current_date.
    Returns dict[symbol] -> DataFrame
    """
    current_date = pd.to_datetime(current_date)
    fresh = {}
    to_fetch = []

    for sym in option_symbols:
        df_cached = options_cache_memory.get(sym)
        if df_cached is None or len(df_cached) == 0:
            to_fetch.append(sym)
        else:
            try:
                max_dt = pd.to_datetime(df_cached['Current Date']).max()
                if pd.isna(max_dt) or max_dt.date() < current_date.date():
                    to_fetch.append(sym)
                else:
                    fresh[sym] = df_cached
            except Exception:
                to_fetch.append(sym)

    if len(to_fetch) > 0:
        fetched = get_historical_options_data_cached(option_symbols=to_fetch,
                                                     current_date=current_date,
                                                     stock_data=stock_data,
                                                     timeframe=timeframe,
                                                     headers=headers)
        # Merge fetched into memory and dedupe by Current Date
        for sym, df_new in fetched.items():
            df_existing = options_cache_memory.get(sym)
            if df_existing is not None and len(df_existing) > 0:
                combined = pd.concat([df_existing, df_new], ignore_index=True)
                if 'Current Date' in combined.columns:
                    combined['Current Date'] = pd.to_datetime(combined['Current Date'])
                    combined.drop_duplicates(subset=['Current Date'], keep='last', inplace=True)
                    combined.sort_values('Current Date', inplace=True)
                options_cache_memory[sym] = combined
            else:
                options_cache_memory[sym] = df_new

    # Build final result
    result = {}
    result.update(fresh)
    result.update({sym: options_cache_memory[sym] for sym in to_fetch if sym in options_cache_memory})
    return result

##############################################
########### Prediction Functions #############
##############################################

def get_historical_magnitude_plots(symbol, timeframe, historical_start_date, historical_end_date = 'now', day_offsets = [30, 60, 90, 180], headers = None):
    
    
    if historical_end_date == 'now':
        now = datetime.now()
        historical_end_date = str(now.date())
    c = []
    h = []
    l = []
    t = []

    url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc"
    response = requests.get(url, headers = headers)

    data = json.loads(response.text)
    for idx in range(len(data['bars'][symbol])):
        c.append(data['bars'][symbol][idx]['c'])
        h.append(data['bars'][symbol][idx]['h'])    
        l.append(data['bars'][symbol][idx]['l'])    
        t.append(data['bars'][symbol][idx]['t'])

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc&page_token={data['next_page_token']}"
        response = requests.get(url, headers = headers)
        data = json.loads(response.text)
        for idx in range(len(data['bars'][symbol])):
            c.append(data['bars'][symbol][idx]['c'])
            h.append(data['bars'][symbol][idx]['h'])    
            l.append(data['bars'][symbol][idx]['l'])    
            t.append(data['bars'][symbol][idx]['t'])    

    c = np.array(c)
    h = np.array(h)
    l = np.array(l)
    t = np.array([pd.to_datetime(x) for x in t])
    
    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {historical_start_date} - {historical_end_date}")
    plt.plot(t, c)
    plt.xlabel('Closing Price')
    plt.ylabel('Time')
    plt.xticks(rotation = 90)
    plt.show()
    
    for offset in day_offsets:
        now = datetime.now()
        filter_start_date = (now - timedelta(days = offset)).date()
        filter_end_date = str(now.date())

        ###############################
        ########## Histogram ##########
        ###############################

        fig, axs = plt.subplots(2, 2, figsize=(20, 20))

        axs[0][0].set_title(f'Histogram of Day-to-Day Changes\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')

        filter_start_date_ = pd.to_datetime(filter_start_date, utc = True)
        filter_end_date_ = pd.to_datetime(filter_end_date, utc = True)
        indices = np.where((t >= filter_start_date_) & (t <= filter_end_date_))

        bins = axs[0][0].hist(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1], bins = 100, density = True)
        axs[0][0].set_xlabel('% Change')
        axs[0][0].set_ylabel('Probability Density')

        axs[0][1].set_title(f'Histogram of Day-to-Day Change Magnitudes\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        bins = axs[0][1].hist(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, density = True)
        axs[0][1].set_xlabel('Magnitude of % Change')
        axs[0][1].set_ylabel('Probability Density')

        ###############################
        ######## Power Law Fit ########
        ###############################

        # Create histogram
        counts, bin_edges = np.histogram(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, density = True)

        # Calculate bin centers (not edges)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # Remove zero counts to avoid log(0)
        nonzero_mask = counts > 0
        x_data = bin_centers[nonzero_mask]
        y_data = counts[nonzero_mask]

        param, _ = opt.curve_fit(f = power_law_log,
                                 xdata = np.log(x_data),
                                 ydata = np.log(y_data))

        alpha, logC = param

        axs[1][0].set_title(f'Linear Scale Power Law Fit\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        axs[1][0].hist(np.abs(100 * (c[indices][1:] - c[indices][:-1]) / c[indices][:-1]), bins = 100, label = 'Magnitude of Change', density = True)
        axs[1][0].plot(x_data, np.exp(logC) * x_data**(-alpha), 'r-', linewidth = 2, label = f'Power Law Fit: $\gamma = {alpha:.3f}\ |\ c = {np.exp(logC):.3f}$')
        axs[1][0].set_xlabel('Magnitude of % Change')
        axs[1][0].set_ylabel('Probability Density')
        axs[1][0].legend()

        axs[1][1].set_title(f'Log Scale Power Law Fit\n{filter_start_date} - {filter_end_date}\nLast {offset} Days')
        axs[1][1].loglog(x_data, y_data, 'o', label = 'Magnitude of Change')
        axs[1][1].loglog(x_data, np.exp(logC) * x_data ** (-alpha), 'r-', linewidth = 2, label = f'Power Law Fit: $\gamma = {alpha:.3f}\ |\ c = {np.exp(logC):.3f}$')
        axs[1][1].set_xlabel('Magnitude of % Change')
        axs[1][1].set_ylabel('Probability Density')
        axs[1][1].legend()

        plt.show()
        
def get_prediction_error(symbol, timeframe, train_start_date, train_end_date, test_start_date, test_end_date, headers):

    historical_start_date = train_start_date
    historical_end_date = test_end_date

    if historical_end_date == 'now':
        now = datetime.now()
        historical_end_date = str(now.date())
        test_end_date = str(now.date())
    c = []
    h = []
    l = []
    t = []

    url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc"
    response = requests.get(url, headers = headers)

    data = json.loads(response.text)
    for idx in range(len(data['bars'][symbol])):
        c.append(data['bars'][symbol][idx]['c'])
        h.append(data['bars'][symbol][idx]['h'])    
        l.append(data['bars'][symbol][idx]['l'])    
        t.append(data['bars'][symbol][idx]['t'])

    while data['next_page_token'] is not None:
        url = f"https://data.alpaca.markets/v2/stocks/bars?symbols={symbol}&timeframe={timeframe}&start={historical_start_date}&end={historical_end_date}&limit=1000&adjustment=raw&feed=iex&sort=asc&page_token={data['next_page_token']}"
        response = requests.get(url, headers = headers)
        data = json.loads(response.text)
        for idx in range(len(data['bars'][symbol])):
            c.append(data['bars'][symbol][idx]['c'])
            h.append(data['bars'][symbol][idx]['h'])    
            l.append(data['bars'][symbol][idx]['l'])    
            t.append(data['bars'][symbol][idx]['t'])    

    c = np.array(c)
    h = np.array(h)
    l = np.array(l)
    t = np.array([pd.to_datetime(x) for x in t])

    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {historical_start_date} - {historical_end_date}")
    plt.plot(t, c)
    plt.xlabel('Closing Price')
    plt.ylabel('Time')
    plt.xticks(rotation = 90)
    plt.show()
    
    train_start = np.where(t > pd.to_datetime(train_start_date, utc = True))[0][0]
    train_end = np.where(t > pd.to_datetime(train_end_date, utc = True))[0][0]
    test_start = np.where(t > pd.to_datetime(test_start_date, utc = True))[0][0]
    test_end = np.where(t >= pd.to_datetime(test_end_date, utc = True))[0][-1]

    train = c[train_start:train_end]
    possible_changes = train[1:] - train[:-1]
    sampled_changes = np.random.choice(possible_changes, test_end - test_start)


    predicted_matrix = []
    gaussian_matrix = []

    difference = []

    for _ in range(100):
        current_value = train[-1]
        predicted_values = [current_value]
        gaussian_values = [current_value]
        for idx in range(len(sampled_changes)):
            predicted_values.append(predicted_values[-1] + sampled_changes[idx])
            gaussian_values.append(gaussian_values[-1] + np.random.normal(loc = np.mean(possible_changes), scale = np.std(possible_changes)))
        predicted_matrix.append(predicted_values)
        gaussian_matrix.append(gaussian_values)

        predicted_error = np.mean(np.square(c[test_start:test_end + 1] - predicted_values))
        gaussian_error = np.mean(np.square(c[test_start:test_end + 1] - gaussian_values))
        difference.append(predicted_error - gaussian_error)


    predicted_values = np.mean(predicted_matrix, axis = 0)
    gaussian_values = np.mean(gaussian_matrix, axis = 0)

    predicted_std = np.std(predicted_matrix, axis = 0)
    gaussian_std = np.std(gaussian_matrix, axis = 0)

    predicted_upper = predicted_values + predicted_std
    predicted_lower = predicted_values - predicted_std

    gaussian_upper = gaussian_values + gaussian_std
    gaussian_lower = gaussian_values - gaussian_std

    predicted_error = np.mean(np.square(c[test_start:test_end + 1] - predicted_values))
    gaussian_error = np.mean(np.square(c[test_start:test_end + 1] - gaussian_values))

    plt.figure(figsize = (15, 5))
    plt.title(f"{symbol}: {test_start_date} - {test_end_date} | Sampled Error - Gaussian Error: ${np.mean(difference):.3f} \pm {np.std(difference):.3f}$")
    plt.plot(t[test_start:], c[test_start:], label = 'True')


    plt.plot(t[test_start:], predicted_values, label = f'Sampled Prediction (MSE = {predicted_error:.3f})')
    plt.fill_between(t[test_start:test_end + 1], predicted_lower, predicted_upper, color = 'red', alpha = 1)

    plt.plot(t[test_start:], gaussian_values, label = f'Gaussian Prediction (MSE = {gaussian_error:.3f})')
    plt.fill_between(t[test_start:test_end + 1], gaussian_lower, gaussian_upper, color = 'green', alpha = 0.1)


    plt.xticks(rotation = 90)
    plt.xlabel('Time')
    plt.ylabel('Price ($)')
    plt.legend()
    plt.show()

    plt.figure(figsize = (15, 5))
    plt.title('Distribution of Errors Between Sampled and Gaussian Predictions')
    plt.hist(difference, bins = 50)
    plt.xlabel('MSE(Sampled) - MSE(Gaussian)')
    plt.ylabel('Frequency')
    plt.show()
    
##############################################
######## Options Strategy Functions ##########
##############################################

def filter_option_trades_by_ff(current_price, strike_group, min_time_distance, max_time_distance, min_ff_perc, max_ff_perc, current_date = 'now', print_output = True):
    
    return_list = []
    # Print out all calendar spreads with a forward factor >= 20%
    for key in strike_group:
        
        if print_output:
            print (f"############################################\n####### Strike Price (Current Price) #######\n############## {key} ({current_price}) ################\n############################################\n")
        df = strike_group[key]
        
        if current_date == 'now':
            now = datetime.now()
        else: 
            now = pd.to_datetime(current_date, utc = True)

        col_names = list(df.columns)
        exp_idx = col_names.index('Expiration Date')
        iv_idx = col_names.index('IV')
        
        try:
            sell_idx = col_names.index('Sell Price')
            buy_idx = col_names.index('Buy Price')
        except:
            sell_idx = col_names.index('Low Option Price')
            buy_idx = col_names.index('High Option Price')
        
        for idx in range(df.shape[0]):

            out_list = []

            dte_1 = (pd.to_datetime(df.values[idx][exp_idx], utc = True) - pd.to_datetime(now, utc = True)).days
            iv_1 = df.values[idx][iv_idx]
            
            out_list.append(df.values[idx])

            for j in range(idx + 1, df.shape[0]):
                dte_2 = (pd.to_datetime(df.values[j][exp_idx], utc = True) - pd.to_datetime(now, utc = True)).days
                iv_2 = df.values[j][iv_idx]

                if (dte_2 - dte_1 >= min_time_distance) and (dte_2 - dte_1 <= max_time_distance):
                    f_ratio = forward_ratio(dte_1, dte_2, iv_1, iv_2)

                    if f_ratio >= min_ff_perc and f_ratio <= max_ff_perc:
                        
                        if print_output:
                            print (f"Forward Ratio: {f_ratio:.3f}% | DTE Front (Sell): {dte_1} | DTE Back (Buy): {dte_2}")
                        out_list.append(df.values[j])
                        
                        net_position = out_list[0][sell_idx] - out_list[1][buy_idx] # sell price - buy price
                        
                        out_pd = pd.DataFrame(out_list)
                        out_pd.columns = col_names
                        
                        out_pd['Forward Factor'] = [f_ratio, f_ratio]
                        out_pd['Net'] = [net_position, net_position]
                        
                        if print_output:
                            display(out_pd)
                        return_list.append(out_pd)
                        out_list = out_list[:-1]
                        
    return return_list

##############################################
############ Backtest Functions ##############
##############################################
    
def extract_strike_price_from_symbol(symbol: str) -> float:
    """
    Extract strike price from option symbol.
    Converts last 8 digits of option symbol to strike price by dividing by 1000.

    Args:
        symbol: Option symbol (e.g., 'SPY250616P00571000')

    Returns:
        float: Strike price (e.g., 571.0) or 0.0 if invalid format
    """
    # Option symbols typically have format: TICKER + YYMMDD + (C/P) + 8-digit strike price
    # The last 8 digits represent strike price * 1000
    try:
        # Extract the last 8 digits and convert to strike price
        strike_str = symbol[-8:]
        strike_price = float(strike_str) / 1000.0
        return strike_price
    except (ValueError, IndexError):
        print(f"Warning: Could not extract strike price from symbol {symbol}")
        return 0.0
    
def generate_option_symbols(symbol, current_date, end_date_delta, min_strike, max_strike, strike_increment, op_type):

    option_symbols = []
    
    if op_type == 'put':
        op = 'P'
    else: 
        op = 'C'
    
    for delta in range(end_date_delta[0], end_date_delta[1] + 1, 1): 
        
        # Format expiration datetime as date: YYMMDD
        expiration_date = current_date.date() + timedelta(days = delta)
        exp_str = expiration_date.strftime("%y%m%d")

        # Generate strikes in increments (rounds UP to the nearest integer)
        current_strike = np.ceil(min_strike / strike_increment) * strike_increment
        
        while current_strike <= max_strike:
            # Format strike price as 8-digit integer (multiply by 1000)
            strike_formatted = f"{int(current_strike * 1000):08d}"
            # Create option symbol: SPY + YYMMDD + P + 8-digit strike
            option_symbol = f"{symbol}{exp_str}{op}{strike_formatted}"
            option_symbols.append(option_symbol)

            current_strike += strike_increment

    return option_symbols
    
def get_single_position_value(position):

    short_pnl = (position['short_entry_price'] - position['current_short_price']) * 100 * position['num_contracts']
    long_pnl = (position['current_long_price'] - position['long_entry_price']) * 100 * position['num_contracts']
    total_pnl = short_pnl + long_pnl

    return total_pnl

def get_all_open_positions_value(open_positions):
    
    open_positions_value = 0
    for trade_id, position in open_positions.items():
        if position['status'] == 'open':            
            open_positions_value += get_single_position_value(position)
    
    return open_positions_value

def execute_identified_trades(list_of_options_dataframes, open_positions, current_cash, current_date, trade_id_counter, total_capital_deployed, max_number_of_open_positions, stock_strike_deviation_limit = 0.1, max_position_ratio_of_capital_per_trade = 0.02, num_contracts_limit = None):

    num_trades_executed_today = 0
    # For calendar spreads: Sell near-term, Buy far-term (same strike)
    for trade in list_of_options_dataframes:
        
        if len([p for p in open_positions.values() if p['status'] == 'open']) >= max_number_of_open_positions:
            break  # Hit max concurrent positions
            
        stock_strike_deviation = abs(trade['Current Stock Price'][0] - trade['Strike Price'][0]) / trade['Current Stock Price'][0]
        if stock_strike_deviation <= stock_strike_deviation_limit:
            
            # Execute Trades
            trade_id_counter += 1

            # Extract trade details
            short_leg_symbol = trade['Symbol'][0]  # Near-term expiry (sell)
            long_leg_symbol = trade['Symbol'][1]   # Far-term expiry (buy)
            strike = trade['Strike Price'][0]
            option_type = trade['Type'][0]
            forward_factor = trade['Forward Factor'][0]

            # Get entry prices
            short_leg_price = trade['Low Option Price'][0]  # Premium received (credit)
            long_leg_price = trade['High Option Price'][1]  # Premium paid (debit)

            # Net debit for calendar spread (we pay more for long than we receive for short)
            net_debit = (long_leg_price - short_leg_price) * 100  # Per contract

            # Position sizing: don't exceed max_position_ratio_of_capital of portfolio
            open_positions_value = get_all_open_positions_value(open_positions)
            current_portfolio_value = current_cash + open_positions_value
            
            max_position_value = current_portfolio_value * max_position_ratio_of_capital_per_trade

            if num_contracts_limit is None:
                num_contracts = max(1, int(max_position_value / net_debit))
            else:
                num_contracts = num_contracts_limit

            # Check if we have enough cash for lesser contracts
            total_cost = net_debit * num_contracts

            if total_cost > current_cash:
                num_contracts = max(1, int(current_cash / net_debit))
                total_cost = net_debit * num_contracts

            if total_cost > current_cash:
                trade_id_counter -= 1
                continue  # Not enough capital

            # Deduct capital
            current_cash -= total_cost
            total_capital_deployed += total_cost

            # Get expiry dates
            short_expiry = trade['Expiration Date'][0]
            long_expiry = trade['Expiration Date'][1]

            # Store the position
            open_positions[trade_id_counter] = {
                'entry_date': pd.to_datetime(current_date),
                'entry_credit' : -total_cost,
                'entry_stock_price': trade['Current Stock Price'][0],
                'strike': strike,
                'IV Short' : trade['IV'][0],
                'IV Long' : trade['IV'][1],
                'Delta_Short' : trade['Delta'][0],
                'Delta_Long' : trade['Delta'][1],
                'Gamma Short' : trade['Gamma'][0],
                'Gamma Long' : trade['Gamma'][1],
                'Theta Short' : trade['Theta'][0],
                'Theta Long' : trade['Theta'][1],
                'Vega Short' : trade['Vega'][0],
                'Vega Long' : trade['Vega'][1],
                'option_type': option_type,
                'short_leg': short_leg_symbol,
                'long_leg': long_leg_symbol,
                'short_expiry': short_expiry,
                'long_expiry': long_expiry,
                'num_contracts': num_contracts,
                'capital_deployed': total_cost,
                'short_entry_price': short_leg_price,
                'long_entry_price': long_leg_price,
                'current_short_price': short_leg_price,
                'current_long_price': long_leg_price,
                'current_pnl': -total_cost, # Net Debit Position 
                'forward_factor' : forward_factor,
                'status': 'open', 
                'spread_distance': (long_expiry - short_expiry).days
            }
            
            
            num_trades_executed_today += 1

    return open_positions, num_trades_executed_today, current_cash, trade_id_counter

def exit_short_leg(position, current_date, options_price_tracker, reason = None, stock_data: pd.DataFrame | None = None):

    if position['short_leg'] in options_price_tracker:
        
        # Get last available price before expiry
        short_leg_data = options_price_tracker[position['short_leg']]

        if current_date >= position['short_expiry']: 
            short_leg_data = short_leg_data[short_leg_data['Current Date'] <= position['short_expiry']]
            position['short_exit_reason'] = 'Short Expired'
            position['short_exit_date'] = position['short_expiry']
        
        else:
            short_leg_data = short_leg_data[short_leg_data['Current Date'] <= current_date]
            position['short_exit_reason'] = f'Early Exit: {str(reason)}'
            position['short_exit_date'] = current_date.date()

        if len(short_leg_data) > 0:
            # Use current/mid price for consistency
            price_col = 'Current Option Price' if 'Current Option Price' in short_leg_data.columns else 'High Option Price'
            short_exit_price = short_leg_data.iloc[-1][price_col]
        
        # Assume the option was executed 
        else:

            # Calculate intrinsic value at expiry
            if stock_data is not None:
                try:
                    stock_price_at_expiry = stock_data[stock_data['Date'].dt.date == position['short_expiry'].date()]['Low'].values[0]
                    if position['option_type'] == 'call':
                        short_exit_price = max(0, stock_price_at_expiry - position['strike'])
                    else:  # put
                        short_exit_price = max(0, position['strike'] - stock_price_at_expiry)
                except Exception:
                    short_exit_price = 0
            else:
                short_exit_price = 0
    else:
        short_exit_price = 0  # Assume worthless if no data

    position['current_short_price'] = short_exit_price
    
def exit_long_leg(position, current_date, current_cash, options_price_tracker, reason = None, stock_data: pd.DataFrame | None = None):

    # Long leg expired - close position
    if position['long_leg'] in options_price_tracker:
        long_leg_data = options_price_tracker[position['long_leg']]

        if current_date >= position['long_expiry']:
            long_leg_data = long_leg_data[long_leg_data['Current Date'] <= position['long_expiry']]
            position['long_exit_reason'] = 'Long Expired'
            position['long_exit_date'] = position['long_expiry']
        else: 
            long_leg_data = long_leg_data[long_leg_data['Current Date'] <= current_date]
            position['long_exit_reason'] = f'Early Exit: {str(reason)}'
            position['long_exit_date'] = current_date.date()

        
        if len(long_leg_data) > 0:
            # Use current/mid price for consistency
            price_col = 'Current Option Price' if 'Current Option Price' in long_leg_data.columns else 'Low Option Price'
            long_exit_price = long_leg_data.iloc[-1][price_col]
        
        # Assume the option was executed
        else:
            # Calculate intrinsic value at expiry
            if stock_data is not None:
                try:
                    stock_price_at_expiry = stock_data[stock_data['Date'].dt.date == position['long_expiry'].date()]['High'].values[0]
                    if position['option_type'] == 'call':
                        long_exit_price = max(0, stock_price_at_expiry - position['strike'])
                    else:  # put
                        long_exit_price = max(0, position['strike'] - stock_price_at_expiry)
                except Exception:
                    long_exit_price = 0
            else:
                long_exit_price = 0
    else:
        long_exit_price = 0
        
    
    position['current_long_price'] = long_exit_price

    # Exit overall position (long and short)
    total_pnl = get_single_position_value(position)
    
    # Return capital plus P&L (return principal + realized P&L)
    current_cash += position['capital_deployed'] + total_pnl
    
    position['exit_date'] = current_date
    position['final_pnl'] = total_pnl
    position['return_pct'] = (total_pnl / position['capital_deployed']) * 100
    position['status'] = 'closed'

    return current_cash
    

    # (duplicate helper functions removed below; using single definitions above)

def check_existing_positions(portfolio_history, open_positions, closed_positions, current_date, current_cash, options_price_tracker, short_exit_limits = (-0.4, 0.3), long_exit_limits = (-0.4, 0.3), position_exit_limits = (-0.4, 0.3), stock_data = None):

    positions_to_close = []
    
    for trade_id, position in open_positions.items():
        
        # Ignore closed trades
        if position['status'] != 'open':
            continue
            
        current_date = pd.to_datetime(current_date)
        
        ###########################
        ##### Check Short Leg #####
        ###########################

        # Check if short leg has expired
        if current_date >= position['short_expiry']:
            exit_short_leg(position, current_date, options_price_tracker, stock_data = stock_data)
            
        else:

            # Update current price of short leg if short position has not been exited
            if position['short_leg'] in options_price_tracker and 'short_exit_date' not in position:
                
                short_leg_data = options_price_tracker[position['short_leg']]
                short_current = short_leg_data[short_leg_data['Current Date'].dt.date == current_date.date()]
                
                if len(short_current) > 0:
                    position['current_short_price'] = short_current.iloc[0]['Current Option Price'] if 'Current Option Price' in short_current.columns else short_current.iloc[0].get('High Option Price', np.nan)
                else:
                    position['current_short_price'] = 0  # Assume worthless if no data

                # Limit Loss
                short_pnl_ratio = (position['short_entry_price'] - position['current_short_price']) / position['short_entry_price']

                if short_pnl_ratio <= short_exit_limits[0]:
                    exit_short_leg(position, current_date, options_price_tracker, reason = f'Limit Loss: {short_pnl_ratio * 100:.3f}%', stock_data = stock_data)

                if short_pnl_ratio >= short_exit_limits[1]:
                    exit_short_leg(position, current_date, options_price_tracker, reason = f'Profit Reached: {short_pnl_ratio * 100:.3f}%', stock_data = stock_data)
                    
        ##########################
        ##### Check Long Leg #####
        ##########################
        
        # Check if long leg has expired or should be closed
        if current_date >= position['long_expiry']:

            current_cash = exit_long_leg(position, current_date, current_cash, options_price_tracker)
            positions_to_close.append(trade_id)
            
        else:

            # Update current price of long leg if it has not already been exited
            if position['long_leg'] in options_price_tracker and 'long_exit_date' not in position:
                long_leg_data = options_price_tracker[position['long_leg']]
                long_current = long_leg_data[long_leg_data['Current Date'].dt.date == current_date.date()]
                
                if len(long_current) > 0:
                    position['current_long_price'] = long_current.iloc[0]['Current Option Price']
                else:
                    position['current_long_price'] = 0  # Assume worthless if no data
                
                # Limit Loss
                long_pnl_ratio = (position['current_long_price'] - position['long_entry_price']) / position['long_entry_price']

                if long_pnl_ratio <= long_exit_limits[0]:
                    current_cash = exit_long_leg(position, current_date, current_cash, options_price_tracker, reason = f'Limit Loss: {long_pnl_ratio * 100:.3f}%', stock_data = stock_data)
                    positions_to_close.append(trade_id)

                if long_pnl_ratio >= long_exit_limits[1] and current_date >= position['short_expiry']:
                    current_cash = exit_long_leg(position, current_date, current_cash, options_price_tracker, reason = f'Profit Reached: {long_pnl_ratio * 100:.3f}%', stock_data = stock_data)
                    positions_to_close.append(trade_id)


        # Calculate Current PnL 
        position['current_pnl'] = get_single_position_value(position)
        position['current_date'] = current_date
        position['return_pct'] = (position['current_pnl'] / position['capital_deployed'])
        
        # Check stop loss on TOTAL position
        if position['return_pct'] <= position_exit_limits[0]:

            exit_short_leg(position, current_date, options_price_tracker, reason = f'Position Limit Loss: {position["return_pct"] * 100:.3f}%', stock_data = stock_data)
            current_cash = exit_long_leg(position, current_date, current_cash, options_price_tracker, reason = f'Position Limit Loss: {position["return_pct"] * 100:.3f}%', stock_data = stock_data)
            positions_to_close.append(trade_id)
            
        # Check profit target
        if position['return_pct'] >= position_exit_limits[1]:
            exit_short_leg(position, current_date, options_price_tracker, reason = f'Position Profit Reached: {position["return_pct"] * 100:.3f}%', stock_data = stock_data)
            current_cash = exit_long_leg(position, current_date, current_cash, options_price_tracker, reason = f'Position Profit Reached: {position["return_pct"] * 100:.3f}%', stock_data = stock_data)
            positions_to_close.append(trade_id)

    # Move closed positions to closed_positions list
    for trade_id in positions_to_close:
        if trade_id in open_positions:
            closed_positions.append(open_positions[trade_id])
            del open_positions[trade_id]
        

    total_unrealized_pnl = sum([p['current_pnl'] for p in open_positions.values() if p['status'] == 'open'])
    total_deployed = sum([p['capital_deployed'] for p in open_positions.values() if p['status'] == 'open'])
    total_portfolio_value = current_cash + total_unrealized_pnl + total_deployed


    # Track portfolio history
    portfolio_history.append({
        'date': pd.to_datetime(current_date),
        'cash': current_cash,
        'open_positions_value': get_all_open_positions_value(open_positions),
        'total_value': total_portfolio_value,
        'num_open_positions': len([p for p in open_positions.values() if p['status'] == 'open']),
        'num_closed_positions': len(closed_positions)
    })

    return portfolio_history, open_positions, closed_positions, current_cash