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


# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")



DATA_STORE_CORE = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature/code/Users/omai.r/spectral_nature/data/'

OPTION_HISTORY_STORE = os.path.join(DATA_STORE_CORE, 'common', 'option_history')
print(os.listdir(DATA_STORE_CORE))

def account_refresh():

    holdings_df = r.account.build_holdings(with_dividends=True)
    user_profile = r.account.build_user_profile()
    historical_df = r.account.get_historical_portfolio(interval='week', span='5year')


def find_optimal_option(r, ticker):
    # Fetch current price
    quote_data = r.stocks.get_quotes(ticker)[0]
    current_price = float(quote_data['last_trade_price'])
    
    # Retrieve option chains
    chain_id = r.options.get_chains(ticker)['id']
    options = r.options.get_option_market_data_by_id(chain_id)
    
    # Build a DataFrame
    df = pd.DataFrame(options)
    print(df)
    df['strike_price'] = df['strike_price'].astype(float)
    
    # Calculate distance from current price
    df['distance'] = abs(df['strike_price'] - current_price)
    
    # Get Greek data
    market_data = [r.options.get_option_market_data_by_id(opt['id'])[0] for opt in options]
    market_df = pd.DataFrame(market_data)
    df['gamma'] = market_df['gamma'].astype(float)
    df['theta'] = market_df['theta'].astype(float)
    df['iv'] = market_df['implied_volatility'].astype(float)
    
    # Define a simple metric to find the "optimal" option
    df['score'] = (
        (1 / (1 + df['distance'])) 
        + (df['gamma'] / (1 + abs(df['theta']))) 
        - (df['iv'] / 100)
    )
    
    # Pick option with highest score
    optimal = df.loc[df['score'].idxmax()]
    
    # Create interactive scatter plot
    fig_scatter = px.scatter(df, x="distance", y="score", title="Score vs Distance")
    fig_scatter.show()

    # Create interactive correlation heatmap
    corr = df[["distance", "gamma", "theta", "iv", "score"]].corr()
    fig_heatmap = px.imshow(corr, text_auto=True, color_continuous_scale='RdBu_r', title="Correlation Heatmap")
    fig_heatmap.show()
    
    return optimal



def get_closest_dates(ticker):
    # Get the current date
    current_date = datetime.date.today()
    
    # Get the expiration dates from Robinhood
    options_chain = r.options.get_chains(ticker)
    expiration_dates = options_chain['expiration_dates']
    
    # Convert expiration dates to datetime.date objects
    expiration_dates = [datetime.datetime.strptime(date, '%Y-%m-%d').date() for date in expiration_dates]
    
    # Define target dates
    target_dates = {
        '1_week': current_date + datetime.timedelta(weeks=1),
        '1_month': current_date + datetime.timedelta(days=30),
        '1_year': current_date + datetime.timedelta(days=365)
    }
    
    # Function to find the closest date
    def find_closest_date(target_date):
        closest_date = min(expiration_dates, key=lambda x: abs(x - target_date), default=None)
        return closest_date if closest_date else None
    
    # Find the closest dates
    closest_dates = {key: find_closest_date(target_date) for key, target_date in target_dates.items()}
    
    return closest_dates


def get_closest_dates_todelete(ticker):
    # Get the current date
    current_date = datetime.date.today()
    
    # Get the expiration dates from Robinhood
    options_chain = r.options.get_chains(ticker)
    expiration_dates = options_chain['expiration_dates']
    
    # Convert expiration dates to datetime.date objects
    expiration_dates = [datetime.datetime.strptime(date, '%Y-%m-%d').date() for date in expiration_dates]
    
    # Define target dates
    target_dates = {
        '1_week': current_date + datetime.timedelta(weeks=1),
        '1_month': current_date + datetime.timedelta(days=30),
        '1_year': current_date + datetime.timedelta(days=365)
    }
    
    # Function to find the closest date
    def find_closest_date(target_date):
        closest_date = min(expiration_dates, key=lambda x: abs(x - target_date), default=None)
        return closest_date if closest_date else None
    
    # Find the closest dates
    closest_dates = {key: find_closest_date(target_date) for key, target_date in target_dates.items()}
    
    return closest_dates


