import pandas as pd
import numpy as np
from datetime import date as _date
from .rh_fixes import get_historical_portfolio



def get_portfolio_history_old(interval='day', span='5year'):
    """
    Fetch historical portfolio data (equity value) using robin_stocks.
    Returns a pandas DataFrame with columns:
        ['timestamp', 'equity', 'adjusted_equity_previous_close']
    """
    raw_data = get_historical_portfolio(interval=interval, span=span)
    
    # 'equity_historicals' is a list of daily records
    historicals = raw_data['equity_historicals']
    
    # Convert to DataFrame
    df = pd.DataFrame(historicals)
    
    # We can choose close_equity or open_equity. 
    # Let's pick 'close_equity' to represent daily portfolio value:
    df['timestamp'] = pd.to_datetime(df['begins_at'], utc=True).dt.tz_convert(None)
    df['equity'] = df['adjusted_close_equity'].astype(float)
    #df['adjusted_equity_previous_close'] = df['adjusted_equity_previous_close'].astype(float)
    
    # Keep only necessary columns
    df = df[['timestamp', 'equity']]
    
    # Sort by date ascending
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df

def get_portfolio_history(r, login_result, interval='day', span='5year'):
    """
    Fetch historical portfolio data (equity value) using robin_stocks.
    Returns a pandas DataFrame with columns:
        ['timestamp', 'equity', 'adjusted_equity_previous_close']
    """
    historical_df = get_historical_portfolio(r, login_result=login_result, span='all')
    historical_df = historical_df[['date', 'cursor_data.price_chart_data.dollar_value.amount']]
    historical_df = historical_df.rename(columns={
        'date': 'timestamp',
        'cursor_data.price_chart_data.dollar_value.amount': 'equity'
    })
    historical_df['timestamp'] = pd.to_datetime(historical_df['timestamp'], utc=True).dt.tz_convert(None)
    historical_df['equity'] = historical_df['equity'].astype(float)

    return historical_df


def get_ticker_history(r, ticker, interval='day', span='5year'):
    """
    Fetch historical data for a given ticker (e.g. SPY, ARKK) using robin_stocks.
    Returns a pandas DataFrame with columns:
        ['timestamp', 'close_price']
    """
    raw_data = r.stocks.get_stock_historicals(
        ticker,
        interval=interval,
        span=span,
        bounds='regular'
    )
    
    # Convert to DataFrame
    df = pd.DataFrame(raw_data)
    
    # Example fields per record:
    #   {
    #       'begins_at': '2023-01-01T00:00:00Z',
    #       'close_price': '400.00',
    #       'open_price': '398.00',
    #       ...
    #   }
    
    df['timestamp'] = pd.to_datetime(df['begins_at'], utc=True).dt.tz_convert(None)
    df['close_price'] = df['close_price'].astype(float)
    
    # Keep only necessary columns
    df = df[['timestamp', 'close_price']]
    
    # Sort by date ascending
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df


###############################################################################
#                          DATA PROCESSING HELPERS
###############################################################################
def filter_by_year(df, year):
    """
    Returns a slice of DataFrame for a specific calendar year.
    Expects a 'timestamp' column in df.
    """
    start = pd.Timestamp(f'{year}-01-01')
    end = pd.Timestamp(f'{year}-12-31 23:59:59')

    return df[(df['timestamp'] >= start) & (df['timestamp'] <= end)].copy()


def filter_by_date_range(df, start_date, end_date):
    """
    Returns data between start_date and end_date (inclusive).
    start_date/end_date can be strings or Timestamps.
    """
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    return df[(df['timestamp'] >= start) & (df['timestamp'] <= end)].copy()


def calculate_daily_returns(df, price_col='equity'):
    """
    Return a daily return Series indexed by calendar day.
    - Parses 'timestamp', normalizes tz, collapses intraday to one price per day (last),
      then pct_change. Works for both portfolio (equity) and tickers (close_price).
    """
    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')

    # If tz-aware, convert to US/Eastern then drop tz for consistent date grouping
    try:
        if getattr(df['timestamp'].dt, 'tz', None) is not None:
            df['timestamp'] = df['timestamp'].dt.tz_convert('US/Eastern').dt.tz_localize(None)
    except Exception:
        # If tz_convert fails (naive), keep as-is
        pass

    # Ensure numeric prices and keep valid rows
    df[price_col] = pd.to_numeric(df[price_col], errors='coerce')
    df = df.dropna(subset=['timestamp', price_col]).sort_values('timestamp')

    # One observation per day (last value per day), then compute returns
    daily_prices = (
        df.assign(date=df['timestamp'].dt.floor('D'))
          .groupby('date', sort=True)[price_col]
          .last()
    )

    ret = daily_prices.pct_change()
    ret.name = f"{price_col}_daily_return"
    return ret


