import pandas as pd

import robin_stocks.robinhood as r

def login(username, password):
    r.login(username, password)

def get_portfolio_data():
    portfolio_data = r.profiles.load_account_profile()
    historical_data = r.profiles.get_historical_portfolio(span='5year')
    df = pd.DataFrame(historical_data['equity_historicals'])
    df['timestamp'] = pd.to_datetime(df['begins_at'])
    df.set_index('timestamp', inplace=True)
    df['net_portfolio_value'] = df['adjusted_close_equity']
    return df[['net_portfolio_value']]

def get_historical_data(symbol, interval='day', span='year'):
    return r.stocks.get_stock_historicals(symbol, interval=interval, span=span)