def get_option_data_todelete(ticker, expiration_date, strike_price, option_type):
    """
    Pull data for options at 5%, 10%, and 20% over/under the strike price.
    
    Parameters:
    ticker (str): The ticker symbol.
    expiration_date (str): The expiration date in 'YYYY-MM-DD' format.
    strike_price (float): The current strike price.
    option_type (str): 'call' or 'put'.
    
    Returns:
    dict: A dictionary with option data.
    """
    percentages = [0.05, 0.10, 0.20]
    option_data = {}
    
    for pct in percentages:
        if option_type == 'call':
            adjusted_strike = strike_price * (1 + pct)
        elif option_type == 'put':
            adjusted_strike = strike_price * (1 - pct)
        else:
            raise ValueError("option_type must be 'call' or 'put'")
        
        # Round the adjusted strike price to the nearest valid strike price
        adjusted_strike = round(adjusted_strike, 2)
        
        # Get option data
        options = r.options.find_options_by_expiration_and_strike(ticker, expiration_date, adjusted_strike, option_type)
        option_data[f'{int(pct*100)}%_{option_type}'] = options
    
    return option_data


def get_option_data(ticker, expiration_date, strike_price, option_type):
    """
    Pull data for options at 5%, 10%, and 20% over/under the strike price.
    If volume of the given option is 0, find another option by increasing the strike price by 5%,
    or increasing the price (for call) / decreasing the price (for put).
    
    Parameters:
    ticker (str): The ticker symbol.
    expiration_date (str): The expiration date in 'YYYY-MM-DD' format.
    strike_price (float): The current strike price.
    option_type (str): 'call' or 'put'.
    
    Returns:
    dict: A dictionary with option data.
    """
    percentages = [0.05, 0.10, 0.20]
    option_data = {}
    
    for pct in percentages:
        if option_type == 'call':
            adjusted_strike = strike_price * (1 + pct)
        elif option_type == 'put':
            adjusted_strike = strike_price * (1 - pct)
        else:
            raise ValueError("option_type must be 'call' or 'put'")
        
        # Round the adjusted strike price to the nearest multiple of 5
        adjusted_strike = round(adjusted_strike / 5) * 5
        
        while True:
            # Get option data
            print(f"Fetching options for {ticker}, expiration: {expiration_date}, strike: {adjusted_strike}, type: {option_type}")
            options = r.options.find_options_by_expiration_and_strike(ticker, expiration_date, adjusted_strike, option_type)
            time.sleep(2)  # Sleep for 2 seconds before each call
            
            if options:
                sample_response = options[0] if isinstance(options, list) and options else options
                print(f"Sample response: {sample_response}")
                
                # Check if volume is 0
                if all(opt['volume'] == 0 for opt in options if isinstance(opt, dict)):
                    # Adjust strike price by $5
                    if option_type == 'call':
                        adjusted_strike += 5  # Increase the price for call
                    elif option_type == 'put':
                        adjusted_strike -= 5  # Decrease the price for put
                    print(f"Volume is 0, adjusting strike price to {adjusted_strike}")
                else:
                    option_data[f'{int(pct*100)}%_{option_type}'] = options
                    break
            else:
                print("No options found, adjusting expiration date")
                expiration_date = (datetime.datetime.strptime(expiration_date, '%Y-%m-%d') + datetime.timedelta(days=7)).strftime('%Y-%m-%d')
        
    return option_data