###############################################################################
#                         PERFORMANCE METRICS CLASSES
###############################################################################
class PerformanceMetrics:
    """
    Class holding static methods to compute various performance metrics.
    """
    
    @staticmethod
    def annual_return(daily_returns):
        """
        Approximate annual return given daily returns.
        If daily_returns is for 1 full year, this yields that year's total return.
        """
        # Convert daily returns to total return factor
        cum_return_factor = (1 + daily_returns).prod()
        # For single-year data, this is simply (cum_return_factor - 1).
        # For partial year or multiple years, you may want to scale by # of trading days, etc.
        return cum_return_factor - 1.0
    
    @staticmethod
    def annualized_volatility(daily_returns, trading_days=252):
        """
        Compute annualized standard deviation of returns.
        """
        return daily_returns.std() * np.sqrt(trading_days)
    
    @staticmethod
    def sharpe_ratio(daily_returns, risk_free_rate=0.02, trading_days=252):
        """
        Sharpe Ratio = (Rp - Rf) / sigma,
        where Rf is risk-free rate, assumed annual,
        and we scale daily Rf to daily basis for convenience.
        """
        # Convert annual risk-free rate to daily
        daily_rf = (1 + risk_free_rate)**(1/trading_days) - 1
        
        excess_return_daily = daily_returns - daily_rf
        mean_excess = excess_return_daily.mean()
        std_excess = excess_return_daily.std()
        
        # annualize the mean_excess
        annual_excess = mean_excess * trading_days
        annual_std = std_excess * np.sqrt(trading_days)
        
        if annual_std == 0:
            return np.nan
        
        return annual_excess / annual_std
    
    @staticmethod
    def beta_alpha(portfolio_returns, market_returns, risk_free_rate=0.02, trading_days=252):
        """
        Compute Beta and Alpha using a simple linear regression with the CAPM model:
            (Rp - Rf) = alpha + beta * (Rm - Rf).
        
        Returns a tuple: (beta, alpha)
        - alpha is annualized
        - beta is slope of regression
        """
        # Convert to aligned series
        df = pd.DataFrame({
            'portfolio': portfolio_returns,
            'market': market_returns
        }).dropna()
        
        # If insufficient data, return None
        if len(df) < 2:
            return (np.nan, np.nan)
        
        daily_rf = (1 + risk_free_rate)**(1/trading_days) - 1
        
        df['excess_portfolio'] = df['portfolio'] - daily_rf
        df['excess_market'] = df['market'] - daily_rf
        
        # Perform linear regression: y = alpha + beta * x
        # Using np.polyfit on (excess_market, excess_portfolio)
        # slope = beta, intercept = alpha (but this alpha is daily alpha in decimal form)
        beta, alpha_daily = np.polyfit(df['excess_market'], df['excess_portfolio'], 1)
        
        # Annualize alpha (rough approximation by scaling daily alpha * 252)
        alpha_annual = alpha_daily * trading_days
        
        return beta, alpha_annual
    
    @staticmethod
    def max_drawdown(series):
        """
        Calculate the maximum drawdown of a time series of values (e.g. cumulative equity).
        The minimum value must occur after the peak value.
        """
        # Calculate the running maximum
        running_max = series.cummax()
        
        # Calculate the drawdown
        drawdown = (series - running_max) / running_max
        
        # The maximum drawdown is the minimum value of the drawdown series
        max_drawdown = drawdown.min()
        
        return max_drawdown


