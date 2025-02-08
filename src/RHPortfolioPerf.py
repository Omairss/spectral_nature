import numpy as np
import pandas as pd
import os
import getpass
import argparse
import robin_stocks.robinhood as r

import datetime
import robin_stocks.robinhood as r

# Get the current working directory
current_directory = os.getcwd()
print(f"Current Working Directory: {current_directory}")

# Change the working directory
new_directory = '/mnt/batch/tasks/shared/LS_root/mounts/clusters/spectral-nature/code/Users/omai.r/spectral_nature/src/rh_perf/src'
os.chdir(new_directory)
print(f"Changed Working Directory to: {new_directory}")


DATA_STORE = '../../data'






# Parse command line arguments
parser = argparse.ArgumentParser(description='Robinhood Portfolio Performance')
parser.add_argument('--username', required=True, help='Robinhood account username')
parser.add_argument('--password', required=True, help='Robinhood account password')
args = parser.parse_args()

rh_username = args.username
rh_password = args.password

login_result = robinhood_login(rh_username, rh_password)
portfolio_data = r.profiles.load_portfolio_profile()
print(portfolio_data)

def robinhood_login(username, password):
    login_result = r.login(username, password)
    return login_result

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
    fig = px.scatter(
        df,
        x='gamma',
        y='chance_of_profit_long',
        size='mark_price',
        color='type',
        hover_data=['period', 'pct_key', 'strike_price', 'expiration_date', 'chance_of_profit_short', 'delta', 'implied_volatility', 'rho', 'theta', 'vega'],
        title='Option Comparison'
    )

    return fig, df



ticker = 'AAPL'
closest_dates = get_closest_dates(ticker)

# Assuming you have a way to get the current strike price, e.g., from the stock's current price
current_strike_price = 150.00  # Example strike price

all_option_data = {}

for period, date in closest_dates.items():
    if date:
        expiration_date = date.strftime('%Y-%m-%d')
        call_data = get_option_data(ticker, expiration_date, current_strike_price, 'call')
        put_data = get_option_data(ticker, expiration_date, current_strike_price, 'put')
        all_option_data[period] = {'call': call_data, 'put': put_data}



print(all_option_data)
fig, df = create_option_plotly(all_option_data)