def create_option_plotly(all_option_data):
    """
    Convert the nested Robinhood option data into a DataFrame, rank it,
    and return a Plotly figure along with the ranked DataFrame.
    Focus on 'mark_price', 'chance_of_profit_long', 'gamma', etc.
    """
    records = []
    for period, cp_data in all_option_data.items():
        for call_put, pct_data in cp_data.items():
            for pct_key, options_list in pct_data.items():
                for opt in options_list:
                    if not isinstance(opt, dict):
                        continue
                    record = {
                        'period': period,
                        'pct_key': pct_key,
                        'type': call_put,  # 'call' or 'put'
                        'mark_price': opt.get('mark_price'),
                        'chance_of_profit_long': opt.get('chance_of_profit_long'),
                        'chance_of_profit_short': opt.get('chance_of_profit_short'),
                        'delta': opt.get('delta'),
                        'gamma': opt.get('gamma'),
                        'implied_volatility': opt.get('implied_volatility'),
                        'rho': opt.get('rho'),
                        'theta': opt.get('theta'),
                        'vega': opt.get('vega'),
                        'symbol': opt.get('chain_symbol'),
                        'strike_price': opt.get('strike_price'),
                        'expiration_date': opt.get('expiration_date')
                    }
                    records.append(record)

    df = pd.DataFrame(records)

    # Drop rows with missing data you care about (optional)
    df.dropna(subset=['mark_price', 'chance_of_profit_long', 'gamma'], inplace=True)

    # Convert mark_price and other metrics to numeric
    df['mark_price'] = pd.to_numeric(df['mark_price'], errors='coerce')
    df['chance_of_profit_long'] = pd.to_numeric(df['chance_of_profit_long'], errors='coerce')
    df['chance_of_profit_short'] = pd.to_numeric(df['chance_of_profit_short'], errors='coerce')
    df['delta'] = pd.to_numeric(df['delta'], errors='coerce')
    df['gamma'] = pd.to_numeric(df['gamma'], errors='coerce')
    df['implied_volatility'] = pd.to_numeric(df['implied_volatility'], errors='coerce')
    df['rho'] = pd.to_numeric(df['rho'], errors='coerce')
    df['theta'] = pd.to_numeric(df['theta'], errors='coerce')
    df['vega'] = pd.to_numeric(df['vega'], errors='coerce')

    # Rank by chance_of_profit_long (descending)
    df['rank'] = df['chance_of_profit_long'].rank(ascending=False)
    df.sort_values('rank', inplace=True)

    # Create an interactive Plotly scatter plot
    x1 = 'chance_of_profit_short'
    y1 = 'chance_of_profit_long'
    fig = px.scatter(
        df,
        x=x1,
        y=y1,
        size='mark_price',
        color='type',
        hover_data=['period', 'pct_key', 'strike_price', 'expiration_date', 'chance_of_profit_short', 'delta', 'implied_volatility', 'rho', 'theta', 'vega'],
        title='chance_of_profit_short vs chance_of_profit_long'
    )
    fig.update_xaxes(title_text=x1) 
    fig.update_yaxes(title_text=y1)  

    x2 = 'delta'
    y2 = 'chance_of_profit_long'
    fig2 = px.scatter(
        df,
        x=x2,
        y=y2,
        size='mark_price',
        color='type',
        hover_data=['period', 'pct_key', 'strike_price', 'expiration_date', 'chance_of_profit_short', 'gamma', 'implied_volatility', 'rho', 'theta', 'vega'],
        title='Delta vs Chance of Profit Long'
    )
    fig2.update_xaxes(title_text=x2)
    fig2.update_yaxes(title_text=y2)

    x3 = 'theta'
    y3 = 'chance_of_profit_long'
    fig3 = px.scatter(
        df,
        x=x3,
        y=y3,
        size='mark_price',
        color='type',
        hover_data=['period', 'pct_key', 'strike_price', 'expiration_date', 'chance_of_profit_short', 'gamma', 'implied_volatility', 'rho', 'delta', 'vega'],
        title='Theta vs Chance of Profit Long'
    )
    fig3.update_xaxes(title_text=x3)
    fig3.update_yaxes(title_text=y3)

    x4 = 'strike_price'
    y4 = 'chance_of_profit_long'
    fig4 = px.scatter(
        df,
        x=x4,
        y=y4,
        size='mark_price',
        color='type',
        hover_data=['period', 'pct_key', 'strike_price', 'expiration_date', 'chance_of_profit_short', 'gamma', 'implied_volatility', 'rho', 'theta', 'delta'],
        title='strike_price vs Chance of Profit Long'
    )
    fig4.update_xaxes(title_text=x4)
    fig4.update_yaxes(title_text=y4)

    # Combine all subplots into one figure

    combined_fig = make_subplots(
        rows=2, cols=2,
        subplot_titles=(fig.layout.title.text, fig2.layout.title.text, fig3.layout.title.text, fig4.layout.title.text)
    )

    for trace in fig.data:
        combined_fig.add_trace(trace, row=1, col=1)
    for trace in fig2.data:
        combined_fig.add_trace(trace, row=1, col=2)
    for trace in fig3.data:
        combined_fig.add_trace(trace, row=2, col=1)
    for trace in fig4.data:
        combined_fig.add_trace(trace, row=2, col=2)

    combined_fig.update_layout(title_text="Option Comparison Subplots")

    #combined_fig.show()

    return combined_fig, df