def analyze_portfolio_performance(r, login_result,
    entity='portfolio',
    start_year=2020,
    end_year=2024,
    interval='day',
    span='5year',
    risk_free_rate=0.02
):
    """
    1. If entity == 'portfolio', fetch your personal portfolio data from Robinhood.
       Otherwise, fetch the specified ticker (e.g. 'SPY', 'AAPL', etc.)
    2. Fetch SPY (S&P 500) and ARKK as benchmarks over the same period.
    3. For each year in [start_year, end_year], compute:
       - Annual Return
       - Sharpe Ratio
       - Beta, Alpha (vs. SPY)
       - Max Drawdown
    4. Also compute the same metrics for the entire [start_year-01-01, end_year-12-31] range.
    
    Returns a dict of results for each year, plus "Cumulative".
    """
    # --------------------- Fetch Data ---------------------
    if entity == 'portfolio':
        data = get_portfolio_history(r, login_result, interval=interval, span=span)
    else:
        data = get_ticker_history(r, entity, interval=interval, span=span)
    
    ticker_histories = {
        'entity': data,
        'ARKK': get_ticker_history(r, 'ARKK', interval=interval, span=span),
        'SPY':  get_ticker_history(r, 'SPY', interval=interval, span=span),
        'DIA':  get_ticker_history(r, 'DIA', interval=interval, span=span),
        'QQQ':  get_ticker_history(r, 'QQQ', interval=interval, span=span),
        'IWM':  get_ticker_history(r, 'IWM', interval=interval, span=span)  # Russell 2000 ETF
    }

    # --------------------- Storage for results ---------------------
    yearly_results = {}
    
    # --------------------- Process each year individually ---------------------
    for year in range(start_year, end_year + 1):
        # Filter data for that year
        df_entity_yr = filter_by_year(ticker_histories['entity'], year)
        df_spy_yr    = filter_by_year(ticker_histories['SPY'], year)
        #df_arkk_yr   = filter_by_year(ticker_histories['ARKK'], year)
        
        # Calculate daily returns
        # Use 'equity' if it's the portfolio, else 'close_price'
        entity_col = 'equity' if entity == 'portfolio' else 'close_price'

        
        entity_ret_yr = calculate_daily_returns(df_entity_yr, entity_col)
        spy_ret_yr    = calculate_daily_returns(df_spy_yr, 'close_price')
        #arkk_ret_yr   = calculate_daily_returns(df_arkk_yr, 'close_price')
        
        # If no data for this year, skip
        if len(entity_ret_yr) == 0:
            continue
        
        # Annual Return
        ann_ret = PerformanceMetrics.annual_return(entity_ret_yr)
        
        # Sharpe Ratio
        sh_ratio = PerformanceMetrics.sharpe_ratio(
            entity_ret_yr, 
            risk_free_rate=risk_free_rate
        )
        
        # Beta, Alpha (vs. SPY)
        beta_spy, alpha_spy = PerformanceMetrics.beta_alpha(
            entity_ret_yr, 
            spy_ret_yr, 
            risk_free_rate=risk_free_rate
        )
        
        # Max Drawdown (need the actual price column to compute)
        max_dd = np.nan
        if len(df_entity_yr) > 0:
            max_dd = PerformanceMetrics.max_drawdown(df_entity_yr[entity_col])
        
        yearly_results[year] = {
            'Annual Return': ann_ret,
            'Sharpe Ratio': sh_ratio,
            'Beta (vs SPY)': beta_spy,
            'Alpha (vs SPY)': alpha_spy,
            'Max Drawdown': max_dd
        }
    
    # --------------------- Process Cumulative Period ---------------------
    start_date = f'{start_year}-01-01'
    end_date   = f'{end_year}-12-31'
    
    df_entity_cum = filter_by_date_range(ticker_histories['entity'], start_date, end_date)
    df_spy_cum    = filter_by_date_range(ticker_histories['SPY'], start_date, end_date)
    #df_arkk_cum   = filter_by_date_range(ticker_histories['ARKK'], start_date, end_date)

    entity_ret_cum = calculate_daily_returns(df_entity_cum, entity_col)
    spy_ret_cum    = calculate_daily_returns(df_spy_cum, 'close_price')
    #arkk_ret_cum   = calculate_daily_returns(df_arkk_cum, 'close_price')
    
    # Annual return for entire multi-year period
    total_days = (df_entity_cum['timestamp'].max() - df_entity_cum['timestamp'].min()).days
    trading_days_per_year = 252
    
    if len(entity_ret_cum) > 0:
        # Cumulative total return factor
        cum_factor = (1 + entity_ret_cum).prod()
        # Approx # of years in the range
        years_in_period = total_days / 365.0
        
        # Geometric annualized return:
        ann_ret_cum = cum_factor ** (1 / years_in_period) - 1
        
        # Sharpe (annualized)
        sh_ratio_cum = PerformanceMetrics.sharpe_ratio(
            entity_ret_cum, 
            risk_free_rate=risk_free_rate,
            trading_days=trading_days_per_year
        )
        
        # Beta, Alpha (vs. SPY)
        beta_spy_cum, alpha_spy_cum = PerformanceMetrics.beta_alpha(
            entity_ret_cum, 
            spy_ret_cum, 
            risk_free_rate=risk_free_rate,
            trading_days=trading_days_per_year
        )
        
        # Max Drawdown
        max_dd_cum = PerformanceMetrics.max_drawdown(df_entity_cum[entity_col])
        
        yearly_results['Cumulative'] = {
            'Annualized Return': ann_ret_cum,
            'Sharpe Ratio': sh_ratio_cum,
            'Beta (vs SPY)': beta_spy_cum,
            'Alpha (vs SPY)': alpha_spy_cum,
            'Max Drawdown': max_dd_cum
        }
    
    return yearly_results