def main(rh_username: str, rh_password: str, ticker: str, current_strike_price: float, cache_mode: str):
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

    # Check if the option history store directory exists, if not, create it
    if not os.path.exists(OPTION_HISTORY_STORE):
        os.makedirs(OPTION_HISTORY_STORE)

    # Define the file path for caching
    current_date_str = datetime.datetime.now().strftime('%Y%m%d')
    cache_file_path = os.path.join(OPTION_HISTORY_STORE, f"{ticker}_options_data_{str(current_strike_price)}_{current_date_str}.pkl")
    
    if cache_mode == 'local':
        if os.path.exists(cache_file_path):
            print("Loading option data from local cache...")
            with open(cache_file_path, 'rb') as f:
                option_bundle = pickle.load(f)
            return option_bundle
        else:
            print("No local cache found but local cache mode is on. Exiting...")
            print('Looked in directory ', cache_file_path)
            #exit(1)

    r.login(rh_username, rh_password)
    portfolio_data = r.profiles.load_portfolio_profile()
    print(portfolio_data)

    closest_dates = get_closest_dates(ticker)

    all_option_data = {}

    for period, date in closest_dates.items():
        if date:
            expiration_date = date.strftime('%Y-%m-%d')
            call_data = get_option_data(ticker, expiration_date, current_strike_price, 'call')
            put_data = get_option_data(ticker, expiration_date, current_strike_price, 'put')
            all_option_data[period] = {'call': call_data, 'put': put_data}


    print(all_option_data)
    (fig,df) = create_option_plotly(all_option_data)
    option_bundle = {'fig':fig, 'df':df}

    # Function to check if the cache file is older than 3 hours
    def is_cache_stale(file_path):
        if not os.path.exists(file_path):
            return True
        file_mod_time = datetime.datetime.fromtimestamp(os.path.getmtime(file_path))
        return (datetime.datetime.now() - file_mod_time).total_seconds() > 3 * 3600


    # Check cache mode
    if cache_mode == 'refresh' or is_cache_stale(cache_file_path):
        print("Refreshing option data...")
        # Save the DataFrame to the cache file
        with open(cache_file_path, 'wb') as f:
            pickle.dump(option_bundle, f)
    else:
        print("Loading option data from cache...")
        with open(cache_file_path, 'rb') as f:
            option_bundle = pickle.load(f)

    return option_bundle




if __name__ == "__main__":

    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Robinhood Portfolio Performance')
    parser.add_argument('--username', required=True, help='Robinhood account username')
    parser.add_argument('--password', required=True, help='Robinhood account password')
    parser.add_argument('--ticker', required=True, help='Robinhood account password')
    parser.add_argument('--target_price', required=True, type=float, help='Target strike price as a float')
    parser.add_argument('--force_refresh', action='store_true', help='Force refresh the option data')
    parser.add_argument('--force_local', action='store_true', help='Force use local cache')

    args = parser.parse_args()
    
    rh_username = args.username
    rh_password = args.password
    ticker = args.ticker
    current_strike_price = args.target_price
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

    print(main(rh_username, rh_password, ticker, current_strike_price, cache_mode))

